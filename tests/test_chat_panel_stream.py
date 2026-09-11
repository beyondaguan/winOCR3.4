# -*- coding: utf-8 -*-
"""ChatPanel 流式打字机 after 句柄回收（3.4.29 修复）回归。

修复背景：面板被 reload_ui 重建/销毁后，在途的 _stream_tick after 任务
虽然会被 _alive() 守卫拦下，但悬空的 after id 从不清理、_stream_stop()
从未被调用。修复后守卫分支主动释放句柄；本文件验证该行为——
面板死亡后触发 _stream_tick，句柄必须被置空。

需要 Tk 显示环境（无头环境自动跳过）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import display_available

pytestmark = pytest.mark.skipif(not display_available(),
                                reason="无图形环境（需要 Tk）")


def _make_dead_panel(root):
    """绕过重量级 __init__，只装配 _stream_tick 守卫路径所需的属性，
    并让面板处于"已被销毁"状态（frame.winfo_exists() 为 False）。"""
    import tkinter as tk
    from tkinter import ttk
    from winocr.ui.tk.chat_panel import ChatPanel

    panel = ChatPanel.__new__(ChatPanel)
    panel.root = root
    panel.frame = ttk.Frame(root)
    panel._stream_widget = tk.Text(panel.frame)   # 在途打字机目标
    panel._stream_after = "999"                   # 伪造的 after 任务 id
    panel.frame.destroy()                         # 模拟 reload_ui 销毁面板
    return panel


def test_stream_tick_releases_handle_when_panel_dead():
    """面板销毁后 _stream_tick 应调 _stream_stop：句柄与控件引用双双置空。"""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        panel = _make_dead_panel(root)
        assert panel._alive() is False
        panel._stream_tick()                  # 守卫分支：释放悬空句柄
        assert panel._stream_after is None, "死亡面板应释放悬空 after id"
        assert panel._stream_widget is None, "死亡面板应清空打字机控件引用"
    finally:
        root.destroy()


def test_stream_stop_directly_clears_state():
    """_stream_stop 本身：取消 after（容忍无效 id）并清空控件引用。"""
    import tkinter as tk
    from tkinter import ttk
    from winocr.ui.tk.chat_panel import ChatPanel

    root = tk.Tk()
    root.withdraw()
    try:
        panel = ChatPanel.__new__(ChatPanel)
        panel.root = root
        panel.frame = ttk.Frame(root)
        panel._stream_widget = tk.Text(panel.frame)
        panel._stream_after = root.after(10, lambda: None)   # 真实 after id
        panel._stream_stop()
        assert panel._stream_after is None
        assert panel._stream_widget is None
        # 重复调用应幂等（clear_history 与守卫分支可能先后触发）
        panel._stream_stop()
        assert panel._stream_after is None
    finally:
        root.destroy()
