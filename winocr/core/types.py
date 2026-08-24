# -*- coding: utf-8 -*-
"""跨模块共享的数据类型。

取代 WinOCR2.0 中散落在函数参数里、靠注释约定的隐式结构。
所有轴（捕获/OCR/翻译/AI/附件/持久化）通过这些数据类通信，形成显式的管线契约：
换掉任何一个实现，只要仍然收发这些类型，其余部分不需要知道它变了。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Lang(str, Enum):
    ZH = "zh-CN"
    EN = "en"
    JA = "ja"
    KO = "ko"
    FR = "fr"
    DE = "de"
    ES = "es"
    RU = "ru"

    @classmethod
    def normalize(cls, code: str) -> str:
        code = (code or "").strip().lower()
        mapping = {
            "zh": "zh-CN", "zh-cn": "zh-CN", "zh_cn": "zh-CN", "chinese": "zh-CN",
            "en": "en", "en-us": "en", "english": "en",
            "ja": "ja", "jp": "ja", "japanese": "ja",
            "ko": "ko", "korean": "ko",
            "fr": "fr", "french": "fr",
            "de": "de", "german": "de",
            "es": "es", "spanish": "es",
            "ru": "ru", "russian": "ru",
        }
        return mapping.get(code, code or "en")

    @classmethod
    def label(cls, code: str) -> str:
        return {
            "zh-CN": "中文", "en": "英文", "ja": "日文", "ko": "韩文",
            "fr": "法文", "de": "德文", "es": "西班牙文", "ru": "俄文",
        }.get(code, code)


@dataclass
class Capture:
    """一次捕获的原始输入。"""
    kind: str = "image"                  # image / text / empty
    image: object = None                 # PIL.Image | None
    text: str = ""                       # 捕获到的文本（剪贴板文字）
    source_path: Optional[str] = None
    source_label: str = ""               # 来源描述：截图 / 剪贴板 / 文件

    @property
    def is_empty(self) -> bool:
        return self.image is None and not self.text


@dataclass
class OcrResult:
    text: str = ""
    lines: List[str] = field(default_factory=list)
    boxes: list = field(default_factory=list)       # 全部识别项的四点框（按行内阅读序）
    line_boxes: list = field(default_factory=list)  # 每行一个列表：该行各识别项的框（几何重排用）
    line_items: list = field(default_factory=list)  # 每行一个列表：该行各识别项文本（分列/表格用）
    engine: str = ""
    confidence: float = 0.0
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


@dataclass
class TranslateResult:
    text: str = ""
    source_lang: str = ""
    target_lang: str = ""
    engine: str = ""
    elapsed: float = 0.0


@dataclass
class Attachment:
    """AI 对话附件（图片 / 文档）。

    图片可能来自剪贴板（没有磁盘路径），因此 image 与 path 都是可选的。
    """
    kind: str = "image"                  # image / pdf / doc / xlsx / text
    path: Optional[str] = None
    image: object = None                 # PIL.Image（剪贴板粘贴时无 path）
    display_name: str = ""
    extracted_text: str = ""
    error: str = ""

    def __post_init__(self):
        if not self.display_name:
            self.display_name = os.path.basename(self.path) if self.path else "剪贴板图片"

    @property
    def is_image(self) -> bool:
        return self.kind == "image"


@dataclass
class ChatMessage:
    role: str = "user"                   # user / assistant / system
    text: str = ""
    images: list = field(default_factory=list)          # PIL.Image 列表
    attachments_text: List[str] = field(default_factory=list)
    context_blocks: List[str] = field(default_factory=list)  # 背景上下文（锚定/知识库召回）

    @classmethod
    def from_attachments(cls, text: str, attachments: List[Attachment]) -> "ChatMessage":
        """把附件列表拆成「图片走视觉通道 / 文档走文本通道」，路由规则集中在此。"""
        images = [a.image for a in attachments if a.is_image and a.image is not None]
        docs = []
        for a in attachments:
            if not a.is_image and a.extracted_text:
                docs.append(f"【{a.display_name}】\n{a.extracted_text}")
        return cls(role="user", text=text, images=images, attachments_text=docs)


@dataclass
class KnowledgeRecord:
    """知识库中的一条沉淀知识（带溯源字段：来源 / 原图哈希 / 场景 / 时间）。

    由 KnowledgeBase（sqlite3 + FTS5）读写；「增强对话」用它召回相关历史。
    ``scenario`` / ``tags`` / ``note`` 是老 WinOCR-Portable-v1.0.0 schema 字段，
    留作向后兼容字段；新代码统一写 ``scene``，迁移时把老 ``scenario`` 拷过来。
    """
    id: int = 0
    source_type: str = ""                # screenshot / clipboard / file / chat
    source_path: str = ""                # 来源文件路径（文件类才有）
    image_hash: str = ""                 # 原图哈希（溯源：同一张图反复回看）
    scene: str = ""                      # 场景标签（如「病历」「说明书」）
    scenario: str = ""                   # 兼容：老库 schema 用的字段名
    tags: str = ""                       # 兼容：老库字段
    note: str = ""                       # 兼容：老库字段
    ocr_text: str = ""
    translate_text: str = ""
    ai_explanation: str = ""             # AI 解读（增强对话沉淀）
    created_at: str = ""                 # 本地时间 %Y-%m-%d %H:%M:%S
