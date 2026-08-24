# -*- coding: utf-8 -*-
"""OCR 引擎契约（插件轴）。

新增引擎（PaddleOCR / Tesseract / 腾讯云 OCR / WeChat OCR）只需在本目录放一个 .py，
继承 OcrEngine 并给出 name，注册表会自动发现，核心代码零改动。

约定：重依赖（onnxruntime 等）必须在方法内惰性导入，
否则「列出可用引擎」这个动作就会强制用户先装齐所有引擎的依赖。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ...core.types import OcrResult


class OcrEngine(ABC):
    name: str = ""
    display_name: str = ""
    offline: bool = True

    @abstractmethod
    def recognize(self, image) -> OcrResult:
        """输入 PIL.Image，返回 OcrResult。"""
        ...

    def available(self) -> bool:
        """依赖是否就绪（用于 doctor 自检，不应抛异常）。"""
        return True

    def configure(self, **kwargs) -> None:
        """接收注入的配置。默认把已知键写到同名属性上。"""
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def warmup(self) -> bool:
        """可选预热（提前加载模型，避免首次识别卡顿）。"""
        return True
