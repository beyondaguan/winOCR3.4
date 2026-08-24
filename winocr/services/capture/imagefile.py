# -*- coding: utf-8 -*-
"""捕获源：从磁盘选择图片文件。"""
from __future__ import annotations

import os

from .base import CaptureSource
from ...core.types import Capture

_FILETYPES = [("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.webp"),
              ("所有文件", "*.*")]


class ImageFileSource(CaptureSource):
    name = "file"
    display_name = "打开图片文件"
    needs_main_thread = True

    def __init__(self) -> None:
        self.parent = None
        self.path = None              # 可直接注入路径，跳过对话框（拖拽/CLI 场景）

    def capture(self) -> Capture:
        path = self.path
        self.path = None              # 一次性，避免下次误用
        if not path:
            path = self._ask()
        if not path:
            return Capture(kind="empty", source_label="未选择文件")
        if not os.path.isfile(path):
            return Capture(kind="empty", source_label=f"文件不存在: {path}")
        from PIL import Image
        # Image.open 是延迟加载，不读数据的话文件句柄会一直占用（Windows 上
        # 用户删不掉/覆盖不了该图片）。copy() 把像素完整读进内存并脱离文件。
        with Image.open(path) as img:
            image = img.copy()
        return Capture(kind="image", image=image,
                       source_path=path,
                       source_label=os.path.basename(path))

    def _ask(self):
        try:
            from tkinter import filedialog
            return filedialog.askopenfilename(title="选择图片", filetypes=_FILETYPES,
                                              parent=self.parent)
        except Exception:
            try:
                return input("输入图片路径: ").strip().strip('"')
            except Exception:
                return None
