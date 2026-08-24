# -*- coding: utf-8 -*-
"""持久化契约（插件）：历史记录 / 导出。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ...core.types import OcrResult, TranslateResult, ChatMessage


class Persistence(ABC):
    name: str = ""
    display_name: str = ""

    @abstractmethod
    def append_record(self, record: dict) -> None:
        ...

    @abstractmethod
    def load_records(self) -> list:
        ...
