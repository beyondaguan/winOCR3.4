# -*- coding: utf-8 -*-
"""验证 GLM 提供方支持任意 OpenAI 兼容 Base URL。"""
from __future__ import annotations

import sys
import urllib.request as urllib_request

sys.path.insert(0, "..")

import pytest

from winocr.services.ai.glm_chat import GlmChatProvider
from winocr.core.types import ChatMessage


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"choices":[{"message":{"content":"pong"}}]}'


@pytest.fixture
def captured(monkeypatch):
    box = {"url": "", "auth": ""}
    real_urlopen = urllib_request.urlopen

    def _fake(req, timeout=None):
        box["url"] = req.full_url
        box["auth"] = req.headers.get("Authorization", "")
        return _FakeResponse()

    monkeypatch.setattr(urllib_request, "urlopen", _fake)
    return box


def test_default_base_url(captured):
    p = GlmChatProvider()
    p.set_api_key("sk-test")
    p.chat(ChatMessage(text="ping"))
    assert captured["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert captured["auth"] == "Bearer sk-test"


def test_custom_base_url(captured):
    p = GlmChatProvider()
    p.set_api_key("sk-test")
    p.set_base_url("https://api.siliconflow.cn/v1/chat/completions")
    p.chat(ChatMessage(text="ping"))
    assert captured["url"] == "https://api.siliconflow.cn/v1/chat/completions"


def test_empty_base_url_keeps_default(captured):
    p = GlmChatProvider()
    p.set_api_key("sk-test")
    p.set_base_url("")
    p.chat(ChatMessage(text="ping"))
    assert captured["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
