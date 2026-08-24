# -*- coding: utf-8 -*-
"""第三方胶囊示例 — 把它丢进 plugins/capsules/ 就会自动出现（零改核心代码）。

用法：
  1. 复制本文件改名，比如 my_ai_summary.py；
  2. 改 name（唯一注册键）、display_name（按钮文字）、hotkey（可选）、sort_order；
  3. 实现 run(ctx, pipeline)：ctx 是只读快照（selected_text/ocr_text/translate_text/...），
     结果一律通过 pipeline 服务 / bus 事件落地，不要碰任何 UI 控件；
  4. 想让它出现在主窗口工具栏，设 ui_section="actions"（进主操作区）或 "knowledge"（进知识库区）。
  5. 重启 WinOCR 即生效。

约定：
  - 每个胶囊 = 一个场景 = 一个 .py，全部依赖自声明；
  - run() 内异常会被 UI 层捕获并转状态栏提示，不会崩程序；
  - 需要多轮/耗时任务用 pipeline.run_async() 丢后台线程，结果经事件总线回传。

本示例默认 enabled=False（仅作为模板，不参与运行）。改成 True 即启用。
"""
from __future__ import annotations

from winocr.core.capsule import Capsule, CapsuleContext
from winocr.core.event_bus import Events


class MySampleCapsule(Capsule):
    name = "sample_status"                     # 唯一注册键
    display_name = "示例：报个到"               # 按钮文字
    hotkey = ""                               # 如 "ctrl+shift+9"；留空 = 无默认热键
    enabled = False                           # 模板默认关闭；改 True 启用
    sort_order = 900                          # 按钮排序，越小越靠前
    ui_section = "actions"                    # 空=不进工具栏；"actions" / "knowledge"
    description = "第三方胶囊示例：把当前选中文字长度发到状态栏"

    def run(self, ctx: CapsuleContext, pipeline) -> None:
        text = (ctx.selected_text or ctx.ocr_text or "").strip()
        if not text:
            pipeline.status("示例胶囊：没有可处理的文字")
            return
        pipeline.status(f"示例胶囊：当前选中 {len(text)} 字")
        pipeline.bus.publish(Events.STATUS, f"[示例] 收到 {len(text)} 字")
