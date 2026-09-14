# -*- coding: utf-8 -*-
"""自动划词（services/capture/auto_select.py）+ SelectionConfig 单测。

覆盖：
  - GestureDetector 手势状态机：划选 / 单击不触发 / 位移阈值 / 双击窗口
  - AutoSelectionHook 三个快速闸门（开关 / busy / 自身窗口）+ 生命周期幂等
  - SelectionConfig 默认值 + 旧 ui.selection_auto 无感迁移

手势判定为纯逻辑（注入时钟），不需要真实鼠标钩子；钩子装钩路径依赖真实
Windows 消息泵，不在单测覆盖范围（由实机验证清单兜底）。
"""
import ctypes
import os
import sys

import pytest

from winocr.services.capture import auto_select as asel
from winocr.services.capture.auto_select import AutoSelectionHook, GestureDetector
from winocr.core.config import AppConfig, SelectionConfig


# ---------------------------------------------------------------------------
# GestureDetector：划选 / 双击手势
# ---------------------------------------------------------------------------
def _g(**kw):
    """构造注入固定时钟的探测器（t 序列按事件顺序消费）。"""
    times = kw.pop("times", None)
    if times is None:
        times = [0.0]
    it = iter(times)

    def now():
        try:
            return next(it)
        except StopIteration:
            return times[-1]

    return GestureDetector(now_fn=now, **kw)


def test_drag_gesture_fires():
    """按下→抬起位移 ≥ 阈值 → drag。"""
    g = _g()
    assert g.on_event(asel.WM_LBUTTONDOWN, 100, 100) is None
    assert g.on_event(asel.WM_LBUTTONUP, 160, 130) == "drag"


def test_plain_click_no_fire():
    """原地点按（位移 0）→ 不触发。"""
    g = _g()
    g.on_event(asel.WM_LBUTTONDOWN, 100, 100)
    assert g.on_event(asel.WM_LBUTTONUP, 100, 100) is None


def test_small_jitter_no_fire():
    """位移 < 10px 阈值（手抖）→ 不触发，防误翻译。"""
    g = _g()
    g.on_event(asel.WM_LBUTTONDOWN, 100, 100)
    assert g.on_event(asel.WM_LBUTTONUP, 107, 105) is None


def test_threshold_is_chebyshev():
    """阈值取 |dx|/|dy| 较大者：单轴大幅移动也命中。"""
    g = _g()
    g.on_event(asel.WM_LBUTTONDOWN, 100, 100)
    assert g.on_event(asel.WM_LBUTTONUP, 100, 140) == "drag"   # 仅纵向 40px


def test_mouse_move_and_right_button_ignored():
    """移动 / 右键等其它消息一律不触发、不破坏状态。"""
    g = _g()
    assert g.on_event(0x0200, 0, 0) is None                    # WM_MOUSEMOVE
    assert g.on_event(0x0204, 0, 0) is None                    # WM_RBUTTONDOWN
    # 状态未破坏：正常划选仍触发
    g.on_event(asel.WM_LBUTTONDOWN, 0, 0)
    assert g.on_event(asel.WM_LBUTTONUP, 50, 0) == "drag"


def test_double_click_within_system_window():
    """两次按下落在系统双击窗口内（时间 + 矩形）→ dblclick。"""
    g = _g(dbl_params_fn=lambda: (500, 4, 4), times=[0.0, 0.05, 0.15])
    g.on_event(asel.WM_LBUTTONDOWN, 100, 100)                  # 第一击
    g.on_event(asel.WM_LBUTTONUP, 101, 100)                    # 抬起
    assert g.on_event(asel.WM_LBUTTONDOWN, 102, 101) == "dblclick"


def test_double_click_too_far_no_fire():
    """第二次按下超出双击矩形（>4px）→ 普通按下。"""
    g = _g(dbl_params_fn=lambda: (500, 4, 4), times=[0.0, 0.05, 0.15])
    g.on_event(asel.WM_LBUTTONDOWN, 100, 100)
    g.on_event(asel.WM_LBUTTONUP, 100, 100)
    assert g.on_event(asel.WM_LBUTTONDOWN, 100, 110) is None


def test_double_click_too_late_no_fire():
    """第二次按下超出双击时间（>500ms）→ 普通按下。"""
    g = _g(dbl_params_fn=lambda: (500, 4, 4), times=[0.0, 0.05, 0.80])
    g.on_event(asel.WM_LBUTTONDOWN, 100, 100)
    g.on_event(asel.WM_LBUTTONUP, 100, 100)
    assert g.on_event(asel.WM_LBUTTONDOWN, 101, 100) is None


def test_drag_then_click_resets_state():
    """一次完整手势后状态被清掉：随后的第二次拖动照常判定（不粘连）。"""
    g = _g()
    g.on_event(asel.WM_LBUTTONDOWN, 0, 0)
    assert g.on_event(asel.WM_LBUTTONUP, 80, 0) == "drag"
    g.on_event(asel.WM_LBUTTONDOWN, 500, 500)
    assert g.on_event(asel.WM_LBUTTONUP, 500, 500) is None     # 第二次是点按


