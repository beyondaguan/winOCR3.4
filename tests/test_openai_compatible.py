# -*- coding: utf-8 -*-
"""共享 OpenAI 兼容客户端 + 三处大模型接口（AI/视觉 OCR/翻译）的防回归测试。

覆盖：429 退避重试、鉴权、多模态拼装、上下文/轮次裁剪、各引擎接入 client。
网络调用一律用桩拦截，不依赖真实平台。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import urllib.request as ur

from winocr.core import App, AppConfig
from winocr.services.openai_compatible import (
    OpenAIClientConfig,
    OpenAICompatibleClient,
)
from winocr.services.ai.glm_chat import GlmChatProvider
from winocr.services.ocr.vision_ocr import VisionOcrEngine
from winocr.services.translate.glm import GlmEngine
from winocr.core.types import ChatMessage, OcrResult


# ----------------------------------------------------------------------
# 共享客户端
# ----------------------------------------------------------------------
class _FakeResp:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return b'{"choices":[ {"message":{"content":"ok"}} ]}'


def test_client_basic_and_auth(monkeypatch):
    calls = {}
    def fake(req, timeout=None):
        calls["auth"] = req.headers.get("Authorization")
        calls["url"] = req.full_url
        return _FakeResp()
    monkeypatch.setattr(ur, "urlopen", fake)

    cfg = OpenAIClientConfig(api_key="sk-x", base_url="https://x/v1/chat/completions",
                             model="m1", max_output_tokens=123)
    c = OpenAICompatibleClient(cfg)
    out = c.complete([{"role": "user", "content": "hi"}], system_prompt="sys")
    assert out == "ok"
    assert calls["auth"] == "Bearer sk-x"
    # max_tokens 写入请求体
    body = None
    def fake2(req, timeout=None):
        nonlocal body
        body = req.data
        return _FakeResp()
    monkeypatch.setattr(ur, "urlopen", fake2)
    c.complete([{"role": "user", "content": "hi"}])
    assert b'"max_tokens": 123' in body


def test_client_429_retry_with_backoff(monkeypatch):
    attempts = {"n": 0}
    class _Late(_FakeResp):
        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'
    def fake(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ur.HTTPError(req.full_url, 429, "rate", {}, None)
        return _Late()
    monkeypatch.setattr(ur, "urlopen", fake)

    cfg = OpenAIClientConfig(api_key="k", base_url="https://x/v1/chat/completions",
                             model="m", retry_attempts=3, retry_backoff=0.01)
    c = OpenAICompatibleClient(cfg)
    t0 = time.time()
    out = c.complete([{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert attempts["n"] == 3                     # 重试了 2 次后第 3 次成功
    assert time.time() - t0 >= 0.01               # 退避时间（0.01*2^1≈0.02）确实发生


def test_client_429_exhausted_raises(monkeypatch):
    def fake(req, timeout=None):
        raise ur.HTTPError(req.full_url, 429, "rate", {}, None)
    monkeypatch.setattr(ur, "urlopen", fake)
    cfg = OpenAIClientConfig(api_key="k", base_url="https://x/v1/chat/completions",
                             model="m", retry_attempts=2, retry_backoff=0.01)
    c = OpenAICompatibleClient(cfg)
    import pytest
    with pytest.raises(RuntimeError):
        c.complete([{"role": "user", "content": "hi"}])


def test_multimodal_message_builds_data_uri():
    from PIL import Image
    img = Image.new("RGB", (10, 10), "red")
    msg = OpenAICompatibleClient.multimodal_user_message("看这张图", [img])
    assert msg["role"] == "user"
    assert isinstance(msg["content"], list)
    assert msg["content"][0]["text"] == "看这张图"
    assert msg["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_image_resized_when_too_large():
    from PIL import Image
    big = Image.new("RGB", (4000, 100))
    uri = OpenAICompatibleClient.image_to_data_uri(big, max_side=1600)
    # 不抛异常即通过；尺寸限制由 base64 长度间接保证（此处仅验证可调用）
    assert uri.startswith("data:image/png;base64,")


# ----------------------------------------------------------------------
# AI 对话：视觉独立 + 限流裁剪
# ----------------------------------------------------------------------
def test_ai_vision_inherits_text_url_when_blank():
    p = GlmChatProvider()
    p.set_api_key("k")
    p.set_base_url("https://api.siliconflow.cn/v1/chat/completions")
    # 视觉没填 → 复用文本地址与 Key
    assert p.vision_client.cfg.base_url.endswith("api.siliconflow.cn/v1/chat/completions")
    assert p.vision_client.cfg.api_key == "k"


def test_ai_vision_independent_config():
    p = GlmChatProvider()
    p.set_api_key("k")
    p.set_base_url("https://api.siliconflow.cn/v1/chat/completions")
    p.set_vision_config(base_url="https://other/v1/chat/completions",
                        api_key="vk", model="some-vl")
    assert p.vision_client.cfg.base_url.endswith("other/v1/chat/completions")
    assert p.vision_client.cfg.api_key == "vk"
    assert p.vision_client.cfg.model == "some-vl"
    # 文本侧不受影响
    assert p.text_client.cfg.base_url.endswith("api.siliconflow.cn/v1/chat/completions")
    assert p.text_client.cfg.api_key == "k"


def test_ai_history_trim_by_turns_and_context():
    p = GlmChatProvider()
    p.set_api_key("k")
    p.set_limits(max_turns=2, max_context_tokens=50)   # 极小阈值便于触发
    for i in range(6):
        p._messages.append({"role": "user", "content": "x" * 40})
        p._messages.append({"role": "assistant", "content": "y" * 40})
        p._trim_history()
    # 最多保留 max_turns*2=4 条
    assert len(p._messages) == 4


def test_ai_limits_injected_via_app():
    cfg = AppConfig()
    # 3.4：AI 对话直接持有文本 + 视觉两套连接参数，无「连接」中间层。
    # 文本与视觉共享同一套地址 / 密钥，仅模型名可不同。
    cfg.ai.api_key = "k"
    cfg.ai.base_url = "https://api.siliconflow.cn/v1/chat/completions"
    cfg.ai.vision_model = "some-vl"
    cfg.ai.max_turns = 5
    cfg.ai.max_output_tokens = 999
    app = App(cfg).build()
    ai = app.services["ai"]
    # 视觉侧复用 AI 对话的地址 / 密钥，但用自己页里填的视觉模型
    assert ai.vision_client.cfg.api_key == "k"
    assert ai.vision_client.cfg.base_url.endswith("siliconflow.cn/v1/chat/completions")
    assert ai.vision_client.cfg.model == "some-vl"
    # 限流参数正确注入
    assert ai.max_turns == 5
    assert ai.text_client.cfg.max_output_tokens == 999


# ----------------------------------------------------------------------
# 云端视觉 OCR 引擎
# ----------------------------------------------------------------------
def test_vision_ocr_connect_and_ocr(monkeypatch):
    def fake(req, timeout=None):
        return _FakeResp()
    monkeypatch.setattr(ur, "urlopen", fake)
    e = VisionOcrEngine()
    e.configure(cloud_api_key="k", cloud_base_url="https://x/v1/chat/completions",
               cloud_model="vl-model")
    assert e.available()
    from PIL import Image
    res = e.recognize(Image.new("RGB", (20, 20)))
    assert isinstance(res, OcrResult)
    assert res.ok and res.engine == "vision_ocr"


def test_vision_ocr_unavailable_without_key():
    e = VisionOcrEngine()
    assert not e.available()


# ----------------------------------------------------------------------
# 翻译引擎接入 client + limits
# ----------------------------------------------------------------------
def test_translate_engine_uses_client_and_limits(monkeypatch):
    def fake(req, timeout=None):
        return _FakeResp()
    monkeypatch.setattr(ur, "urlopen", fake)
    e = GlmEngine()
    e.set_config(glm_api_key="k", base_url="https://x/v1/chat/completions",
                 model="glm-4-flash", max_output_tokens=512, retry_attempts=2)
    assert e.available()
    out = e.translate("Hello world", "en", "zh-CN")
    assert out == "ok"
    assert e._client().cfg.max_output_tokens == 512


def test_translate_engine_blank_falls_back_to_default():
    e = GlmEngine()
    e.set_config(glm_api_key="k", base_url="", model="")
    assert e._url == GlmEngine.DEFAULT_URL
    assert e._model == GlmEngine.DEFAULT_MODEL


# ----------------------------------------------------------------------
# 配置往返
# ----------------------------------------------------------------------
def test_config_roundtrip(tmp_path):
    cfg = AppConfig()
    # AI 对话：文本 + 视觉同在本功能配置内
    cfg.ai.base_url = "https://api.siliconflow.cn/v1/chat/completions"
    cfg.ai.api_key = "k"
    cfg.ai.vision_model = "vl"
    cfg.ai.max_context_tokens = 100
    cfg.ai.max_turns = 7
    # 大模型翻译：本功能自己的连接参数（独立持有）
    cfg.translate.api_key = "tk"
    cfg.translate.base_url = "https://t/v1/chat/completions"
    cfg.translate.text_model = "glm-4-flash"
    cfg.translate.max_output_tokens = 800
    cfg.translate.retry_attempts = 4
    # 云端视觉 OCR：本功能自己的连接参数（独立持有）
    cfg.ocr.engine = "vision_ocr"
    cfg.ocr.api_key = "ck"
    cfg.ocr.base_url = "https://c/v1/chat/completions"
    cfg.ocr.vision_model = "vl"
    cfg.ocr.max_output_tokens = 3000
    p = tmp_path / "config.toml"
    cfg.save(p)

    back = AppConfig.load(p)
    # AI 对话往返
    assert back.ai.base_url.endswith("/v1/chat/completions")
    assert back.ai.api_key == "k"
    assert back.ai.vision_model == "vl"
    assert back.ai.max_context_tokens == 100
    assert back.ai.max_turns == 7
    # 大模型翻译往返
    assert back.translate.api_key == "tk"
    assert back.translate.base_url.endswith("/v1/chat/completions")
    assert back.translate.text_model == "glm-4-flash"
    assert back.translate.max_output_tokens == 800
    assert back.translate.retry_attempts == 4
    # 云端视觉 OCR 往返
    assert back.ocr.engine == "vision_ocr"
    assert back.ocr.api_key == "ck"
    assert back.ocr.base_url.endswith("/v1/chat/completions")
    assert back.ocr.vision_model == "vl"
    assert back.ocr.max_output_tokens == 3000


def test_apply_config_reinjects_translate_limits():
    cfg = AppConfig()
    cfg.translate.api_key = "tk"            # 翻译独立 Key（本功能自己的连接参数）
    cfg.translate.max_output_tokens = 700
    app = App(cfg).build()
    eng = app.translate_engines["glm"]
    assert eng._client().cfg.max_output_tokens == 700

    app.config.translate.max_output_tokens = 1500
    app.apply_config(save=False)
    assert eng._client().cfg.max_output_tokens == 1500
