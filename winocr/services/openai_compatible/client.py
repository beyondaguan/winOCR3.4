# -*- coding: utf-8 -*-
"""OpenAI 兼容 Chat Completions 客户端（仅标准库，零额外依赖）。

被三处复用：AI 对话（GLM）、云端视觉 OCR、大模型翻译。
统一解决：鉴权、多模态消息拼装、图片尺寸限制、429 退避重试、
可配置超时 / max_tokens / 重试次数。

「任意 OpenAI 兼容接口」的能力只实现一次，翻译 / 视觉 / OCR 各自只填参数。
"""
from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

DEFAULT_TIMEOUT = 60
DEFAULT_MAX_OUTPUT = 2048
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_BACKOFF = 1.5          # 秒，指数退避基数
DEFAULT_MAX_IMAGE_SIDE = 1600  # 超过则缩图：省流量，也避开服务端尺寸上限


@dataclass
class OpenAIClientConfig:
    api_key: str = ""
    base_url: str = ""                       # 完整 chat/completions 地址
    model: str = ""
    timeout: int = DEFAULT_TIMEOUT
    max_output_tokens: int = DEFAULT_MAX_OUTPUT
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS
    retry_backoff: float = DEFAULT_BACKOFF
    max_image_side: int = DEFAULT_MAX_IMAGE_SIDE

    def effective_url(self, default: str = "") -> str:
        return (self.base_url or "").strip() or default


class OpenAICompatibleClient:
    """对一次 chat/completions 请求的薄封装，内置 429 退避与超时/重试。"""

    def __init__(self, config: OpenAIClientConfig):
        self.cfg = config

    # ------------------------------------------------------------------
    # 图片 -> data URI（自动限尺寸）
    # ------------------------------------------------------------------
    @staticmethod
    def image_to_data_uri(image, max_side: int = DEFAULT_MAX_IMAGE_SIDE) -> str:
        from PIL import Image
        if image.mode != "RGB":
            image = image.convert("RGB")
        w, h = image.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return "data:image/png;base64," + b64

    @staticmethod
    def multimodal_user_message(text: str, images: list) -> dict:
        """把文本 + 若干 PIL 图拼成多模态 user 消息。"""
        content = [{"type": "text", "text": text or "请描述这张图片"}]
        for img in images:
            if img is None:
                continue
            content.append({
                "type": "image_url",
                "image_url": {"url": OpenAICompatibleClient.image_to_data_uri(img)},
            })
        return {"role": "user", "content": content}

    # ------------------------------------------------------------------
    # 一次完整请求
    # ------------------------------------------------------------------
    def complete(self, messages: List[dict],
                 system_prompt: Optional[str] = None,
                 temperature: Optional[float] = None,
                 top_p: Optional[float] = None) -> str:
        """发起一次 chat/completions 请求。

        ``temperature`` / ``top_p`` 为可选：传 ``None`` 则不写入请求体，
        让服务端用默认采样策略。这对推理类视觉模型（如 GLM-4.1V-9B-Thinking）
        至关重要——它们常拒绝 temperature，写死 0.7 会直接 400。
        ``max_tokens`` 始终发送（服务端有合理默认值，缺失反而易报错）。
        """
        body_messages: List[dict] = []
        if system_prompt:
            body_messages.append({"role": "system", "content": system_prompt})
        body_messages.extend(messages)
        body: dict = {
            "model": self.cfg.model,
            "messages": body_messages,
            "max_tokens": self.cfg.max_output_tokens,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if top_p is not None:
            body["top_p"] = top_p
        return self._post(json.dumps(body).encode("utf-8"))

    def _post(self, body: bytes) -> str:
        url = self.cfg.effective_url()
        if not url:
            raise RuntimeError("Base URL 未配置")
        if not self.cfg.api_key:
            raise RuntimeError("API Key 未配置")

        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        })

        last_err: Optional[Exception] = None
        for attempt in range(self.cfg.retry_attempts):
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return _extract_text(data)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")
                if e.code == 429 and attempt < self.cfg.retry_attempts - 1:
                    wait = self.cfg.retry_backoff * (2 ** attempt)
                    time.sleep(wait)
                    last_err = RuntimeError(
                        f"请求被限流(429)，{wait:.1f}s 后重试")
                    continue
                raise RuntimeError(
                    f"API 错误 {e.code}: {detail[:300]}") from None
            except OSError as e:
                # urlopen 超时抛的是 socket.timeout（== TimeoutError），并不是
                # URLError —— 以前只捕 URLError，导致「超时自动重试」从未生效。
                # URLError / TimeoutError / ConnectionError 全是 OSError 子类，
                # 统一在此按网络类故障退避重试。
                reason = getattr(e, "reason", e)
                if attempt < self.cfg.retry_attempts - 1:
                    time.sleep(self.cfg.retry_backoff * (2 ** attempt))
                    last_err = RuntimeError(f"网络不可达: {reason}")
                    continue
                raise RuntimeError(f"网络不可达: {reason}") from None
        raise last_err or RuntimeError("请求失败（未知原因）")


def _extract_text(data: dict) -> str:
    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError, TypeError):
        raise RuntimeError(f"响应格式异常: {str(data)[:200]}")
    if not text:
        raise RuntimeError("模型返回空内容")
    return text
