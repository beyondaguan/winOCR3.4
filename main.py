# -*- coding: utf-8 -*-
"""WinOCR 3.0 启动入口。

用法：
    python main.py                 启动图形界面（默认）
    python main.py gui             同上
    python main.py console         无界面模式（脚本 / 服务器上验证管线）
    python main.py doctor          自检：各轴插件是否可用、缺什么依赖
    python main.py models          查看模型资源（OCR 模型、Argos 离线翻译包）
    python main.py config [--init] 查看/生成配置文件
    python main.py ocr <图片路径>   命令行识别一张图（可加 -t 语言 做翻译）

设计要点：入口只做「解析参数 → 组装 App → 挂 UI」，一行业务逻辑都不写。
换界面只换 attach_ui 那一行，这是插件化架构最直接的收益。
"""

from __future__ import annotations

import argparse
import sys

from winocr.core import App, AppConfig
from winocr.version import __version__

# 3.4.21 日志与崩溃捕获：在最早阶段初始化，确保任何异常都有迹可循
from winocr.core.logging_config import setup_logging
from winocr.core.crash_handler import install_crash_handler


# ----------------------------------------------------------------------
def _is_real_console(stream) -> bool:
    """判断这个流是不是「真·Windows 控制台」，而不是管道/文件。

    判据用 GetConsoleMode()：控制台句柄能拿到模式，管道和文件一律失败。
    比 isatty() 可靠——某些终端模拟器会让 isatty() 说谎。
    """
    if sys.platform != "win32":
        try:
            return bool(stream.isatty())
        except Exception:
            return False
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = wintypes.DWORD()
        ok = ctypes.windll.kernel32.GetConsoleMode(
            wintypes.HANDLE(handle), ctypes.byref(mode)
        )
        return bool(ok)
    except Exception:
        return False


def _init_console() -> None:
    """让控制台输出在任何代码页下都不乱码、不崩溃。

    Windows 上「输出到控制台」和「输出到管道/文件」是两条完全不同的路，
    必须分开处理，用同一套策略一定有一边是错的：

    1) 真·控制台：Python 3.6+ 底层是 _WindowsConsoleIO，它把文本层交来的
       **UTF-8 字节**解码成宽字符再调 WriteConsoleW 绘制。也就是说，控制台
       路径天生就是 Unicode 安全的，跟 chcp 是几号毫无关系。
       此时若「好心」把编码改成 cp936，文本层会吐 GBK 字节，底层仍按 UTF-8
       去解 —— 反而制造出乱码。所以这里**只调 errors，绝不动 encoding**。

    2) 管道/文件：不走 WriteConsoleW，Python 按自身默认编码（新版本是 UTF-8）
       直接写字节，而 cmd、type、日志查看器按控制台代码页（中文机器默认
       936/GBK）解码，于是出现「鑷妫」式乱码。此时才需要把编码对齐到
       GetConsoleOutputCP()。

    另外统一设 errors="replace"：万一出现当前编码表示不了的字符（emoji、对勾），
    显示成 ? 也好过抛 UnicodeEncodeError 把主流程打断。
    完全没有控制台时（pythonw 启动 GUI，stdout 为 None）直接跳过。
    """
    enc = None
    if sys.platform == "win32":
        try:
            import ctypes

            cp = ctypes.windll.kernel32.GetConsoleOutputCP()
            if cp:  # 0 = 本进程根本没有控制台
                enc = f"cp{cp}"
        except Exception:
            enc = None

    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        # 真控制台：保持底层 UTF-8 通道，只放宽错误处理
        target = None if _is_real_console(stream) else enc
        try:
            stream.reconfigure(encoding=target, errors="replace")  # Python 3.7+
        except Exception:
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass


# CLI 输出一律用纯 ASCII 标记，保证 chcp 936 / 65001 下都不乱码、不报错。
# （GUI 里的 emoji 不受影响：Tk 内部是 Unicode，与控制台代码页无关。）
MARK_OK, MARK_NO, MARK_DOT = "[+]", "[-]", " . "

# 全局互斥锁句柄；保留引用以防 Python GC 关掉句柄。
# 单实例 GUI 启动时由 cmd_gui 创建（CreateMutexW），进程退出时由 OS 释放。
_gui_mutex = None


