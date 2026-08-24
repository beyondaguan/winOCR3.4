# -*- coding: utf-8 -*-
"""翻译轴：放置各翻译引擎插件（继承 TranslateEngine）。"""
from .base import TranslateEngine
from .dispatcher import TranslateDispatcher

__all__ = ["TranslateEngine", "TranslateDispatcher"]
