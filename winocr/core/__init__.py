# -*- coding: utf-8 -*-
"""核心层：与具体技术无关的骨架（配置 / 事件 / 注册表 / 管线 / 组合根）。

依赖方向铁律：core 不依赖 services，services 不依赖 ui。
反向依赖一旦出现，插件化就会退化成「看起来分了文件的巨石」。
"""
from .app import App
from .config import AppConfig
from .event_bus import EventBus, Events
from .pipeline import Pipeline
from .registry import PluginRegistry, default_registry, register_plugin_dir
from .types import (Attachment, Capture, ChatMessage, KnowledgeRecord, Lang,
                    OcrResult, TranslateResult)

__all__ = [
    "App", "AppConfig", "EventBus", "Events", "Pipeline",
    "PluginRegistry", "default_registry", "register_plugin_dir",
    "Attachment", "Capture", "ChatMessage", "KnowledgeRecord", "Lang",
    "OcrResult", "TranslateResult",
]
