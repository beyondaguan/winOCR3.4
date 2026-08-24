# -*- mode: python ; coding: utf-8 -*-
"""WinOCR PyInstaller spec —— P3-15 分包/分发。

形态：onedir（dist/WinOCR/WinOCR.exe + _internal/）。
数据策略：models/、vendor/、plugins/、config.toml 全部与 exe 同级（外部目录），
不进 _internal —— paths.py 的 sys.frozen 分支专为此设计，零代码改动。

动态导入处理（registry/capsule 用字符串包名 importlib 扫描）：
  - collect_submodules("winocr.services") + ("winocr.ui") 兜住内置轴
  - 重型库（onnxruntime/ctranslate2/fitz/edge_tts 等）随子模块分析带入，
    再显式列关键库兜底
数据收集：
  - tkinterdnd2：需 tkdnd 二进制（官方要求 --collect-data）
  - rapidocr：包内自带 small 模型 onnx
"""
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# SPECPATH = spec 文件所在目录（packaging/），项目根在其上一级。
# 注意：spec 在 exec 命名空间运行，__file__ 不可用，必须用 PyInstaller 提供的 SPECPATH。
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=collect_data_files("tkinterdnd2") + collect_data_files("rapidocr"),
    hiddenimports=(
        collect_submodules("winocr.services")
        + collect_submodules("winocr.ui")
        + [
            "onnxruntime", "ctranslate2", "sentencepiece", "rapidocr",
            "fitz", "docx", "openpyxl", "keyboard", "uiautomation",
            "edge_tts", "tkinterdnd2", "comtypes",
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=True,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WinOCR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=os.path.join(ROOT, "packaging", "WinOCR.ico"),
)

# CLI 版：console=True，供 doctor / ocr / config 等命令行子命令输出。
# 同一 Analysis，双 exe 共用 _internal，仅 bootloader 体积增加（~几 MB）。
# windowed 主 exe 下 stdout=None，doctor 类命令的 print 会崩，故必须独立 CLI exe。
exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WinOCR-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=os.path.join(ROOT, "packaging", "WinOCR.ico"),
)

coll = COLLECT(
    exe,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="WinOCR",
)
