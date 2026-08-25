# -*- coding: utf-8 -*-
"""线程安全事件总线 — 取代 WinOCR2.0 的全局状态机（_chatting 等全局锁）。

问题回顾：旧版在 WinOCR.py 用模块级全局变量 _chatting 当互斥锁，
工作线程若因异常没走到 finally 复位，就会永久卡死（「第二轮对话无响应」）。

3.0 的解法有三层：
  1. 发布/订阅解耦：后台线程只管 publish，UI 只管 subscribe，双方互不持锁。
  2. 订阅者异常隔离：单个 handler 抛错不影响其他订阅者，更不会中断发布者。
  3. 快照式派发：publish 时对订阅表取快照，允许 handler 内部再订阅/退订而不死锁。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subs: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event: str, handler: Callable) -> Callable[[], None]:
        """订阅事件，返回一个退订函数（便于 UI 销毁时清理）。"""
        with self._lock:
            self._subs.setdefault(event, []).append(handler)

        def _unsub() -> None:
            self.unsubscribe(event, handler)

        return _unsub

    def once(self, event: str, handler: Callable) -> None:
        """只触发一次的订阅。"""
        def _wrapper(payload):
            self.unsubscribe(event, _wrapper)
            handler(payload)
        self.subscribe(event, _wrapper)

    def unsubscribe(self, event: str, handler: Callable) -> None:
        with self._lock:
            lst = self._subs.get(event)
            if not lst:
                return
            try:
                lst.remove(handler)
            except ValueError:
                pass

    def publish(self, event: str, payload=None) -> None:
        """发布事件。对订阅表取快照后在锁外调用，避免 handler 内部再订阅时死锁。"""
        with self._lock:
            handlers = list(self._subs.get(event, ()))
        for h in handlers:
            try:
                h(payload)
            except Exception as e:      # 订阅者异常绝不冒泡回发布者
                logger.exception(f"[事件总线] 处理器异常 ({event}): {e}")

    def clear(self) -> None:
        with self._lock:
            self._subs.clear()


class Events:
    """预定义事件名（集中定义，避免字符串拼写不一致）。"""
    # 管线
    CAPTURE_DONE = "capture:done"
    OCR_START = "ocr:start"
    OCR_DONE = "ocr:done"
    TRANSLATE_START = "translate:start"
    TRANSLATE_DONE = "translate:done"
    WORKFLOW_DONE = "workflow:done"        # 整条链路（捕获→OCR→翻译→历史）结束
    # AI 对话
    CHAT_START = "chat:start"
    CHAT_TOKEN = "chat:token"
    CHAT_DONE = "chat:done"
    CHAT_ERROR = "chat:error"
    # 通用
    STATUS = "status"
    PROGRESS = "progress"
    ERROR = "error"
    CONFIG_CHANGED = "config:changed"
    HOTKEY = "hotkey"
    # 知识库（数据资产闭环）
    KB_SAVE = "kb:save"
    KB_RESULTS = "kb:results"
    KB_SEARCH = "kb:search"
    # 结构化输出（P4：截图→Markdown）
    MD_RESULT = "md:result"
    # 划词翻译（P4：小贴条）
    STICKER_RESULT = "sticker:result"