def cmd_gui(args) -> int:
    # 单实例锁：避免用户多次启动 run.bat 导致多个进程同时响应同一个全局热键
    # （每个实例都会创建选区窗口 / 触发 OCR，结果就是屏幕上堆出一堆重复窗口）。
    # 已有实例在跑 → 找到它的主窗口拉到前台，退出当前进程。
    global _gui_mutex
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        mutex_name = f"WinOCR_{__version__}_SingleInstance_Mutex"
        _gui_mutex = kernel32.CreateMutexW(None, False, mutex_name)
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            hwnd = 0
            try:
                # 64 位下 HWND 是 8 字节指针，必须显式声明 restype/argtypes，
                # 否则 ctypes 按 32 位 c_int 截断，FindWindowW 永远找不到窗口
                from ctypes import wintypes

                user32.FindWindowW.restype = wintypes.HWND
                user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
                # 标题须与 TkUi 主窗口标题完全一致（含版本号）
                hwnd = user32.FindWindowW(
                    None, f"WinOCR {__version__} — 截图识字 · 翻译 · AI"
                )
            except Exception:
                hwnd = 0
            if hwnd:
                # 确有可见窗口 → 拉到前台并退出（避免多实例抢全局热键）
                try:
                    from ctypes import wintypes

                    user32.IsIconic.restype = wintypes.BOOL
                    user32.IsIconic.argtypes = [wintypes.HWND]
                    user32.IsWindowVisible.restype = wintypes.BOOL
                    user32.IsWindowVisible.argtypes = [wintypes.HWND]
                    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
                    user32.SetForegroundWindow.restype = wintypes.BOOL
                    if user32.IsIconic(hwnd):
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE：还原最小化
                    elif not user32.IsWindowVisible(hwnd):
                        # 被 Esc/关闭按钮隐藏（托盘态）的窗口：SW_RESTORE 对
                        # withdraw 状态无效，必须 SW_SHOW 才能重新显示出来
                        user32.ShowWindow(hwnd, 5)  # SW_SHOW
                    user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                print("WinOCR 已经在运行 —— 已切到现有窗口。当前进程退出。")
                return 0
            # 否则：锁残留但窗口已死（上次崩溃/异常退出未释放锁），视为僵尸锁。
            # 继续启动，让用户能重新打开；旧的僵尸进程可在任务管理器结束。
    except Exception:
        pass  # 非 Windows / ctypes 不可用，跳过

    app = App().build()

    # 3.4.21 日志与崩溃捕获初始化（读取 config 中的 logging 配置）
    log_cfg = app.config.logging
    setup_logging(
        level=log_cfg.level or None,
        log_dir=log_cfg.dir or None,
        console=log_cfg.console,
    )
    install_crash_handler()
    try:
        from winocr.ui.tk import TkUi

        app.attach_ui(TkUi())
    except Exception as e:
        print(f"[错误] 原生 (Tk) 界面不可用: {e}")
        print("       请改用: python main.py console")
        return 2
    try:
        app.start()
    except Exception:
        # crash_handler 已接管 sys.excepthook，这里无需手动写 crash.log
        raise
    return 0


def cmd_console(args) -> int:
    from winocr.ui import ConsoleUi

    app = App().build()
    app.attach_ui(ConsoleUi())
    app.start()
    return 0


