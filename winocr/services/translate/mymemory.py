# -*- coding: utf-8 -*-
"""MyMemory 免费在线翻译（免 Key，单次请求有长度限制，作为最后兜底）。"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .base import TranslateEngine


class MyMemoryEngine(TranslateEngine):
    name = "mymemory"
    display_name = "MyMemory（免 Key 兜底）"
    online = True

    def __init__(self) -> None:
        self.url = "https://api.mymemory.translated.net/get"
        self.max_chunk = 480
        self._lang_map = {"zh-CN": "zh-CN", "zh": "zh-CN", "en": "en",
                          "ja": "ja", "ko": "ko", "fr": "fr", "de": "de",
                          "es": "es", "ru": "ru"}

    def available(self) -> bool:
        return True

    def translate(self, text: str, source: str, target: str) -> str:
        """单段 ≤ max_chunk 直接翻；长文本按行切块分段翻译再拼接（保留换行）。

        此前直接 ``text[:max_chunk]`` 静默截断，兜底引擎会把大段译文只剩开头。
        免费接口单次长度受限，切块是唯一不丢内容的方式。
        """
        langpair = f"{self._lang_map.get(source, source)}|" \
                   f"{self._lang_map.get(target, target)}"
        chunks = self._chunk(text, self.max_chunk)
        if len(chunks) == 1:
            return self._request(chunks[0], langpair)
        parts = [self._request(c, langpair) for c in chunks]
        return "\n".join(parts)

    def _request(self, q: str, langpair: str) -> str:
        params = urllib.parse.urlencode({"q": q, "langpair": langpair})
        last_err: Exception = RuntimeError("MyMemory 请求失败")
        for attempt in range(2):                 # 免费接口偶发超时，重试一次
            try:
                req = urllib.request.Request(f"{self.url}?{params}")
                req.add_header("User-Agent", "Mozilla/5.0")
                with urllib.request.urlopen(req, timeout=20 if attempt == 0 else 30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                if result.get("responseStatus") != 200:
                    raise RuntimeError(
                        f"MyMemory 错误: {result.get('responseDetails', 'unknown')}")
                return result.get("responseData", {}).get("translatedText", "").strip()
            except Exception as e:
                last_err = e
        raise last_err

    @staticmethod
    def _chunk(text: str, max_len: int) -> list:
        """按行切块，每块不超过 max_len；超长单行硬切。保留换行结构。"""
        chunks: list = []
        cur: list = []
        cur_len = 0
        for line in (text or "").split("\n"):
            line = line.rstrip()
            while len(line) > max_len:            # 单行超过上限：硬切成多段
                if cur:
                    chunks.append("\n".join(cur))
                    cur, cur_len = [], 0
                chunks.append(line[:max_len])
                line = line[max_len:]
            if cur and cur_len + len(line) + 1 > max_len:
                chunks.append("\n".join(cur))
                cur, cur_len = [], 0
            cur.append(line)
            cur_len += len(line) + 1
        if cur:
            chunks.append("\n".join(cur))
        return chunks or [""]
