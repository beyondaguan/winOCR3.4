# -*- coding: utf-8 -*-
"""蒙版翻译的译文缓存（TranslationCache）。

目的：相同原文直接复用译文，省掉翻译 API 的网络延迟（滚动后原文重现可秒出）。

线程安全：OCR+翻译在后台线程（worker）内进行，缓存只在该线程读写；但为避免未来
并发 worker 或主线程误读导致竞态，所有访问用 threading.Lock 保护。

容量上限：LRU 淘汰（默认 200 条），避免长时间运行无限增长造成内存泄漏。
"""
from __future__ import annotations

import threading
from collections import OrderedDict


class TranslationCache:
    """带锁的 LRU 译文缓存。"""

    def __init__(self, maxsize: int = 200) -> None:
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._data: "OrderedDict[str, str]" = OrderedDict()

    def get(self, key: str):
        """命中返回译文(str)，未命中返回 None。"""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return None

    def put(self, key: str, value: str) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def get_or_put(self, key: str, value: str) -> str:
        """便捷：命中即返回；未命中写入并返回（原子）。"""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            self._data[key] = value
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)
            return value
