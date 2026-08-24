# -*- coding: utf-8 -*-
"""捕获源契约（插件轴）：截图 / 剪贴板 / 文件 / 未来的摄像头、窗口抓取。

设计要点：capture() 可能需要 UI 上下文（如框选需要 Tk 主窗口做父窗口），
但捕获源不该反向依赖 UI 层。折中方案是 configure(parent=...) 由 UI 注入，
捕获源只把它当作不透明句柄使用。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ...core.types import Capture


class CaptureSource(ABC):
    name: str = ""
    display_name: str = ""
    needs_main_thread: bool = False      # True 表示必须在 UI 主线程调用

    @abstractmethod
    def capture(self) -> Capture:
        """执行一次捕获。取消或无内容时返回空 Capture（而非抛异常）。"""
        ...

    def available(self) -> bool:
        return True

    def configure(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
