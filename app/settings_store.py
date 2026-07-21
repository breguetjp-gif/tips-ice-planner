"""UI設定の永続化。アプリ専用データ領域(app_data_dir)のJSONに保存する。
理由：アプリを **アップデート（.app差し替え）しても設定が消えない** ようにするため
（QSettings/cfprefsd はApp Translocation等で読み書きが不安定なことがあり、言語などが
既定=英語に戻る事象があった）。app_data_dir はユーザーの Application Support 配下で
バンドル外＝更新に影響されない。QSettings 互換の value()/setValue() を提供する。
"""
import os
import json
import threading

import catalog

_LOCK = threading.Lock()


def _path():
    # 都度 catalog.app_data_dir() を参照（テストの monkeypatch を尊重・キャッシュしない）
    return os.path.join(catalog.app_data_dir(), "ui_settings.json")


def _load():
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    with _LOCK:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=0)
        os.replace(tmp, _path())                       # アトミック置換


class Store:
    """QSettings と同じ使い勝手（value/setValue）で JSON バックエンド。"""

    def value(self, key, default=None, type=None):
        v = _load().get(key, default)
        if type is bool:
            if isinstance(v, bool):
                return v
            return str(v).strip().lower() in ("1", "true", "yes", "on")
        if type is int:
            try:
                return int(v)
            except Exception:
                return default
        return v

    def setValue(self, key, val):
        d = _load(); d[key] = val; _save(d)

    def remove(self, key):
        d = _load(); d.pop(key, None); _save(d)


_STORE = Store()


def store():
    return _STORE


# 既存 QSettings からの一回限りの移行（現在の言語などを引き継ぐ）
_MIGRATE_KEYS = [
    ("ui_lang", str), ("control_style", str),
    ("show_tips_on_startup", bool), ("next_tip_index", int),
    ("donation_status", str), ("donation_last_ack", str),
]


def migrate_from_qsettings():
    """JSONに未設定のキーだけ、旧QSettingsの値を取り込む（既存ユーザーの設定を維持）。"""
    d = _load()
    if d.get("_migrated"):
        return
    try:
        from PySide6.QtCore import QSettings
        qs = QSettings("Bonchan", "TIPS ICE Planner")
        for key, typ in _MIGRATE_KEYS:
            if key in d:
                continue
            raw = qs.value(key, None)
            if raw is None:
                continue
            if typ is bool:
                d[key] = str(raw).strip().lower() in ("1", "true", "yes", "on")
            elif typ is int:
                try:
                    d[key] = int(raw)
                except Exception:
                    pass
            else:
                d[key] = str(raw)
    except Exception:
        pass
    d["_migrated"] = True
    _save(d)
