# -*- coding: utf-8 -*-
"""OpenAI 兼容接口能力（共享）。

任何走 chat/completions 的远程模型（AI 对话、云端视觉 OCR、大模型翻译）
都复用这里的客户端，避免「任意 OpenAI 兼容接口 + 429 退避 + 限流参数」
被重复实现三遍。
"""
from .client import (
    OpenAIClientConfig,
    OpenAICompatibleClient,
)

__all__ = ["OpenAIClientConfig", "OpenAICompatibleClient"]
