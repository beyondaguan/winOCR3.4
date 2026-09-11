# -*- coding: utf-8 -*-
"""GlmChatProvider 对话历史锁（3.4.29 修复）回归。

修复背景：chat() 对 _messages 的快照/回写、get_history()/clear_history()
原先无锁，后台 chat 线程与 UI 线程并发访问同一列表。修复后由 _chat_lock
互斥；本文件验证锁语义确实生效——持锁期间相关操作必须阻塞，而不是
悄悄并发（GIL 之下裸列表读多数时候"碰巧不炸"，锁是行为契约）。

全部封闭：客户端被替身，无网络；历史文件落在 conftest 隔离的
WINOCR_HOME 临时目录。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from winocr.core.types import ChatMessage
from winocr.services.ai.glm_chat import GlmChatProvider

_CANNED_REPLY = "好的，这是替身回复。"


def _make_provider():
    """带替身客户端的 provider：complete 立即返回固定回复，记录调用。"""
    p = GlmChatProvider()
    p.set_api_key("test-key")
    calls = {"n": 0}

    def fake_complete(messages, **kwargs):
        calls["n"] += 1
        return _CANNED_REPLY

    p.text_client.complete = fake_complete   # 实例级替身，零网络
    return p, calls


def test_chat_snapshot_waits_for_lock():
    """持锁期间 chat() 必须阻塞在历史快照（网络请求前），锁放行才继续。"""
    p, calls = _make_provider()
    p._chat_lock.acquire()
    result = {}
    t = threading.Thread(
        target=lambda: result.setdefault("reply", p.chat(ChatMessage(text="hi"))))
    t.start()
    time.sleep(0.25)
    assert "reply" not in result, "锁被持有期间 chat() 不应完成历史快照"
    assert calls["n"] == 0, "锁被持有期间不应发出网络请求（快照在请求之前）"
    p._chat_lock.release()
    t.join(timeout=5)
    assert not t.is_alive(), "放锁后 chat() 应在超时内完成"
    assert result.get("reply") == _CANNED_REPLY
    assert calls["n"] == 1


def test_get_history_waits_for_lock():
    """持锁期间 get_history() 阻塞；放锁后返回一致快照。"""
    p, _ = _make_provider()
    p._chat_lock.acquire()
    got = {}
    t = threading.Thread(target=lambda: got.setdefault("h", p.get_history()))
    t.start()
    time.sleep(0.25)
    assert "h" not in got, "锁被持有期间 get_history() 不应返回"
    p._chat_lock.release()
    t.join(timeout=5)
    assert got.get("h") == [], "放锁后 get_history() 应返回当前历史（空）"


def test_chat_appends_pair_and_history_is_copy():
    """每轮 chat 追加 user+assistant 两条；get_history 返回副本，
    改返回值不影响内部状态；超限后裁剪保持偶数长度。"""
    p, _ = _make_provider()
    for _ in range(30):                      # 超过 max_turns*2=24，触发裁剪
        p.chat(ChatMessage(text="q"))
    h = p.get_history()
    assert len(h) <= p.max_turns * 2, "裁剪上限失效"
    assert len(h) % 2 == 0, "历史应保持 user/assistant 成对（回写原子性）"
    before = len(p.get_history())
    h.clear()                                # 副本语义：清掉返回值不影响内部
    assert len(p.get_history()) == before, "get_history 应返回副本而非内部列表引用"
