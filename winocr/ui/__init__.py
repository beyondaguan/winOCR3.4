# -*- coding: utf-8 -*-
"""UI 适配器：表现层插件（Tkinter / Web / 托盘 / CLI）。"""
from .base import UiAdapter
from .console import ConsoleUi

__all__ = ["UiAdapter", "ConsoleUi"]
