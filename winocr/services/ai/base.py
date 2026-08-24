# -*- coding: utf-8 -*-
"""AI 对话提供方契约（插件轴）。

未来接 OpenAI / DeepSeek / 本地 llama.cpp，只需在本目录加一个文件。
多模态由 ChatMessage 承载：images 走视觉通道，attachments_text 拼进文本上下文，
路由细节由各 provider 自行决定（不同厂商的多模态接口差异很大，不宜在核心里假设）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ...core.types import ChatMessage


class AiProvider(ABC):
    name: str = ""
    display_name: str = ""
    supports_vision: bool = False

    def set_api_key(self, key: str) -> None:
        ...

    @abstractmethod
    def available(self) -> bool:
        """是否已就绪（通常指 API Key 是否配置）。"""
        ...

    @abstractmethod
    def chat(self, msg: ChatMessage) -> str:
        """处理一轮对话，返回回复文本。失败请抛异常，由管线转成事件。"""
        ...

    def clear_history(self) -> None:
        ...

    def get_history(self) -> List[dict]:
        return []
