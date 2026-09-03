# -*- coding: utf-8 -*-
"""蒙版窗口拖拽（移动）与「随拖重译」开关回归测试。

验证 3.4.20 修复与开关：
- 在译文区 / 标题栏空白按下均可启动整窗拖拽；
- 拖动后 geometry 跟随更新（不再因 bindtags 冒泡规则卡死）；
- 点击标题栏按钮（✕ / ⟳ / 方向 / 随拖）不误触发拖拽；
- 「随拖」开关默认关 → 拖动结束不重译；开启 → 拖动结束自动重译。
"""
import tkinter as tk
from types import SimpleNamespace

import pytest

from winocr.ui.tk.mask_window import MaskWindow


class _PipelineStub:
    """记录 run_async 调用次数，并立即以 None 结果回调 on_done（模拟异步完成、清 busy）。"""
    def __init__(self):
        self.calls = 0
        self.services = {}
    def run_async(self, worker=None, on_done=None, on_error=None):
        self.calls += 1
        if on_done is not None:
            on_done(None)
    def translate(self, *a, **k):
        return SimpleNamespace(text="译文")


@pytest.fixture
def tk_env():
    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def _make_window(parent):
    pipeline = _PipelineStub()
    ui = SimpleNamespace(post=lambda fn: fn(), _mask_windows=[])
    return MaskWindow(parent, (100, 100, 300, 200), pipeline, ui)


def test_drag_moves_window(tk_env):
    w = _make_window(tk_env)
    # 在译文区按下（应触发整窗拖拽）
    w._on_drag_start(SimpleNamespace(x_root=150, y_root=120, widget=w._cv))
    assert w._drag_active is True
    assert w._drag_offset == (50, 20)
    # 拖到 (250, 200)
    w._on_drag_move(SimpleNamespace(x_root=250, y_root=200, widget=w._cv))
    assert w._bx == 200
    assert w._by == 180
    assert "+200+180" in w.root.geometry()
    # 松开结束拖拽
    w._on_drag_end(SimpleNamespace(widget=w._cv))
    assert w._drag_active is False


def test_drag_excludes_title_buttons(tk_env):
    w = _make_window(tk_env)
    # 点关闭按钮不应启动拖拽
    w._on_drag_start(SimpleNamespace(x_root=10, y_root=10, widget=w._close_btn))
    assert w._drag_active is False
    before = (w._bx, w._by)
    # 即便收到 move（理论上不会），也不应移动窗口
    w._on_drag_move(SimpleNamespace(x_root=999, y_root=999, widget=w._close_btn))
    assert (w._bx, w._by) == before


def test_drag_end_no_retranslate_by_default(tk_env):
    w = _make_window(tk_env)
    w._on_drag_start(SimpleNamespace(x_root=150, y_root=120, widget=w._cv))
    base = w._pipeline.calls                  # __init__ 已触发一次首次翻译
    w._on_drag_end(SimpleNamespace(widget=w._cv))
    assert w._pipeline.calls == base   # 默认关闭：拖动结束不重译


def test_drag_end_retranslate_when_enabled(tk_env):
    w = _make_window(tk_env)
    w._drag_retranslate = True
    w._on_drag_start(SimpleNamespace(x_root=150, y_root=120, widget=w._cv))
    base = w._pipeline.calls
    w._on_drag_end(SimpleNamespace(widget=w._cv))
    assert w._pipeline.calls == base + 1   # 开启：拖动结束自动重译一次


def test_drag_retranslate_toggle(tk_env):
    w = _make_window(tk_env)
    assert w._drag_retranslate is False
    w._toggle_drag_retranslate()
    assert w._drag_retranslate is True
    w._toggle_drag_retranslate()
    assert w._drag_retranslate is False
