# -*- coding: utf-8 -*-
"""管线编排 — 把「捕获 → OCR → 翻译 → AI → 持久化」串成可组合的步骤。

旧版这些步骤是 WinOCR.py 里几十个互相调用、共享全局变量的函数，
调用顺序与状态复位靠人脑维护，漏一个 finally 就死锁。

3.0：每一步都是独立、可单测的服务调用；长任务统一走 run_async 丢到后台线程，
进度与结果只通过事件总线回传，UI 永不阻塞，也没有需要手工复位的全局标志。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

from .event_bus import EventBus, Events
from .types import Attachment, Capture, ChatMessage, OcrResult, TranslateResult


class Pipeline:
    """持有各轴服务实例，对外提供高层动作。"""

    def __init__(self, bus: EventBus, services: dict, config=None) -> None:
        self.bus = bus
        self.services = services
        self.config = config

    # ------------------------------------------------------------------
    # 单步（同步，可被后台线程调用，也可被测试直接调用）
    # ------------------------------------------------------------------
    def ocr(self, capture: Capture) -> OcrResult:
        eng = self.services.get("ocr")
        if eng is None:
            raise RuntimeError("未配置 OCR 引擎")
        if capture.image is None:
            return OcrResult()
        self.bus.publish(Events.OCR_START, capture)
        t0 = time.time()
        res = eng.recognize(capture.image)
        res.elapsed = time.time() - t0
        self.bus.publish(Events.OCR_DONE, res)
        return res

    def translate(self, text: str, target: Optional[str] = None,
                  explicit: bool = False, note: str = "",
                  silent: bool = False) -> TranslateResult:
        """explicit=True 表示目标语言由用户明确指定，不允许自动纠正方向。
        note 为本次翻译的方向说明（如「（选中片段 → 中文）」），随事件发布，仅 UI 展示。
        silent=True 表示不向事件总线广播（TRANSLATE_START / TRANSLATE_DONE）。
        供「结果不进主界面译文框」的调用方使用——例如蒙版翻译，它有自己的
        渲染目标，若照常广播会覆盖主界面译文框、改状态栏，甚至触发自动朗读。"""
        disp = self.services.get("translate")
        if disp is None:
            raise RuntimeError("未配置翻译服务")
        if not text or not text.strip():
            return TranslateResult()
        if not silent:
            self.bus.publish(Events.TRANSLATE_START, (text, note))
        t0 = time.time()
        res = disp.translate_detailed(text, target, explicit)
        res.elapsed = time.time() - t0
        if not silent:
            self.bus.publish(Events.TRANSLATE_DONE, res)
        return res

    def chat(self, msg: ChatMessage) -> str:
        ai = self.services.get("ai")
        if ai is None:
            raise RuntimeError("未配置 AI 服务")
        if not ai.available():
            raise RuntimeError("AI 未就绪：请先在「API 设置」填入 GLM API Key")
        self.bus.publish(Events.CHAT_START, msg)
        reply = ai.chat(msg)
        self.bus.publish(Events.CHAT_DONE, reply)
        return reply

    def parse_attachment(self, path: str) -> Attachment:
        """交给附件轴解析（图片直接读，文档抽文本）。"""
        parsers = self.services.get("attach") or {}
        for parser in parsers.values():
            if parser.can_handle(path):
                return parser.parse(path)
        return Attachment(kind="text", path=path,
                          error="不支持的文件类型")

    def record(self, ocr_text: str = "", translate_text: str = "", extra: dict = None) -> None:
        store = self.services.get("persistence")
        if store is None:
            return
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
               "ocr": ocr_text, "translate": translate_text}
        if extra:
            rec.update(extra)
        try:
            store.append_record(rec)
        except Exception as e:
            logger.warning("[持久化] 写入失败: %s", e)

    # ------------------------------------------------------------------
    # 知识库（数据资产闭环）
    # ------------------------------------------------------------------
    def save_knowledge(self, record) -> int:
        """把一条可溯源的知识存入本地知识库，返回记录 id。"""
        kb = self.services.get("knowledge")
        if kb is None:
            raise RuntimeError("知识库服务未初始化")
        return kb.save_knowledge(record)

    def search_knowledge(self, query: str, limit: int = 50):
        """按关键词 / 原图哈希回看历史知识。"""
        kb = self.services.get("knowledge")
        if kb is None:
            return []
        return kb.search(query, limit)

    # ------------------------------------------------------------------
    # 增强对话（P2 理解层）：锚定当前捕获物 + 召回知识库记忆
    # ------------------------------------------------------------------
    @staticmethod
    def _recall_candidates(anchor: dict, question: str):
        """召回查询候选，从精确到宽泛。

        优先级：① 用户选中文字（最明确的指向）
              ② 当前捕获原文（「同一批文档反复回看」的天然主键）
              ③ 用户问题（逐级截短兜底，如「肌酐偏高要紧吗？」→「肌酐偏高」→「肌酐」）
        """
        anchor = anchor or {}
        cands = []
        sel = (anchor.get("selected_text") or "").strip()
        ocr = (anchor.get("ocr_text") or "").strip()
        q = (question or "").strip()
        if len(sel) >= 2:
            cands.append(sel)
        if len(ocr) >= 3:
            cands.append(ocr)
        if len(q) >= 2:
            cands.append(q)
        return cands

    def _recall(self, kb, anchor: dict, question: str, limit: int):
        """依次尝试候选查询，命中即返回（去重）。"""
        seen, seen_ids = [], set()
        for cand in self._recall_candidates(anchor, question):
            q = cand
            for _ in range(6):                 # 逐级截短 2 字符兜底（如「肌酐偏高要紧吗？」→「肌酐」）
                if len(q) < 2:
                    break
                try:
                    hits = kb.search(q, limit) or []
                except Exception as e:
                    logger.debug("知识库召回查询失败 %r：%s", q, e)
                    hits = []
                if hits:
                    for r in hits:
                        key = getattr(r, "id", id(r))
                        if key not in seen_ids:
                            seen.append(r)
                            seen_ids.add(key)
                    return seen
                q = q[:-2]
        return seen

    def chat_with_context(self, msg: ChatMessage, anchor: dict = None,
                          recall: bool = True, recall_limit: int = 3) -> dict:
        """在常规 chat() 之上，把「当前屏幕内容」与「知识库相关历史」注入上下文。

        anchor（当前捕获物，供溯源与锚定）：
            ocr_text / translate_text / selected_text /
            source_type / source_path / image_hash

        返回 {"reply": str, "recalled": List[KnowledgeRecord]}。
        recall=False 或知识库缺失时，recalled 为空列表，绝不阻塞对话。
        """
        anchor = anchor or {}

        # ---- 锚定当前捕获物（高可信上下文：用户屏幕上的东西）----
        ctx_blocks = []
        sel = (anchor.get("selected_text") or "").strip()
        ocr = (anchor.get("ocr_text") or "").strip()
        tr = (anchor.get("translate_text") or "").strip()
        if sel or ocr:
            parts = []
            if sel:
                parts.append(f"【用户选中的文字】\n{sel}")
            if ocr:
                parts.append(f"【屏幕识别原文】\n{ocr}")
            if tr:
                parts.append(f"【译文】\n{tr}")
            ctx_blocks.append("\n\n".join(parts))

        # ---- 召回知识库记忆（中可信上下文：过去沉淀过的理解）----
        recalled = []
        if recall:
            kb = self.services.get("knowledge")
            if kb is not None:
                recalled = self._recall(kb, anchor, msg.text, recall_limit)
            if recalled:
                lines = []
                for i, r in enumerate(recalled, 1):
                    body = (r.ai_explanation or r.ocr_text or "").strip()
                    if len(body) > 200:
                        body = body[:200] + "…"
                    lines.append(f"{i}. {body}")
                ctx_blocks.append(
                    "【知识库相关记忆（你以前沉淀过的理解，可参考，有冲突时以新问答为准）】\n"
                    + "\n".join(lines))

        # 注入为「背景上下文」，与用户输入/附件并列但语义独立
        if ctx_blocks:
            msg.context_blocks.extend(ctx_blocks)

        reply = self.chat(msg)
        return {"reply": reply, "recalled": recalled}

    # ------------------------------------------------------------------
    # 组合动作
    # ------------------------------------------------------------------
    def extract_and_translate(self, capture: Capture,
                              target: Optional[str] = None,
                              auto_translate: Optional[bool] = None) -> dict:
        """OCR →（可选）翻译 → 记历史。整条链路的唯一实现，UI 只需调它。"""
        if capture.image is None and capture.text:
            ocr_res = OcrResult(text=capture.text, engine="clipboard-text")
            self.bus.publish(Events.OCR_DONE, ocr_res)
        else:
            ocr_res = self.ocr(capture)

        if auto_translate is None:
            auto_translate = bool(getattr(self.config, "auto_translate", True)) \
                if self.config is not None else True

        trans_res = TranslateResult()
        if ocr_res.ok and auto_translate:
            try:
                trans_res = self.translate(ocr_res.text, target)
            except Exception as e:
                self.bus.publish(Events.ERROR, f"翻译失败: {e}")

        self.record(ocr_res.text, trans_res.text)
        self.bus.publish(Events.WORKFLOW_DONE, ocr_res)
        return {"ocr": ocr_res, "translate": trans_res}

    # ------------------------------------------------------------------
    # 异步包装：把同步步骤丢到后台线程，异常统一转成事件（绝不静默吞掉）
    # ------------------------------------------------------------------
    def run_async(self, fn: Callable, *args,
                  on_error: Optional[Callable[[Exception], None]] = None,
                  on_done: Optional[Callable] = None,
                  cancel_event: Optional[threading.Event] = None,
                  **kwargs) -> threading.Thread:
        def _runner():
            try:
                result = fn(*args, **kwargs)
                # 任务完成但已被用户取消：丢弃结果，不回调 on_done（避免界面回填过期内容）
                if cancel_event is not None and cancel_event.is_set():
                    self.bus.publish(Events.STATUS, "已取消")
                    return
                if on_done:
                    # 二次校验：上一次检查与回调之间仍有时窗，任务结果在手、
                    # 用户恰好此刻点了取消 → 不回填过期内容
                    if cancel_event is not None and cancel_event.is_set():
                        self.bus.publish(Events.STATUS, "已取消")
                        return
                    on_done(result)
            except Exception as e:
                if cancel_event is not None and cancel_event.is_set():
                    self.bus.publish(Events.STATUS, "已取消")
                    return
                self.bus.publish(Events.ERROR, str(e))
                self.bus.publish(Events.STATUS, f"错误: {e}")
                if on_error:
                    try:
                        on_error(e)
                    except Exception:
                        pass

        t = threading.Thread(target=_runner, daemon=True,
                             name=f"winocr-{getattr(fn, '__name__', 'task')}")
        t.start()
        return t

    def status(self, text: str) -> None:
        self.bus.publish(Events.STATUS, text)
