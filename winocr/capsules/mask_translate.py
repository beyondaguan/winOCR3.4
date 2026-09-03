# -*- coding: utf-8 -*-
"""蒙版翻译胶囊。

框选屏幕区域后，在该区域钉一个半透明置顶浮层，显示译文（原地遮蔽）。

- 文本获取双路径：首次优先系统剪贴板文本（可复制场景），否则 OCR 选区
  截图；滚动刷新统一走 OCR。
- 蒙版角落有「方向」按钮（自动 / 中→英 / 英→中），可即时切换重译。
- 页面滚动后选区坐标内容变化，蒙版每 ~800ms 轮询重截，内容变了才重译。
"""
from __future__ import annotations

from winocr.core.capsule import Capsule, CapsuleContext
from winocr.services.capture._region import select_region_box
from winocr.ui.tk.mask_window import MaskWindow


class MaskTranslateCapsule(Capsule):
    name = "mask_translate"
    display_name = "蒙版翻译"
    enabled = True
    hotkey = "ctrl+shift+m"     # 默认全局快捷键（用户可在「热键设置」中改/清除）
    ui_section = "actions"
    sort_order = 50
    description = ("框选区域后原地遮蔽翻译：半透明蒙版显示译文，"
                  "支持中英方向切换与滚动刷新。")

    def run(self, ctx: CapsuleContext, pipeline, ui=None) -> None:
        # 必须在 Tk 主线程调用（RegionSelector 依赖主 Tk 实例）
        parent = getattr(ui, "root", None) if ui is not None else None
        img, bbox = select_region_box(parent)
        if img is None or bbox is None:
            return
        MaskWindow(parent, bbox, pipeline, ui)
