# -*- coding: utf-8 -*-
"""UI 适配器契约（插件）— 让表现层与核心彻底解耦。

旧版 UI 直接写在 WinOCR.py 同一文件里，无法换成 Web / 托盘 / CLI。
新版：任何 UI 只需实现 UiAdapter，App.attach_ui() 即可接管。
参考实现见 ui/console.py（无界面演示用）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.app import App


class UiAdapter(ABC):
    @abstractmethod
    def bind(self, app: App) -> None:
        ...

    @abstractmethod
    def run(self) -> None:
        ...
