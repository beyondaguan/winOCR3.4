# -*- coding: utf-8 -*-
"""设置页连接测试：AI / 翻译 / 云端 OCR 的连通性自检。

从 dialogs_settings.py 拆分而来。测试请求一律丢后台线程，结果经
_post_to_ui 回写主线程的标签控件。
"""
from __future__ import annotations

import threading

from . import theme
from .dialogs_common import _post_to_ui


def _test_ai(app, key, base_url, text_model, vision_model,
              vision_base_url, vision_key, which, label) -> None:
    key = (key or "").strip()
    if not key:
        label.config(text="请先填入 API Key", foreground="#c0392b")
        return
    label.config(text="测试中…", foreground=theme.TEXT_MUTED)

    def _work():
        from ...core.types import ChatMessage
        try:
            cls = app.discovered.get("ai", {}).get("glm")
            if cls is None:
                raise RuntimeError("未发现 GLM 提供方插件")
            probe = cls()                    # 临时实例，不污染主对话历史
            probe.set_api_key(key)
            probe.set_base_url(base_url)
            probe.set_models(text_model=text_model.strip(),
                             vision_model=vision_model.strip())
            probe.set_vision_config(base_url=vision_base_url.strip(),
                                    api_key=vision_key.strip(),
                                    model=vision_model.strip())
            if which == "vision":
                from PIL import Image
                img = Image.new("RGB", (64, 64), "white")
                reply = probe.chat(ChatMessage(
                    text="用两个字描述这张图：空白", images=[img]))
                model = probe.vision_client.cfg.model
                url = probe.vision_client.cfg.effective_url()
            else:
                reply = probe.chat(ChatMessage(text="回复两个字：可用"))
                model = probe.text_client.cfg.model
                url = probe.text_client.cfg.effective_url()
            msg = f"连接正常（{model}）:\n{url}\n{reply[:40]}"
            color = "#1e8e3e"
        except Exception as e:
            msg, color = f"连接失败: {e}", "#c0392b"

        def _show():
            try:
                label.config(text=msg, foreground=color)
            except Exception:
                pass
        _post_to_ui(app, _show)

    threading.Thread(target=_work, daemon=True, name="ai-test").start()


def _test_translate(app, base_url, model, api_key, label) -> None:
    kw = app._translate_llm_kwargs()
    if not (api_key or kw["glm_api_key"]):
        label.config(text="请先在「大模型翻译」页填写 API Key", foreground="#c0392b")
        return
    label.config(text="测试中…", foreground=theme.TEXT_MUTED)

    def _work():
        try:
            cls = app.discovered.get("translate", {}).get("glm")
            if cls is None:
                raise RuntimeError("未发现 GLM 翻译引擎")
            probe = cls()
            probe.set_config(
                glm_api_key=(api_key or "").strip() or kw["glm_api_key"],
                base_url=(base_url or "").strip() or kw["base_url"],
                model=(model or "").strip() or kw["model"],
                max_output_tokens=kw["max_output_tokens"],
                retry_attempts=kw["retry_attempts"],
                retry_backoff=kw["retry_backoff"],
            )
            if not probe.available():
                raise RuntimeError("翻译 API Key 仍为空")
            out = probe.translate("Hello, world.", "en", "zh-CN")
            msg = f"翻译正常: Hello, world. → {out}"
            color = "#1e8e3e"
        except Exception as e:
            msg, color = f"连接失败: {e}", "#c0392b"

        def _show():
            try:
                label.config(text=msg, foreground=color)
            except Exception:
                pass
        _post_to_ui(app, _show)

    threading.Thread(target=_work, daemon=True, name="translate-test").start()


def _test_ocr(app, base_url, model, api_key, label) -> None:
    """用「云端 OCR」页填的连接参数发真实测试请求。

    页面上填的参数优先；留空则回落到配置里已存的值。
    """
    kw = app._cloud_ocr_kwargs()
    api_key = (api_key or "").strip() or kw["api_key"]
    base_url = (base_url or "").strip() or kw["base_url"]
    model = (model or "").strip() or kw["model"]
    if app.config.ocr.engine != "vision_ocr":
        label.config(text="当前 OCR 引擎不是「云端视觉 OCR」，无需测试云端连接",
                     foreground="#c0392b")
        return
    if not api_key:
        label.config(text="请先在「云端 OCR」页填写 API Key",
                     foreground="#c0392b")
        return
    label.config(text="测试中…", foreground=theme.TEXT_MUTED)

    def _work():
        try:
            cls = app.discovered.get("ocr", {}).get("vision_ocr")
            if cls is None:
                raise RuntimeError("未发现云端视觉 OCR 引擎")
            probe = cls()
            probe.configure(cloud_api_key=api_key,
                            cloud_base_url=base_url,
                            cloud_model=model,
                            cloud_max_output_tokens=kw["max_output_tokens"],
                            cloud_retry_attempts=kw["retry_attempts"],
                            cloud_retry_backoff=kw["retry_backoff"],
                            cloud_timeout=kw["timeout"])
            if not probe.available():
                raise RuntimeError("云端 OCR API Key 为空")
            from PIL import Image
            img = Image.new("RGB", (80, 40), "white")
            res = probe.recognize(img)
            if not res.ok:
                raise RuntimeError(res.text or "返回为空")
            msg = f"视觉 OCR 正常（{probe._model}）:\n{res.text[:60]}"
            color = "#1e8e3e"
        except Exception as e:
            msg, color = f"连接失败: {e}", "#c0392b"

        def _show():
            try:
                label.config(text=msg, foreground=color)
            except Exception:
                pass
        _post_to_ui(app, _show)

    threading.Thread(target=_work, daemon=True, name="ocr-test").start()
