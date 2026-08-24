# -*- coding: utf-8 -*-
"""Tkinter 表现层。

对外只暴露 TkUi 一个符号 —— 它是本包与 core 的唯一接缝。
main_window / chat_panel / dialogs 都是 TkUi 的内部实现细节，
换一套 UI（Web、托盘）时整包替换即可，core 一行都不用动。
"""
from .app import TkUi

__all__ = ["TkUi"]
