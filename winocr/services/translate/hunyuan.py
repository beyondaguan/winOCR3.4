# -*- coding: utf-8 -*-
"""腾讯混元翻译引擎（需 API Key）。"""
from __future__ import annotations

import json
import urllib.request

from .base import TranslateEngine


class HunyuanEngine(TranslateEngine):
    name = "hunyuan"
    display_name = "腾讯混元"
    online = True

    def __init__(self) -> None:
        self.api_key = ""
        self.url = "https://tokenhub.tencentmaas.com/v1/chat/completions"
        self.model = "hy3"
        self._lang_names = {"zh-CN": "中文", "en": "英文", "ja": "日文",
                            "ko": "韩文", "fr": "法文", "de": "德文",
                            "es": "西班牙文", "ru": "俄文"}

    def set_config(self, api_key=None, **kwargs) -> None:
        if api_key is not None:
            self.api_key = api_key

    def available(self) -> bool:
        return bool(self.api_key)

    def translate(self, text: str, source: str, target: str) -> str:
        if not self.api_key:
            raise RuntimeError("腾讯混元 API Key 未配置")
        prompt = (f"将以下{self._lang_names.get(source, source)}文本翻译为"
                  f"{self._lang_names.get(target, target)}，只输出译文，"
                  f"不要解释、不要注释、保持原文的换行结构：\n\n{text}")
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False, "temperature": 0.1,
        }).encode("utf-8")

        last_err: Exception = RuntimeError("混元请求失败")
        for attempt in range(2):
            try:
                req = urllib.request.Request(self.url, data=body, method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("Authorization", f"Bearer {self.api_key}")
                # 部分网关拒绝无 User-Agent 的请求
                req.add_header("User-Agent", "WinOCR/3.4 (+https://github.com/winocr)")
                with urllib.request.urlopen(req, timeout=30 if attempt == 0 else 45) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                translated = (result.get("choices", [{}])[0]
                              .get("message", {}).get("content", "").strip())
                if not translated:
                    raise RuntimeError("混元返回空译文")
                return translated
            except Exception as e:
                last_err = e
        raise last_err
