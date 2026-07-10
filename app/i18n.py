"""UI言語（英語/日本語）の切り替え。

L(en, ja) が現在の言語の文字列を返すだけの極小モジュール。
描画時に呼べば paintEvent 系、setText 時に呼べばウィジェット系の両方で使える。
言語の保存(QSettings)は main 側で行う（ここは Qt 非依存に保つ）。
"""
_LANG = "en"


def lang():
    return _LANG


def set_lang(l):
    global _LANG
    _LANG = "ja" if str(l) == "ja" else "en"


def toggle():
    set_lang("ja" if _LANG == "en" else "en")
    return _LANG


def L(en, ja):
    return ja if _LANG == "ja" else en
