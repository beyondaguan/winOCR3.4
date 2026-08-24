# -*- coding: utf-8 -*-
"""通用插件注册表 — 把 WinOCR2.0 的 engines/ 自动发现模式推广到所有扩展点。

相比旧实现（engines/__init__.py 只服务于翻译）的关键改进：
  - 与业务无关：泛型，任何基类都能用 discover(base, package)。
  - 惰性导入重依赖：发现阶段只 inspect 类，不触发模块顶部的 ctranslate2/rapidocr 等，
    因此「发现插件列表」这个动作本身不需要安装重型依赖（依赖在引擎方法内惰性导入）。
  - 多来源：支持内置包 + 外部插件目录（plugins/），未来可热插拔第三方插件。
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Dict, List, Type, TypeVar

T = TypeVar("T")

_PLUGIN_DIRS: List[str] = []  # 额外插件目录（绝对路径）


def register_plugin_dir(path: str) -> None:
    """注册一个外部插件目录（含 <axis>/<name>.py 结构或扁平 .py）。"""
    if path and path not in _PLUGIN_DIRS:
        _PLUGIN_DIRS.append(path)


class PluginRegistry:
    """扫描某包目录下 base 的所有子类，按 name 注册。"""

    def discover(self, base: Type[T], package_name: str) -> Dict[str, Type[T]]:
        """发现 package_name 包下所有 base 的具体子类。

        返回 {引擎.name: 类}。只收集带非空 name 的类，避免抽象基类混入。
        """
        found: Dict[str, Type[T]] = {}
        self._scan_package(base, package_name, found)
        # 额外外部插件目录（扁平 .py，按文件名作为顶层模块导入）
        for d in _PLUGIN_DIRS:
            self._scan_dir(base, Path(d), None, found)
        return found

    def _scan_package(self, base, package_name: str, found: dict) -> None:
        try:
            pkg = importlib.import_module(package_name)
        except ImportError:
            return
        paths = list(getattr(pkg, "__path__", []) or [])
        if not paths:
            return
        for pkg_path in paths:
            self._scan_dir(base, Path(pkg_path), package_name, found)

    def _scan_dir(self, base, pkg_path: Path, package_prefix: str, found: dict) -> None:
        if not pkg_path.is_dir():
            return
        # 外部插件目录（package_prefix=None）按顶层模块导入，需把目录临时挂到 sys.path
        if package_prefix is None and str(pkg_path) not in sys.path:
            sys.path.insert(0, str(pkg_path))
        # 用 pkgutil.iter_modules 而非 Path.iterdir()+suffix 过滤：
        #   - 源码形态是 .py，PyInstaller 打包形态落盘是 .pyc（如 rapidocr.cpython-312.pyc），
        #     按文件名后缀过滤会漏掉打包形态 → 插件列表为空（P3-15 打包实测踩坑）。
        #   - iter_modules 同时识别 .py/.pyc，返回去掉平台后缀的模块名，两种形态行为一致。
        try:
            infos = sorted(pkgutil.iter_modules([str(pkg_path)]),
                           key=lambda m: m.name)
        except Exception:
            return
        for _, name, _ in infos:
            if name.startswith("_"):
                continue
            # 内置包：winocr.services.translate.argos；外部目录：argos（顶层）
            mod_name = f"{package_prefix}.{name}" if package_prefix else name
            try:
                mod = importlib.import_module(mod_name)
            except Exception as e:
                print(f"[插件跳过] {mod_name}: {e}")
                continue
            for _, obj in inspect.getmembers(mod):
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, base)
                    and obj is not base
                    and getattr(obj, "name", None)
                ):
                    found[obj.name] = obj  # type: ignore[assignment]


# 全局单例（组合根持有，亦可被测试替换）
default_registry = PluginRegistry()
