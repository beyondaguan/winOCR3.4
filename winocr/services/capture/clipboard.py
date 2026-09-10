# -*- coding: utf-8 -*-
"""捕获源：剪贴板（优先图片，其次文本）。"""
from __future__ import annotations

from .base import CaptureSource
from ...core.types import Capture


class ClipboardSource(CaptureSource):
    name = "clipboard"
    display_name = "剪贴板"

    def capture(self) -> Capture:
        img = self._grab_image()
        if img is not None:
            return Capture(kind="image", image=img, source_label="剪贴板图片")

        text = self._grab_text()
        if text:
            # 剪贴板里是文字：跳过 OCR 直接进翻译，比「先截图再识别」快得多
            return Capture(kind="text", text=text, source_label="剪贴板文本")
        return Capture(kind="empty", source_label="剪贴板为空")

    @staticmethod
    def _grab_image():
        try:
            from PIL import Image, ImageGrab
            data = ImageGrab.grabclipboard()
            if isinstance(data, Image.Image):
                return data
            # Windows 复制文件时返回路径列表，取第一张图片
            if isinstance(data, list) and data:
                first = str(data[0])
                if first.lower().endswith((".png", ".jpg", ".jpeg", ".bmp",
                                           ".gif", ".tiff", ".webp")):
                    # copy() 立即读入内存并释放文件句柄（延迟加载会占用文件）
                    with Image.open(first) as img:
                        return img.copy()
        except Exception:
            pass
        return None

    @staticmethod
    def _grab_text() -> str:
        """读剪贴板文本。

        2.0 硬依赖 pyperclip；这里改成三级回退，少一个第三方依赖也照样能用：
        pyperclip → Windows 原生 API → Tk 自带剪贴板。
        """
        try:
            import pyperclip
            return (pyperclip.paste() or "").strip()
        except Exception:
            pass
        try:
            text = _win_clipboard_text()
            if text:
                return text.strip()
        except Exception:
            pass
        try:
            import tkinter as tk
            r = tk.Tk()
            r.withdraw()
            try:
                return (r.clipboard_get() or "").strip()
            finally:
                r.destroy()
        except Exception:
            return ""


# ----------------------------------------------------------------------
# Win32 剪贴板：文本读取 + 完整备份/还原
# 划词翻译的「模拟 Ctrl+C」会覆盖用户剪贴板，必须能无损还原。
# 64 位下所有句柄型返回值都必须显式声明 restype，否则 ctypes 按 c_int
# 截断为 32 位（HGLOBAL / HHOOK 等都是 64 位指针，截断 = 访问无效内存）。
# ----------------------------------------------------------------------
def _u32() -> "object":
    import ctypes
    u32 = ctypes.windll.user32
    u32.OpenClipboard.restype = ctypes.c_int
    u32.GetClipboardData.restype = ctypes.c_void_p     # HGLOBAL 64 位指针
    u32.EnumClipboardFormats.restype = ctypes.c_uint
    u32.GetClipboardFormatNameW.restype = ctypes.c_int
    u32.RegisterClipboardFormatW.restype = ctypes.c_uint
    u32.SetClipboardData.restype = ctypes.c_void_p
    return u32


