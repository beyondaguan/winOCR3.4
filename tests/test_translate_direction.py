# -*- coding: utf-8 -*-
"""翻译方向与译文区可编辑性的防回归测试。

钉住一个真实踩过的坑：中文原文点「译英」拿到英文后，再点「译中」毫无反应。
根因是 detect() 无条件把 target=zh-CN 纠正成 en，
于是第二次翻译又译成英文、结果与上次一字不差，看起来像按钮失灵。

不变量：
  1. 用户显式指定目标语言时，谁都不许改它；
  2. 未指定时才做「躲开同语言空转」的自动纠正；
  3. 原文已经是目标语种时，改翻译文区（回译），而不是原地空转；
  4. 译文区必须可编辑（机翻要人改）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from winocr.services.translate.dispatcher import TranslateDispatcher

ZH = "今天天气很好"
EN = "The weather is nice today"


class _Cfg:
    engine = "auto"
    target = "en"
    fallback_order = ["argos"]


def _disp():
    return TranslateDispatcher({}, _Cfg())


# ---------------------------------------------------------------- 方向判定
def test_explicit_target_is_never_rewritten():
    """显式指定 = 用户的明确意图，任何情况下都不许被程序改写。"""
    d = _disp()
    assert d.detect(ZH, "zh-CN", explicit=True) == ("zh-CN", "zh-CN")
    assert d.detect(EN, "en", explicit=True) == ("en", "en")
    assert d.detect(ZH, "en", explicit=True) == ("zh-CN", "en")
    assert d.detect(EN, "ja", explicit=True) == ("en", "ja")


def test_auto_target_avoids_same_language_spin():
    """未指定目标（热键/一键翻译）时才自动躲开「中文翻中文」。"""
    d = _disp()
    assert d.detect(ZH, "zh-CN") == ("zh-CN", "en")
    assert d.detect(EN, "en") == ("en", "zh-CN")
    assert d.detect(ZH, "en") == ("zh-CN", "en")


def test_is_same_language():
    d = _disp()
    assert d.is_same_language(ZH, "zh-CN") is True
    assert d.is_same_language(ZH, "zh") is True          # 归一化后仍成立
    assert d.is_same_language(EN, "en") is True
    assert d.is_same_language(ZH, "en") is False
    assert d.is_same_language(EN, "zh-CN") is False
    assert d.is_same_language("", "en") is False
    # 中英之外不下判断，一律允许翻译
    assert d.is_same_language(ZH, "ja") is False


def test_detect_signature_default_is_auto():
    """默认必须是自动模式，免得漏传参数把老调用点的行为改掉。"""
    d = _disp()
    assert d.detect(ZH, "zh-CN") == d.detect(ZH, "zh-CN", explicit=False)


# ---------------------------------------------------------------- GUI 契约（TK）
# 契约测试落到唯一的 TK 界面上。
# 这些用例现在真跑起来才有防回归价值。
try:
    import tkinter as _tk
    _root_probe = _tk.Tk()
    _root_probe.destroy()
    _HAS_DISPLAY = True
except Exception:
    _HAS_DISPLAY = False

pytestmark = pytest.mark.skipif(not _HAS_DISPLAY, reason="无图形显示，跳过 GUI 契约")


def _make_ui():
    """造一份离屏（withdraw）的真 TK 界面，跑完必须 destroy。"""
    from winocr.core import App
    from winocr.ui.tk.app import TkUi

    app = App().build()
    ui = TkUi()
    ui.bind(app)
    ui.root = _tk.Tk()
    ui.root.withdraw()                      # 不干扰跑测试的人
    ui._setup_style()
    from winocr.ui.tk.main_window import MainWindow
    ui.window = MainWindow(ui)
    ui._wire_events()
    return app, ui


def _close(ui):
    try:
        ui.root.destroy()
    except Exception:
        pass


def _sel(window, box, start="1.0", end="1.2"):
    """在 Text 控件里制造一段选区（模拟用户拖选）。"""
    box.tag_add("sel", start, end)
    box.focus_set()


def _capture_translate(ui):
    """mock run_async，返回 calls 列表；(args, kwargs) 逐次记录。"""
    calls = []
    ui.app.pipeline.run_async = lambda fn, *a, **kw: calls.append((a, kw))
    return calls


def test_translation_box_is_editable():
    """译文区必须能改 —— OCR 有错字、机翻有生硬处，得让人当场修。"""
    app, ui = _make_ui()
    try:
        box = ui.window.txt_translated
        assert str(box.cget("state")) == "normal", "译文区必须可编辑"
        box.insert("end", "手动补充")
        assert "手动补充" in ui.window.get_translation()

        from winocr.core.types import TranslateResult
        ui.window.show_translation(TranslateResult(text="hello", engine="argos",
                                                   target_lang="en"))
        assert str(box.cget("state")) == "normal", "刷新译文后仍要保持可编辑"
        assert ui.window.get_translation() == "hello"
    finally:
        _close(ui)


def test_selection_is_picked_up():
    """原文区选中文字应能被拾取（翻译时只翻选中段）。"""
    app, ui = _make_ui()
    try:
        ui.window.show_original("今天天气很好")
        assert ui.window.get_selected_text() == ""
        _sel(ui.window, ui.window.txt_original)
        assert ui.window.get_selected_text() == "今天"
    finally:
        _close(ui)


def test_explicit_zh_falls_back_to_translation_box():
    """中文原文 + 英文译文，点「译中」→ 必须去翻译文区那段英文（回译）。"""
    app, ui = _make_ui()
    calls = _capture_translate(ui)
    try:
        ui.window.show_original("今天天气很好")
        from winocr.core.types import TranslateResult
        ui.window.show_translation(TranslateResult(text="The weather is nice",
                                                   engine="argos", target_lang="en"))
        ui.do_translate("zh-CN")

        assert calls, "应当发起一次翻译，而不是静默无反应"
        args, kwargs = calls[-1]
        assert args[0] == "The weather is nice"      # 源取自译文区
        assert args[1] == "zh-CN"
        assert kwargs.get("explicit") is True
    finally:
        _close(ui)


def test_explicit_en_uses_original_box():
    """中文原文点「译英」→ 正常走原文区。"""
    app, ui = _make_ui()
    calls = _capture_translate(ui)
    try:
        ui.window.show_original("今天天气很好")
        ui.do_translate("en")
        args, kwargs = calls[-1]
        assert args[0] == "今天天气很好"
        assert args[1] == "en"
        assert kwargs.get("explicit") is True
    finally:
        _close(ui)


def test_nothing_translatable_reports_clearly():
    """两边都已经是目标语种时要说人话，不能静默 —— 静默就是「按钮坏了」。"""
    app, ui = _make_ui()
    calls = _capture_translate(ui)
    try:
        ui.window.show_original("今天天气很好")
        ui.do_translate("zh-CN")             # 译文区是空的
        assert not calls
        ui.root.update()                     # 冲掉事件总线回写
        assert "无需翻译" in ui.window.status_label.cget("text")
    finally:
        _close(ui)


def test_explicit_zh_never_picks_clipboard_when_original_is_chinese():
    """回归：中文原文点「译中」时，即便剪贴板里塞着英文，也绝不能误翻剪贴板，
    而应报「无需翻译」。

    早期 bug：else 分支里「回退剪贴板」的判断排在「原文已是目标语种」之前，
    剪贴板有非中文内容时会被悄悄翻译成「从剪贴板翻译」，按钮表现像失灵。
    """
    app, ui = _make_ui()
    calls = _capture_translate(ui)
    try:
        ui.root.clipboard_clear()
        ui.root.clipboard_append("The weather is nice today")   # 非中文
        ui.window.show_original("今天天气很好")     # 原文即中文 = 已是目标语种
        ui.do_translate("zh-CN")                  # 译文区为空
        assert not calls, "绝不应从剪贴板发起翻译"
        ui.root.update()
        txt = ui.window.status_label.cget("text")
        assert "无需翻译" in txt
        assert "剪贴板" not in txt
    finally:
        _close(ui)
