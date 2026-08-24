# -*- coding: utf-8 -*-
"""附件解析契约（插件轴）。

WinOCR2.0 的 file_attach.py 是一坨 if kind == "pdf" / "doc" / "xlsx" 的分支，
每加一种格式就要改那个函数（违反开闭原则）。

3.0 把每种格式做成一个解析器插件：想支持 PPT、EPUB、ZIP？
在本目录放一个 .py，实现 can_handle/parse 即可，本体一行不改。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Set

from ...core.types import Attachment

# 单个附件提取文本上限，防止超大 PDF 撑爆请求
MAX_TEXT_CHARS = 30000


class AttachParser(ABC):
    name: str = ""
    display_name: str = ""
    extensions: Set[str] = set()
    kind: str = "text"                 # image / pdf / doc / xlsx / text

    def can_handle(self, path: str) -> bool:
        return os.path.splitext(path)[1].lower() in self.extensions

    @abstractmethod
    def parse(self, path: str) -> Attachment:
        ...

    def available(self) -> bool:
        return True

    # -- 供子类复用 --
    @staticmethod
    def truncate(text: str) -> str:
        if len(text) > MAX_TEXT_CHARS:
            return text[:MAX_TEXT_CHARS] + \
                f"\n\n...（内容过长，已截断至 {MAX_TEXT_CHARS} 字符）"
        return text

    def make(self, path: str, text: str = "", error: str = "", image=None) -> Attachment:
        return Attachment(kind=self.kind, path=path, image=image,
                          extracted_text=self.truncate(text) if text else "",
                          error=error)


def human_size(path: str) -> str:
    try:
        sz = float(os.path.getsize(path))
    except OSError:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if sz < 1024:
            return f"{sz:.0f}{unit}"
        sz /= 1024
    return f"{sz:.0f}TB"
