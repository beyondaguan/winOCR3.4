# -*- coding: utf-8 -*-
"""场景胶囊发现器。

胶囊是 WinOCR 的「场景化动作」：把截图、OCR、翻译、AI 分析打包成一个
可由热键或侧边栏触发的原子能力。例如：
  - 「截图并翻译」胶囊：F1 → 截图 → OCR → 翻译 → 弹结果
  - 「划词搜索」胶囊：监听选中文本 → 弹出贴条

本模块只负责发现；执行逻辑由胶囊自身实现，App 仅把它注册到事件总线。
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Type


# 胶囊约定属性（UI / doctor 展示用）
CAPSULE_META_ATTRS = ("display_name", "enabled", "hotkey", "ui_section")


@dataclass
class CapsuleContext:
    """胶囊只读上下文快照：当前用户选中的、OCR 的、翻译的内容。"""

    selected_text: str = ""
    ocr_text: str = ""
    translate_text: str = ""
    source_type: str = ""
    source_path: str = ""
    image_hash: str = ""
    extra: Dict[str, object] = field(default_factory=dict)


class Capsule:
    """第三方场景胶囊基类。

    子类只需声明类属性并实现 run(ctx, pipeline) 即可被自动发现。
    """

    name: str = ""
    display_name: str = ""
    hotkey: str = ""
    enabled: bool = True
    sort_order: int = 1000
    ui_section: str = ""          # "actions" / "knowledge" / ""
    description: str = ""
    is_capsule: bool = True

    def run(self, ctx: CapsuleContext, pipeline) -> None:
        """执行胶囊逻辑。所有结果通过 pipeline 服务或事件总线落地。"""
        raise NotImplementedError


def discover_capsules(package_name: str = "winocr.capsules") -> Dict[str, Type]:
    """发现指定包及其子模块下所有标记为胶囊的类。

    识别规则（满足其一即可）：
      1. 类上显式声明 `is_capsule = True`
      2. 类同时具有 `display_name` / `enabled` / `hotkey` / `ui_section` 属性

    包不存在或导入失败时返回空字典，绝不阻塞应用启动。
    """
    out: Dict[str, Type] = {}
    try:
        pkg = importlib.import_module(package_name)
    except Exception as e:
        logger.debug("胶囊包 %s 导入失败，已跳过：%s", package_name, e)
        return out

    # 扫描包本身以及所有子模块
    modules = [pkg]
    try:
        pkg_path = getattr(pkg, "__path__", None)
        if pkg_path:
            for _, name, _ in pkgutil.iter_modules(pkg_path, prefix=package_name + "."):
                try:
                    modules.append(importlib.import_module(name))
                except Exception as e:
                    logger.debug("跳过无法导入的模块 %s：%s", name, e)
    except Exception as e:
        logger.debug("iter_modules 失败，仅扫描包顶层：%s", e)

    for mod in modules:
        for name in dir(mod):
            if name.startswith("_"):
                continue
            obj = getattr(mod, name)
            if not inspect.isclass(obj):
                continue
            if obj is Capsule:                  # 排除基类自身
                continue
            if getattr(obj, "is_capsule", False):
                out[name] = obj
                continue
            if all(hasattr(obj, attr) for attr in CAPSULE_META_ATTRS):
                out[name] = obj
    return out