# ---------------------------------------------------------------------------
# AutoSelectionHook：闸门与生命周期（不装真实钩子）
# ---------------------------------------------------------------------------
def _hook(**kw):
    defaults = dict(
        on_gesture=lambda kind: None,
        enabled_fn=lambda: True,
        busy_fn=lambda: False,
        dblclick_fn=lambda: True,
        delay_ms_fn=lambda: 0,
        own_pid_fn=lambda: 0,          # 前台进程 ≠ 本进程
    )
    defaults.update(kw)
    return AutoSelectionHook(**defaults)


def test_should_fire_gates():
    """三个闸门逐一验证：未启用 / busy / 自身窗口 均不放行。"""
    h = _hook()
    h._hook = 1                                    # 模拟已装钩
    assert h._should_fire() is True

    h = _hook(enabled_fn=lambda: False)
    h._hook = 1
    assert h._should_fire() is False               # 总开关关

    h = _hook(busy_fn=lambda: True)
    h._hook = 1
    assert h._should_fire() is False               # 翻译在途

    h = _hook(own_pid_fn=lambda: os.getpid())
    h._hook = 1
    assert h._should_fire() is False               # 自身窗口内划选

    h = _hook()                                    # 未装钩（_hook=None）
    assert h._should_fire() is False


def test_hook_cb_enqueues_gesture():
    """钩子回调：手势命中且闸门放行 → 入队；闸门拦截 → 不入队。"""
    h = _hook()
    h._hook = 1
    info = asel._MSLLHOOKSTRUCT()
    info.pt.x, info.pt.y = 100, 100
    lparam = ctypes.addressof(info)
    down, up = asel.WM_LBUTTONDOWN, asel.WM_LBUTTONUP

    # 划选完成（按下+抬起位移≥阈值）→ 入队 1 个 "drag"
    h._hook_cb(0, down, lparam)
    info.pt.x, info.pt.y = 160, 100
    h._hook_cb(0, up, lparam)
    assert h._q.get_nowait() == "drag"

    # busy 时手势被闸门拦截 → 队列为空
    h._busy_fn = lambda: True
    h._hook_cb(0, down, lparam)
    info.pt.x, info.pt.y = 220, 100
    h._hook_cb(0, up, lparam)
    assert h._q.empty()


def test_hook_cb_always_calls_next_hook():
    """回调任何路径都必须 CallNextHookEx 放行（否则拖慢全系统鼠标）。"""
    called = []
    h = _hook()
    orig = asel.u32.CallNextHookEx
    asel.u32.CallNextHookEx = lambda *a: called.append(1) or 0
    try:
        h._hook_cb(0, 0x0204, 0)                   # 右键消息（无手势）
        assert called
    finally:
        asel.u32.CallNextHookEx = orig


def test_stop_without_start_is_noop():
    """未启动直接 stop()：幂等、不抛异常（退出清理路径的安全网）。"""
    h = _hook()
    h.stop()                                       # 不应抛
    assert not h.is_running()


def test_is_running_false_before_start():
    h = _hook()
    assert h.is_running() is False


# ---------------------------------------------------------------------------
# SelectionConfig：默认值 + 旧配置迁移
# ---------------------------------------------------------------------------
def test_selection_defaults():
    c = SelectionConfig()
    assert c.enabled is False                      # 默认关（防误触）
    assert c.dblclick is True
    assert c.delay_ms == 300


def test_selection_migration_from_legacy_ui_flag():
    """旧配置 ui.selection_auto=True 且无 [selection] 段 → 迁移为 enabled=True。"""
    cfg = AppConfig.from_dict({"ui": {"selection_auto": True}})
    assert cfg.selection.enabled is True


def test_selection_no_migration_when_explicit_section():
    """已有 [selection] 段时以新段为准，不再吃旧开关。"""
    cfg = AppConfig.from_dict({
        "ui": {"selection_auto": True},
        "selection": {"enabled": False, "delay_ms": 500, "dblclick": False},
    })
    assert cfg.selection.enabled is False
    assert cfg.selection.delay_ms == 500
    assert cfg.selection.dblclick is False


def test_selection_roundtrip_save_load(tmp_path):
    """selection 段参与配置落盘 / 回读（save → load 往返不丢字段）。"""
    cfg = AppConfig.from_dict({})
    cfg.selection.enabled = True
    cfg.selection.delay_ms = 450
    cfg.selection.dblclick = False
    p = tmp_path / "config.toml"
    cfg.save(str(p))
    cfg2 = AppConfig.load(str(p))
    assert cfg2.selection.enabled is True
    assert cfg2.selection.delay_ms == 450
    assert cfg2.selection.dblclick is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
