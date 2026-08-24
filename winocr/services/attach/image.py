# -*- coding: utf-8 -*-
"""附件解析器：图片（走视觉模型通道，不提取文本）。"""
from __future__ import annotations

from .base import AttachParser
from ...core.types import Attachment


class ImageParser(AttachParser):
    name = "image"
    display_name = "图片"
    kind = "image"
    extensions = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".tif", ".webp"}

    def parse(self, path: str) -> Attachment:
        try:
            from PIL import Image
            img = Image.open(path)
            img.load()                       # 立即读入，避免文件句柄悬空
            return self.make(path, image=img)
        except Exception as e:
            return self.make(path, error=f"图片读取失败: {e}")
