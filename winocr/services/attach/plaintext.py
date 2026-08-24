# -*- coding: utf-8 -*-
"""附件解析器：纯文本 / 代码 / CSV 等（直接读，编码错误容错替换）。"""
from __future__ import annotations

from .base import AttachParser
from ...core.types import Attachment


class PlainTextParser(AttachParser):
    name = "text"
    display_name = "文本/代码"
    kind = "text"
    extensions = {".txt", ".md", ".json", ".xml", ".html", ".htm", ".csv", ".tsv",
                  ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".go", ".rs",
                  ".sh", ".bat", ".ps1", ".sql", ".log", ".yaml", ".yml",
                  ".ini", ".cfg", ".toml"}

    def parse(self, path: str) -> Attachment:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return self.make(path, text=f.read())
        except Exception as e:
            return self.make(path, error=f"读取失败: {e}")
