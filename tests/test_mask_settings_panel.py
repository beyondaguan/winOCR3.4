# -*- coding: utf-8 -*-
"""蒙版外设置小面板（遮蔽/字号滑条）回归测试。

契约：
- 面板随蒙版创建（独立 Toplevel，初始可见）；
- 遮蔽滑条 → 即时改整窗 -alpha；
- 字号滑条 → 更新 _font_scale 并触发重渲染（不重新 OCR/翻译）；
- ⚙ 开关 → 面板 withdraw/deiconify；
- close → 面板一并销毁。
"""
import tkinter as tk
from types import SimpleNamespace

from winocr.ui.tk.mask_window import MaskWindow


def _make_window():
    root = tk.Tk()
    root.withdraw()
    pipeline = SimpleNamespace(
        services={},
        run_async=lambda *a, **k: None,
        translate=lambda *a, **k: SimpleNamespace(text=""),
    )
    ui = SimpleNamespace(_mask_windows=[], post=lambda fn: fn())
    w = MaskWindow(root, (100, 100, 400, 300), pipeline, ui)
    return w, root


def test_panel_created_and_visible():
    w, root = _make_window()
    assert w._panel is not None and w._panel.winfo_exists()
    assert w._panel_visible, "设置面板初始应可见"
    root.destroy()


def test_alpha_slider_updates_window_alpha():
    w, root = _make_window()
    w._set_alpha(0.8)
    got = float(w.root.attributes("-alpha"))   # 蒙版 Toplevel 的 alpha
    assert abs(got - 0.8) < 0.001, f"遮蔽滑条应改整窗 alpha，得到 {got}"
    w._set_alpha(0.45)
    assert abs(float(w.root.attributes("-alpha")) - 0.45) < 0.001
    root.destroy()


def test_font_scale_rerenders_without_translate():
    w, root = _make_window()
    calls = {"n": 0}
    rec = SimpleNamespace(text="x", lines=None, line_boxes=None)

    def fake_render(ocr, translation):
        calls["n"] += 1

    w._renderer.render = fake_render
    w._last_ocr = rec
    w._set_font_scale(1.2)
    assert w._font_scale == 1.2
    assert calls["n"] == 1, "字号滑条应触发一次即时重渲染"
    root.destroy()


def test_font_scale_no_crash_without_last_ocr():
    """尚无 OCR 结果时拖字号滑条不应抛异常。"""
    w, root = _make_window()
    w._last_ocr = None
    w._set_font_scale(0.8)      # 不应崩
    assert w._font_scale == 0.8
    root.destroy()


def test_toggle_panel_show_hide():
    w, root = _make_window()
    w._toggle_settings_panel()
    assert not w._panel_visible, "第一次切应隐藏面板"
    w._toggle_settings_panel()
    assert w._panel_visible, "第二次切应重新显示面板"
    root.destroy()


def test_close_destroys_panel():
    w, root = _make_window()
    p = w._panel
    w.close()
    assert not p.winfo_exists(), "close 应一并销毁设置面板"
    root.destroy()


def test_engine_label_reports_online_vs_argos():
    """面板「引擎」行：在线引擎(glm)→蓝；argos 离线兜底→红(提示差译文来源)。"""
    from winocr.ui.tk import theme
    w, root = _make_window()
    assert w._engine_label is not None
    w._report_engine("glm")
    assert w._engine_label.cget("text") == "glm"
    assert w._engine_label.cget("fg") == theme.ACCENT, "在线引擎应蓝字"
    w._report_engine("argos")
    assert w._engine_label.cget("text") == "argos"
    assert w._engine_label.cget("fg") == theme.DANGER, "argos 应红字提示离线兜底"
    root.destroy()


def test_engine_label_reports_failure():
    from winocr.ui.tk import theme
    w, root = _make_window()
    w._report_engine("(全部失败)")
    assert "(全部失败)" in w._engine_label.cget("text")
    assert w._engine_label.cget("fg") == theme.DANGER
    root.destroy()
