# -*- coding: utf-8 -*-
"""持久化：JSON 历史记录 + Markdown / 纯文本导出。

存放位置由 core.paths 决定（便携模式放项目内，常规模式放 ~/.winocr）。
写入采用「先写临时文件再替换」，避免进程被强杀时把历史文件写成半截 JSON。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import List, Optional

from .base import Persistence

logger = logging.getLogger(__name__)

_MAX_RECORDS = 500          # 只保留最近 N 条，防止文件无限膨胀


class JsonHistory(Persistence):
    name = "json_history"
    display_name = "JSON 历史记录"

    def __init__(self, path: Optional[str] = None,
                 project_id: Optional[str] = None) -> None:
        # 优先用显式 path（向后兼容），否则按项目 id 取隔离文件；
        # 两者皆无则回落旧全局 history.json。
        self._explicit_path = path
        self._project_id = project_id
        self._lock = threading.Lock()
        self._resolve_path()

    def _resolve_path(self) -> None:
        from ...core.paths import history_path, project_history_path
        if self._explicit_path:
            self.path = self._explicit_path
        elif self._project_id:
            self.path = str(project_history_path(self._project_id))
        else:
            self.path = str(history_path())

    def set_project(self, project_id: str) -> None:
        """切换当前项目（ProjectManager 调用）：重指当前历史文件。"""
        self._project_id = project_id
        self._explicit_path = None
        self._resolve_path()

    def append_record(self, record: dict) -> None:
        if not (record.get("ocr") or record.get("translate")):
            return                                   # 空记录不写
        with self._lock:
            records = self.load_records()
            records.append(record)
            if len(records) > _MAX_RECORDS:
                records = records[-_MAX_RECORDS:]
            self._atomic_write(records)

    def load_records(self) -> List[dict]:
        if not os.path.isfile(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []                                # 文件损坏就当空历史，别让程序崩

    def clear(self) -> None:
        """清空全部历史。清空前自动留一份备份（<历史文件>.bak，覆盖式保留
        最近一次清空前的全量记录），误清后可把 .bak 改名还原。"""
        with self._lock:
            records = self.load_records()
            if records:
                self._backup(records)
            self._atomic_write([])

    def _backup(self, records: list) -> None:
        try:
            with open(self.path + ".bak", "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("[历史] 清空备份失败: %s", e)

    def _atomic_write(self, records: list) -> None:
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            logger.warning("[历史] 写入失败: %s", e)
            try:
                os.remove(tmp)
            except OSError:
                pass


def export_markdown(records: list, path: str) -> None:
    lines = ["# WinOCR 识别记录导出", ""]
    for i, r in enumerate(records, 1):
        lines.append(f"## {i}. {r.get('time', '')}")
        if r.get("ocr"):
            lines.append(f"**原文**\n\n```\n{r['ocr']}\n```\n")
        if r.get("translate"):
            lines.append(f"**译文**\n\n{r['translate']}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def export_text(records: list, path: str) -> None:
    blocks = []
    for r in records:
        blocks.append(f"[{r.get('time', '')}]\n{r.get('ocr', '')}\n"
                      f"--- 译文 ---\n{r.get('translate', '')}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))
