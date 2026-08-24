# -*- coding: utf-8 -*-
"""无界面控制台 UI 适配器 — 仅用于演示 / 测试组合根接线，不依赖 Tkinter。"""
from __future__ import annotations

from ..core.app import App
from .base import UiAdapter


class ConsoleUi(UiAdapter):
    def bind(self, app: App) -> None:
        self.app = app

    def run(self) -> None:
        print("[ConsoleUI] 应用已启动（无界面模式）。")
        print(f"  已发现插件: "
              f"{ {k: list(v.keys()) for k, v in self.app.discovered.items()} }")
        # 演示：监听事件总线
        self.app.bus.subscribe("status", lambda p: print(f"  [状态] {p}"))
        self.app.bus.publish("status", "控制台 UI 就绪")
