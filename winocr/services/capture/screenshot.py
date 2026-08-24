# -*- coding: utf-8 -*-
"""捕获源：屏幕框选截图 / 全屏截图。"""
from __future__ import annotations

from .base import CaptureSource
from ...core.types import Capture


class RegionScreenshotSource(CaptureSource):
    """拖拽框选一块屏幕区域。需要 UI 注入 parent，且必须在主线程调用。"""

    name = "screenshot"
    display_name = "框选截图"
    needs_main_thread = True

    def __init__(self) -> None:
        self.parent = None            # 由 UI 通过 configure(parent=root) 注入

    def capture(self) -> Capture:
        if self.parent is None:
            # 没有 UI 上下文时退化为全屏，保证 CLI 场景也能用
            return FullScreenSource().capture()
        from ._region import select_region
        img = select_region(self.parent)
        if img is None:
            return Capture(kind="empty", source_label="截图已取消")
        return Capture(kind="image", image=img, source_label="框选截图")


class FullScreenSource(CaptureSource):
    name = "fullscreen"
    display_name = "全屏截图"

    def capture(self) -> Capture:
        from PIL import ImageGrab
        return Capture(kind="image", image=ImageGrab.grab(), source_label="全屏截图")
