# -*- coding: utf-8 -*-
"""附件解析器：PDF（PyMuPDF 提取全文，按页标注，超长自动截断）。"""
from __future__ import annotations

from .base import MAX_TEXT_CHARS, AttachParser
from ...core.types import Attachment


class PdfParser(AttachParser):
    name = "pdf"
    display_name = "PDF 文档"
    kind = "pdf"
    extensions = {".pdf"}

    def available(self) -> bool:
        try:
            import pymupdf  # noqa: F401
            return True
        except ImportError:
            try:
                import fitz  # noqa: F401
                return True
            except ImportError:
                return False

    def parse(self, path: str) -> Attachment:
        try:
            doc = self._open(path)
        except ImportError:
            return self.make(path, error="未安装 PyMuPDF（pip install PyMuPDF）")
        except Exception as e:
            return self.make(path, error=f"PDF 打开失败: {e}")

        try:
            parts, total = [], 0
            page_count = len(doc)
            for i, page in enumerate(doc):
                ptext = page.get_text()
                parts.append(f"--- 第 {i + 1}/{page_count} 页 ---\n{ptext}")
                total += len(ptext)
                if total > MAX_TEXT_CHARS:
                    parts.append("\n...（后续页已省略）")
                    break
            return self.make(path, text="\n".join(parts))
        except Exception as e:
            return self.make(path, error=f"PDF 解析失败: {e}")
        finally:
            try:
                doc.close()
            except Exception:
                pass

    @staticmethod
    def _open(path):
        # PyMuPDF 已把 `import fitz` 标记为 deprecated，优先用新名字
        try:
            import pymupdf
            return pymupdf.open(path)
        except ImportError:
            import fitz
            return fitz.open(path)