def _k32() -> "object":
    import ctypes
    k32 = ctypes.windll.kernel32
    # 64 位句柄必须同时声明 argtypes，否则 ctypes 默认按 c_int 传参，
    # 大地址句柄会报 "int too long to convert" 并导致剪贴板备份崩溃。
    HGLOBAL = ctypes.c_void_p
    k32.GlobalSize.argtypes = [HGLOBAL]
    k32.GlobalSize.restype = ctypes.c_size_t
    k32.GlobalLock.argtypes = [HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [HGLOBAL]
    k32.GlobalUnlock.restype = ctypes.c_int
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.c_void_p
    return k32


# 句柄型 GDI 格式不能按字节拷贝（还原需要真实对象句柄），跳过它们：
# 图片场景主流是 CF_DIB(8)/CF_DIBV5(17) 字节数据，不受影响。
_GDI_HANDLE_FORMATS = {2, 3, 14}          # CF_BITMAP / CF_METAFILEPICT / CF_ENHMETAFILE


def _win_clipboard_text() -> str:
    """Win32 读剪贴板纯文本（CF_UNICODETEXT），失败返回 ""。"""
    import ctypes
    u32 = _u32()
    k32 = _k32()
    if not u32.OpenClipboard(None):
        return ""
    try:
        handle = u32.GetClipboardData(13)             # CF_UNICODETEXT
        if not handle:
            return ""
        ptr = k32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value or ""
        finally:
            k32.GlobalUnlock(handle)
    finally:
        u32.CloseClipboard()


def backup_clipboard() -> dict:
    """完整备份剪贴板所有格式（含图片/HTML/自定义格式），供 restore_clipboard 还原。

    返回 ``{格式号: {"data": bytes, "name": str}}``；备份失败或空剪贴板返回 None。
    非文本剪贴板 Tk 读不到，模拟 Ctrl+C 会覆盖 —— 这是唯一能无损还原的路径。
    """
    import ctypes
    u32 = _u32()
    k32 = _k32()
    if not u32.OpenClipboard(None):
        return None
    try:
        fmts = {}
        fmt = 0
        while True:
            fmt = u32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _GDI_HANDLE_FORMATS:
                continue
            handle = u32.GetClipboardData(fmt)
            if not handle:
                continue
            size = k32.GlobalSize(handle)
            if not size:
                continue
            ptr = k32.GlobalLock(handle)
            if not ptr:
                continue
            try:
                data = ctypes.string_at(ptr, size)
            finally:
                k32.GlobalUnlock(handle)
            name = ""
            if fmt >= 0xC000:                          # 注册的自定义格式：记下名字才能还原
                buf = ctypes.create_unicode_buffer(256)
                if u32.GetClipboardFormatNameW(fmt, buf, 256):
                    name = buf.value
            fmts[fmt] = {"data": data, "name": name}
        return fmts or None
    finally:
        u32.CloseClipboard()


def clear_clipboard() -> bool:
    """清空剪贴板（EmptyClipboard）。取词注入前调用：让后续轮询只认新内容。"""
    try:
        u32 = _u32()
        if not u32.OpenClipboard(None):
            return False
        try:
            u32.EmptyClipboard()
            return True
        finally:
            u32.CloseClipboard()
    except Exception:
        return False


def restore_clipboard(fmts: dict) -> bool:
    """把 backup_clipboard() 的结果写回剪贴板。失败安静返回 False。"""
    if not fmts:
        return False
    try:
        import ctypes
        u32 = _u32()
        k32 = _k32()
        # 先对自定义格式注册拿回真实格式号（格式号在跨进程后会变）
        fmt_ids = {}
        for fmt, meta in fmts.items():
            if fmt >= 0xC000 and meta.get("name"):
                registered = u32.RegisterClipboardFormatW(meta["name"])
                if registered:
                    fmt_ids[fmt] = registered
            else:
                fmt_ids[fmt] = fmt
        if not u32.OpenClipboard(None):
            return False
        try:
            u32.EmptyClipboard()
            # 还原顺序：CF_UNICODETEXT 最后放，避免被后续格式顶掉（Windows 惯例）
            for fmt, meta in sorted(fmts.items(), key=lambda kv: 0 if kv[0] == 13 else 1):
                fid = fmt_ids.get(fmt, fmt)
                if fid == 0:
                    continue
                data = meta.get("data") or b""
                buf = ctypes.create_string_buffer(data, len(data) or 1)
                handle = k32.GlobalAlloc(0x0042, len(data) or 1)  # GMEM_MOVEABLE|GMEM_ZEROINIT
                if not handle:
                    continue
                ptr = k32.GlobalLock(handle)
                if ptr:
                    try:
                        ctypes.memmove(ptr, buf, len(data) or 1)
                    finally:
                        k32.GlobalUnlock(handle)
                # SetClipboardData 接管 handle 所有权，失败时需手动释放
                if not u32.SetClipboardData(fid, handle):
                    k32.GlobalFree(handle)
            return True
        finally:
            u32.CloseClipboard()
    except Exception:
        return False
