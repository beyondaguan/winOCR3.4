# -*- coding: utf-8 -*-
"""蒙版 ESC 关闭 / Ctrl+C 复制（鼠标悬停生效）回归测试。

设计约束：蒙版不夺焦点（键盘事件发给焦点窗口，Tk bind 收不到），快捷键靠
「鼠标悬停 + GetAsyncKeyState 轮询」实现——因此必须锁死：
- 悬停蒙版：ESC 关闭、Ctrl+C 复制"所见即所得"内容（译文）；
- 鼠标不在蒙版上：两个快捷键都不生效（不干扰其它软件）；
- 按住沿触发：只关一次 / 只复制一次。
"""
import tkinter as tk
from types import SimpleNamespace

from winocr.ui.tk import mask_window as mw_mod
from winocr.ui.tk.mask_window import MaskWindow


def _make_window():
    root = tk.Tk()
    root.withdraw()
    pipeline = SimpleNamespace(
        services={},
        run_async=lambda *a, **k: None,      # 只登记不执行：不走翻译链路
        translate=lambda *a, **k: SimpleNamespace(text=""),
    )
    ui = SimpleNamespace(_mask_windows=[], post=lambda fn: fn())
    w = MaskWindow(root, (100, 100, 300, 200), pipeline, ui)
    return w, root


def test_esc_closes_when_hover(monkeypatch):
    """鼠标悬停蒙版内按 ESC → 关闭。"""
    w, root = _make_window()
    monkeypatch.setattr(mw_mod, "_cursor_xy", lambda: (150, 150))
    monkeypatch.setattr(mw_mod, "_key_down", lambda vk: vk == mw_mod._VK_ESCAPE)
    w._poll_keys()
    assert not w._alive, "悬停蒙版按 ESC 应关闭"
    root.destroy()


def test_esc_ignored_when_not_hover(monkeypatch):
    """鼠标不在蒙版上按 ESC → 不关（快捷键只对本蒙版生效，不干扰其它软件）。"""
    w, root = _make_window()
    monkeypatch.setattr(mw_mod, "_cursor_xy", lambda: (-100, -100))
    monkeypatch.setattr(mw_mod, "_key_down", lambda vk: vk == mw_mod._VK_ESCAPE)
    w._poll_keys()
    assert w._alive, "鼠标不在蒙版上时 ESC 不应关闭"
    root.destroy()


def test_ctrl_c_copies_displayed_content(monkeypatch):
    """悬停蒙版按 Ctrl+C → 剪贴板收到蒙版显示内容（译文，所见即所得）。"""
    w, root = _make_window()
    w._last_translation = "译文内容ABC"
    monkeypatch.setattr(mw_mod, "_cursor_xy", lambda: (150, 150))
    monkeypatch.setattr(mw_mod, "_key_down",
                        lambda vk: vk in (mw_mod._VK_CONTROL, mw_mod._VK_C))
    w._poll_keys()
    got = root.clipboard_get()
    assert got == "译文内容ABC", f"Ctrl+C 应复制蒙版显示内容，得到 {got!r}"
    root.destroy()


def test_ctrl_c_falls_back_to_original_text(monkeypatch):
    """译文尚未到达（流式阶段）时 Ctrl+C → 复制 OCR 原文。"""
    w, root = _make_window()
    w._last_translation = ""          # 流式补偿阶段：显示的是 OCR 原文
    w._current_text = "original text"
    monkeypatch.setattr(mw_mod, "_cursor_xy", lambda: (150, 150))
    monkeypatch.setattr(mw_mod, "_key_down",
                        lambda vk: vk in (mw_mod._VK_CONTROL, mw_mod._VK_C))
    w._poll_keys()
    assert root.clipboard_get() == "original text"
    root.destroy()


def test_ctrl_c_held_triggers_once(monkeypatch):
    """按住 Ctrl+C 期间轮询多次 → 沿触发只复制一次。"""
    w, root = _make_window()
    w._last_translation = "内容X"
    calls = {"n": 0}
    orig_status = w._set_status

    def spy(text):
        if "已复制" in str(text):
            calls["n"] += 1
        orig_status(text)

    w._set_status = spy
    monkeypatch.setattr(mw_mod, "_cursor_xy", lambda: (150, 150))
    monkeypatch.setattr(mw_mod, "_key_down",
                        lambda vk: vk in (mw_mod._VK_CONTROL, mw_mod._VK_C))
    w._poll_keys()
    w._poll_keys()          # 仍按住：_copy_prev=True，不重复
    assert calls["n"] == 1, f"按住应只触发一次，实际 {calls['n']}"
    root.destroy()