def cmd_doctor(args) -> int:
    from winocr.core.paths import (
        argos_search_dirs,
        config_path,
        history_path,
        ocr_model_dir,
        plugins_dir,
        user_dir,
    )

    app = App().build()

    print("=" * 60)
    print(f"  WinOCR {__version__} — 自检")
    print("=" * 60)
    print(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
    print(f"用户目录    : {user_dir()}")
    print(
        f"配置文件    : {config_path()}  {'[存在]' if config_path().is_file() else '[未创建，用默认值]'}"
    )
    print(f"历史记录    : {history_path()}")
    print(
        f"插件目录    : {plugins_dir()}  {'[存在]' if plugins_dir().is_dir() else '[无，可自建]'}"
    )

    print(
        f"\n[插件装载情况]  {MARK_OK}可用  {MARK_NO}缺依赖/未配置  {MARK_DOT}未实例化"
    )
    axis_label = {
        "ocr": "OCR 引擎",
        "translate": "翻译引擎",
        "ai": "AI 提供方",
        "capture": "捕获源",
        "attach": "附件解析",
        "persistence": "持久化",
    }
    for axis, items in app.describe().items():
        print(f"  {axis_label.get(axis, axis)}:")
        for it in items:
            flag = {True: MARK_OK, False: MARK_NO, None: MARK_DOT}[it["available"]]
            print(f"     {flag} {it['name']:<12} {it['display']}")

    print("\n[关键依赖]")
    for mod, why in (
        ("PIL", "截图与图像处理（必需）"),
        ("rapidocr", "OCR 识别（必需）"),
        ("onnxruntime", "OCR 推理后端（必需）"),
        ("ctranslate2", "Argos 离线翻译"),
        ("sentencepiece", "Argos 分词"),
        ("keyboard", "全局热键"),
        ("pymupdf", "PDF 附件解析"),
        ("docx", "Word 附件解析"),
        ("openpyxl", "Excel 附件解析"),
    ):
        try:
            __import__(mod)
            print(f"  {MARK_OK} {mod:<15} {why}")
        except Exception:
            print(f"  {MARK_NO} {mod:<15} {why}")

    print("\n[模型资源]")
    from winocr.services.ocr.rapidocr import RapidOcrEngine

    _probe = RapidOcrEngine()  # 复用统一判定（目录 + 包内）
    for tier in ("tiny", "small", "medium"):
        md = ocr_model_dir(tier)
        det, _rec, _cls = _probe._find_models(tier)
        has = det is not None
        mark = MARK_OK if has else MARK_NO
        extra = " (随包自带)" if tier == "small" and has else ""
        print(
            f"  {mark} OCR v6_{tier}: {md.name}{' [可用]' if has else ' [缺失]'}{extra}"
        )
    ocr_svc = app.services.get("ocr")
    if ocr_svc is not None:
        if isinstance(ocr_svc, RapidOcrEngine):
            eff = ocr_svc._effective_tier()
            print(
                f"     当前档位: {ocr_svc.model_type} "
                f"(实际生效: {eff}"
                + ("，small 随包自带" if eff == "small" else "")
                + ")"
            )
    found = [d for d in argos_search_dirs() if d.is_dir() and any(d.iterdir())]
    print(f"  Argos 包目录: {found[0] if found else '未找到（在线翻译仍可用）'}")

    disp = app.services.get("translate")
    print(f"\n可用翻译引擎: {', '.join(disp.available_engines()) if disp else '无'}")

    print("\n[场景胶囊]")
    if app.capsules:
        for name, cls in sorted(app.capsules.items()):
            flag = MARK_OK if getattr(cls, "enabled", True) else MARK_NO
            hot = getattr(cls, "hotkey", "") or "-"
            sec = getattr(cls, "ui_section", "") or "-"
            print(
                f"  {flag} {name:<20} {getattr(cls, 'display_name', '')}  "
                f"[热键 {hot}] [区 {sec}]"
            )
    else:
        print("  （无）")
    return 0


def cmd_models(args) -> int:
    from winocr.core.paths import argos_search_dirs, ocr_model_dir

    print(f"OCR 模型目录: {ocr_model_dir()}")
    if ocr_model_dir().is_dir():
        for f in sorted(ocr_model_dir().iterdir()):
            size = f.stat().st_size / 1024
            print(f"   - {f.name:<28} {size:,.0f} KB")
    else:
        print("   （缺失，首次识别时 rapidocr 会尝试使用其自带模型）")

    print("\nArgos 离线翻译包搜索路径（先找到先用）：")
    for d in argos_search_dirs():
        if d.is_dir():
            pkgs = [
                p.name for p in d.iterdir() if p.is_dir() or p.suffix == ".argosmodel"
            ]
            mark = f"[{len(pkgs)} 个]" if pkgs else "[空]"
            print(f"   {MARK_OK} {d}  {mark}")
            for p in pkgs[:10]:
                print(f"        - {p}")
        else:
            print(f"   {MARK_DOT} {d}  [不存在]")
    return 0


def cmd_config(args) -> int:
    from winocr.core.paths import config_path

    p = config_path()
    if args.init:
        cfg = AppConfig.load()
        path = cfg.save()
        print(f"配置已写入: {path}")
        return 0
    if not p.is_file():
        print(f"配置文件尚未创建: {p}")
        print("运行 `python main.py config --init` 生成一份默认配置。")
        return 0
    print(f"# {p}\n")
    print(p.read_text(encoding="utf-8"))
    return 0


def cmd_ocr(args) -> int:
    """命令行识别，便于脚本调用与回归测试。"""
    from winocr.core.types import Capture

    app = App().build()
    try:
        from PIL import Image

        img = Image.open(args.image)
        img.load()
    except Exception as e:
        print(f"[错误] 无法读取图片: {e}")
        return 2

    result = app.pipeline.ocr(Capture(image=img, source_path=args.image))
    print(result.text)
    if args.target:
        tr = app.pipeline.translate(result.text, args.target)
        print("\n--- 翻译 ---")
        print(tr.text)
        print(f"（引擎: {tr.engine}, 耗时 {tr.elapsed:.2f}s）", file=sys.stderr)
    return 0


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="winocr", description=f"WinOCR {__version__}")
    p.add_argument("-v", "--version", action="version", version=f"WinOCR {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("gui", help="启动图形界面（默认）").set_defaults(func=cmd_gui)
    sub.add_parser("console", help="无界面模式").set_defaults(func=cmd_console)
    sub.add_parser("doctor", help="自检：插件与依赖").set_defaults(func=cmd_doctor)
    sub.add_parser("models", help="查看模型资源").set_defaults(func=cmd_models)

    c = sub.add_parser("config", help="查看/生成配置文件")
    c.add_argument("--init", action="store_true", help="写出一份默认配置")
    c.set_defaults(func=cmd_config)

    o = sub.add_parser("ocr", help="命令行识别一张图片")
    o.add_argument("image", help="图片路径")
    o.add_argument("-t", "--target", help="顺便翻译成该语言，如 zh-CN / en")
    o.set_defaults(func=cmd_ocr)

    p.set_defaults(func=cmd_gui)
    return p


def main(argv=None) -> int:
    _init_console()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
