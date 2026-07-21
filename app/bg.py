"""重いI/O（DICOM走査・シリーズ読込）をUIスレッドから外すための小さなバックグラウンド実行。

UIが固まらないよう、処理を QThread で走らせ、進捗を QProgressDialog に出す。
fn は `fn(progress)` の形（progress(i, n) を呼ぶと進捗バーが進む。無視してもよい）。
"""
from __future__ import annotations
import atexit
import traceback
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QProgressDialog, QMessageBox

_live_workers: list["Worker"] = []


def _wait_workers_at_exit():
    """インタープリタ終了時、走行中の Worker の完了を待つ。

    app.exec() を通らない終了（テスト・ヘッドレススクリプト等）では aboutToQuit も
    closeEvent も呼ばれず、走行中の QThread が PySide の終了処理
    (destroyQCoreApplication) で破棄されて qFatal→abort() する
    （2026-07-14 に肝抽出 Worker 走行中のプロセス終了で実際に SIGABRT）。
    atexit は後から登録した方が先に走る(LIFO)ので、PySide の終了処理より先に
    ここでワーカーを待てる。"""
    for w in list(_live_workers):
        try:
            if w.isRunning():
                w.requestInterruption()
                w.wait(10000)
        except RuntimeError:                                # 既に C++ 側破棄済み等は無視
            pass


atexit.register(_wait_workers_at_exit)


class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        _live_workers.append(self)                          # 終了時待ち対象（atexit 用）
        self.finished.connect(
            lambda: _live_workers.remove(self) if self in _live_workers else None)

    def run(self):
        try:
            res = self._fn(lambda i, n: self.progress.emit(int(i), int(n)))
            self.done.emit(res)
        except Exception as e:                                   # noqa
            self.failed.emit(f"{e}\n{traceback.format_exc()}")


def run_with_progress(parent, label, fn, on_done, on_fail=None):
    """fn をバックグラウンドで実行。完了で on_done(result)、失敗で on_fail(msg)。"""
    dlg = QProgressDialog(label, "Cancel", 0, 0, parent)        # 0,0=ビジー表示（進捗が来たら確定表示に）
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setValue(0)

    w = Worker(fn)

    def prog(i, n):
        if n > 0:
            dlg.setMaximum(n)
            dlg.setValue(min(i, n))
    w.progress.connect(prog)

    def finish_ok(res):
        dlg.close()
        try:
            on_done(res)
        finally:
            w.deleteLater()

    def finish_err(msg):
        dlg.close()
        if on_fail:
            on_fail(msg)
        else:
            QMessageBox.warning(parent, "Error", msg)
        w.deleteLater()

    w.done.connect(finish_ok)
    w.failed.connect(finish_err)
    dlg.canceled.connect(w.requestInterruption)                 # 協調キャンセル（ベストエフォート）
    # GC されないよう参照を親に保持
    if not hasattr(parent, "_bg_workers"):
        parent._bg_workers = []
    parent._bg_workers.append(w)
    w.finished.connect(lambda: parent._bg_workers.remove(w) if w in parent._bg_workers else None)
    w.start()
    return w
