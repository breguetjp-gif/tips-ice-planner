"""重いI/O（DICOM走査・シリーズ読込）をUIスレッドから外すための小さなバックグラウンド実行。

UIが固まらないよう、処理を QThread で走らせ、進捗を QProgressDialog に出す。
fn は `fn(progress)` の形（progress(i, n) を呼ぶと進捗バーが進む。無視してもよい）。
"""
from __future__ import annotations
import traceback
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QProgressDialog, QMessageBox


class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

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
