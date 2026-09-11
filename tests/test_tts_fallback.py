# -*- coding: utf-8 -*-
"""TTS 降级链单测（蓝图 §4-B：补 `_work` 回退链缺口）。

裁决背景（2026-08-21）：SAPI5 保留为离线兜底，回退链 = edge(在线) → sapi(离线)。
本文件锁定 `_work` 的降级语义，防止未来维护者误删兜底或改错顺序：

  auto : edge 成功即停（不触 sapi）；edge 失败/异常 → 降级 sapi；sapi 也失败 → 提示失败
  edge : edge 失败 → 提示不可用，**不降级**（用户显式指定）
  sapi : 直接走 sapi，**不走 edge**（即使本机可探测到 edge）
  取消  : edge 失败后若已取消 → 直接返回，不触 sapi

注（2026-09-12）：3.4.4 起 `_work` 调用 `_speak_edge/_speak_sapi` 时会传
``on_progress=`` 关键字参数（朗读进度蒙版）。测试替身签名必须带
``on_progress=None``，否则替身自身抛 TypeError、被 `_work` 的异常兜底吞掉，
导致替身"从未被调用"的假象（本文件 5 个用例曾因此长期误报为环境问题）。
"""
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from winocr.services.tts import TtsService


def _make(engine="auto"):
    svc = TtsService()
    svc.cfg = SimpleNamespace(engine=engine)
    svc._has_edge = lambda: True      # 本机可探测到 edge-tts（让 auto/edge 分支可进入）
    return svc


def test_auto_edge_hit_stops_no_sapi():
    """auto：edge 成功即停，sapi 不被调用，last_engine=edge。"""
    svc = _make("auto")
    calls = {"sapi": 0}
    svc._speak_edge = lambda t, c, on_progress=None: True
    svc._speak_sapi = lambda t, c, on_progress=None: calls.__setitem__("sapi", calls["sapi"] + 1) or True
    msgs = []
    svc._work("你好，世界。", msgs.append, threading.Event())
    assert calls["sapi"] == 0
    assert svc.last_engine == "edge"
    assert "朗读完成" in msgs


def test_auto_edge_fail_falls_back_to_sapi():
    """auto：edge 返回 False → 自动降级 sapi，last_engine=sapi。"""
    svc = _make("auto")
    calls = {"edge": 0}
    svc._speak_edge = lambda t, c, on_progress=None: calls.__setitem__("edge", calls["edge"] + 1) or False
    svc._speak_sapi = lambda t, c, on_progress=None: True
    msgs = []
    svc._work("你好，世界。", msgs.append, threading.Event())
    assert calls["edge"] == 1
    assert svc.last_engine == "sapi"
    assert "在线语音不可用，改用系统语音…" in msgs
    assert "朗读完成（系统语音）" in msgs


def test_auto_edge_exception_falls_back_to_sapi():
    """auto：edge 抛异常（超时/被墙）→ except 吞掉 → 降级 sapi。"""
    svc = _make("auto")

    def boom(t, c, on_progress=None):
        raise RuntimeError("edge timeout")

    svc._speak_edge = boom
    svc._speak_sapi = lambda t, c, on_progress=None: True
    svc._work("你好。", None, threading.Event())
    assert svc.last_engine == "sapi"


def test_edge_mode_fail_no_sapi():
    """edge：显式指定 edge，失败 → 提示不可用，不降级 sapi。"""
    svc = _make("edge")
    calls = {"sapi": 0}
    svc._speak_edge = lambda t, c, on_progress=None: False
    svc._speak_sapi = lambda t, c, on_progress=None: calls.__setitem__("sapi", calls["sapi"] + 1) or True
    msgs = []
    svc._work("你好。", msgs.append, threading.Event())
    assert calls["sapi"] == 0
    assert svc.last_engine == ""
    assert "Edge 在线语音不可用（检查网络）" in msgs


def test_sapi_mode_skips_edge():
    """sapi：显式指定 sapi → 直接走 sapi，即使 _has_edge=True 也不进 edge 分支。"""
    svc = _make("sapi")
    calls = {"edge": 0}
    svc._speak_edge = lambda t, c, on_progress=None: calls.__setitem__("edge", calls["edge"] + 1) or True
    svc._speak_sapi = lambda t, c, on_progress=None: True
    msgs = []
    svc._work("你好。", msgs.append, threading.Event())
    assert calls["edge"] == 0
    assert svc.last_engine == "sapi"


def test_auto_all_fail_reports_failure():
    """auto：edge 与 sapi 都失败 → 提示朗读失败，last_engine 不变。"""
    svc = _make("auto")
    svc._speak_edge = lambda t, c, on_progress=None: False
    svc._speak_sapi = lambda t, c, on_progress=None: False
    msgs = []
    svc._work("你好。", msgs.append, threading.Event())
    assert svc.last_engine == ""
    assert "朗读失败：系统语音不可用" in msgs


def test_auto_cancel_during_fallback_skips_sapi():
    """auto：edge 失败期间用户取消 → 直接返回，不触 sapi。"""
    svc = _make("auto")
    cancel = threading.Event()

    def edge_with_cancel(t, c, on_progress=None):
        cancel.set()          # 模拟 edge 失败瞬间用户按了取消
        return False

    calls = {"sapi": 0}
    svc._speak_edge = edge_with_cancel
    svc._speak_sapi = lambda t, c, on_progress=None: calls.__setitem__("sapi", calls["sapi"] + 1) or True
    msgs = []
    svc._work("你好。", msgs.append, cancel)
    assert calls["sapi"] == 0
    assert svc.last_engine == ""
