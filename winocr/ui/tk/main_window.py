# -*- coding: utf-8 -*-
"""主窗口 — 纯表现层。

它只负责「摆部件 + 显示结果」，一行业务逻辑都不写：
所有动作都转交给 TkUi（再由 TkUi 走 Pipeline），
所有结果都由 TkUi 通过事件总线 + after(0) 推回来。

这条界限是 2.0 最大的结构问题所在 —— 那边 UI 函数里直接调 OCR、
直接读写全局 _last_text、直接决定回退引擎，导致想换界面就得重写业务。
"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from . import theme
from .dialogs import (open_about, open_api_settings, open_history,
                      open_hotkey_settings, open_knowledge, open_plugins)
from .guards import ui_thread
from .project_bar import FlowFrame, ProjectTabBar, open_manage_projects
from .sticker import StickerWindow


class MainWindow:
    def __init__(self, ui) -> None:
        self.ui = ui
        self.app = ui.app
        self.root = ui.root

        self.last_text = ""            # 最近一次识别原文（AI 面板拿它当上下文）
        self.last_image = None         # 最近一次截图（AI 面板可选带上）
        self.chat = None
        self.chat_visible = False

        self._build_project_bar()
        self._build_merged_band()
        self._build_mode_bars()
        self._build_text_area()
        self._build_chat_area()
        self._build_status_bar()
        self._build_sticker()

        # 项目变更（增/切/关/改名/设译向/真删）后刷新标签栏 + 引擎标签
        self.app.projects.on_change(self._refresh_project_bar)

        self.apply_ui_mode(self.app.config.ui.mode)
        self.root.bind("<FocusIn>", lambda e: self._drop_topmost())
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass

    # ==================================================================
    # 构建
    # ==================================================================
    def _build_project_bar(self) -> None:
        """顶部项目标签页栏（默认 / 病历编码 / 房产调研 / +）。"""
        self.project_tab_bar = ProjectTabBar(self.root, self)
        self.project_tab_bar.frame.pack(fill=tk.X, padx=2, pady=(2, 0))

    def _build_merged_band(self) -> None:
        """合并窄带：替代原 info_bar + action_bar；分组按钮，窄窗整组换行。

        组：输入（截图识别蓝 / 粘贴 / 打开）、系统（历史/知识库/插件/关于/
        API/热键/管理项目）、信息（OCR/引擎标签 + 忙时进度条）。竖线分隔随组
        一起换行，避免窄窗时按钮被推出可视区。
        """
        band = FlowFrame(self.root, padx=6, pady=4)
        band.pack(fill=tk.X)
        self.merged_band = band

        # 组：输入（主操作保持 #1677ff 蓝）
        g_input = ttk.Frame(band)
        self.btn_snap = theme.accent_button(g_input, "📸 截图识别", self.ui.do_snap)
        self.btn_snap.pack(side=tk.LEFT, padx=2)
        self.btn_paste = ttk.Button(g_input, text="📋 粘贴图片", command=self.ui.do_clipboard)
        self.btn_paste.pack(side=tk.LEFT, padx=2)
        self.btn_file = ttk.Button(g_input, text="📂 打开文件", command=self.ui.do_open_file)
        self.btn_file.pack(side=tk.LEFT, padx=2)
        self.btn_mask = theme.accent_button(
            g_input, "🎭 蒙版翻译", lambda: self.ui.run_capsule("mask_translate"))
        self.btn_mask.pack(side=tk.LEFT, padx=2)
        band.add_group(g_input)

        # 组：系统
        g_sys = ttk.Frame(band)
        ttk.Separator(g_sys, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=(2, 6), fill=tk.Y)
        ttk.Button(g_sys, text="历史记录", command=lambda: open_history(self)).pack(side=tk.LEFT)
        ttk.Button(g_sys, text="知识库", command=lambda: open_knowledge(self)).pack(side=tk.LEFT)
        ttk.Button(g_sys, text="插件", command=lambda: open_plugins(self)).pack(side=tk.LEFT)
        ttk.Button(g_sys, text="关于", command=lambda: open_about(self)).pack(side=tk.LEFT)
        ttk.Button(g_sys, text="API 设置", command=lambda: open_api_settings(self)).pack(side=tk.LEFT)
        ttk.Button(g_sys, text="热键设置", command=lambda: open_hotkey_settings(self)).pack(side=tk.LEFT)
        ttk.Button(g_sys, text="管理项目", command=lambda: open_manage_projects(self)).pack(side=tk.LEFT)
        band.add_group(g_sys)

        # 组：信息（OCR/引擎标签 + 忙时进度条）
        g_info = ttk.Frame(band)
        ttk.Separator(g_info, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=(2, 6), fill=tk.Y)
        ocr = self.app.services.get("ocr")
        ocr_name = getattr(ocr, "display_name", "未配置") if ocr else "未配置"
        self.ocr_label = ttk.Label(g_info, text=f"OCR: {ocr_name}", foreground=theme.TEXT_MUTED)
        self.ocr_label.pack(side=tk.LEFT)
        self.engine_label = ttk.Label(g_info, text="", foreground=theme.TEXT_MUTED)
        self.engine_label.pack(side=tk.LEFT, padx=(12, 0))
        self.progress = ttk.Progressbar(g_info, mode="indeterminate", length=90)
        self.band_info_group = g_info
        band.add_group(g_info)

        self.refresh_engine_label()

    def _build_mode_bars(self) -> None:
        # ---- 简洁模式（整组换行）----
        sf = FlowFrame(self.root, padx=6, pady=3)
        self.simple_flow = sf

        g_out = ttk.Frame(sf)
        self.btn_sim_copy = ttk.Button(g_out, text="复制译文", command=self.copy_translation)
        self.btn_sim_copy.pack(side=tk.LEFT, padx=2)
        self.btn_sim_retry = ttk.Button(g_out, text="重新翻译", command=lambda: self.ui.do_translate())
        self.btn_sim_retry.pack(side=tk.LEFT, padx=2)
        self.btn_sim_read = ttk.Button(g_out, text="🔊 朗读", command=self.ui.do_tts_read)
        self.btn_sim_read.pack(side=tk.LEFT, padx=2)
        ttk.Button(g_out, text="存知识库", command=self.save_to_knowledge).pack(side=tk.LEFT, padx=2)
        sf.add_group(g_out)

        g_tool = ttk.Frame(sf)
        self.btn_sim_chat = ttk.Button(g_tool, text="AI 对话 ▼", command=self.toggle_chat)
        self.btn_sim_chat.pack(side=tk.LEFT, padx=2)
        self.btn_sim_clear = ttk.Button(g_tool, text="清空", command=self.clear_all)
        self.btn_sim_clear.pack(side=tk.LEFT, padx=2)
        self.btn_sim_hot = ttk.Button(g_tool, text="热键设置", command=lambda: open_hotkey_settings(self))
        self.btn_sim_hot.pack(side=tk.LEFT, padx=2)
        self.btn_sim_adv = ttk.Button(g_tool, text="高级 ▼", command=lambda: self.switch_ui_mode("advanced"))
        self.btn_sim_adv.pack(side=tk.LEFT, padx=(8, 2))
        sf.add_group(g_tool)

        # ---- 高级模式（整组换行）----
        af = FlowFrame(self.root, padx=6, pady=3)
        self.advanced_flow = af

        g_out2 = ttk.Frame(af)
        self.btn_copy_orig = ttk.Button(g_out2, text="复制原文", command=self.copy_original)
        self.btn_copy_orig.pack(side=tk.LEFT, padx=2)
        self.btn_copy_trans = ttk.Button(g_out2, text="复制译文", command=self.copy_translation)
        self.btn_copy_trans.pack(side=tk.LEFT, padx=2)
        self.btn_copy_all = ttk.Button(g_out2, text="复制全部", command=self.copy_all)
        self.btn_copy_all.pack(side=tk.LEFT, padx=2)
        af.add_group(g_out2)

        g_tr = ttk.Frame(af)
        self.btn_zh = ttk.Button(g_tr, text="译中", command=lambda: self.ui.do_translate("zh-CN"))
        self.btn_zh.pack(side=tk.LEFT, padx=2)
        self.btn_en = ttk.Button(g_tr, text="译英", command=lambda: self.ui.do_translate("en"))
        self.btn_en.pack(side=tk.LEFT, padx=2)
        self.btn_cycle = ttk.Button(g_tr, text="切换引擎", command=self.ui.do_switch_engine)
        self.btn_cycle.pack(side=tk.LEFT, padx=2)
        af.add_group(g_tr)

        g_tool2 = ttk.Frame(af)
        self.btn_adv_clear = ttk.Button(g_tool2, text="清场", command=self.clear_all)
        self.btn_adv_clear.pack(side=tk.LEFT, padx=2)
        self.btn_adv_read = ttk.Button(g_tool2, text="🔊 朗读", command=self.ui.do_tts_read)
        self.btn_adv_read.pack(side=tk.LEFT, padx=2)
        self.btn_adv_chat = ttk.Button(g_tool2, text="AI 对话 ▼", command=self.toggle_chat)
        self.btn_adv_chat.pack(side=tk.LEFT, padx=2)
        ttk.Button(g_tool2, text="存知识库", command=self.save_to_knowledge).pack(side=tk.LEFT, padx=2)
        self.btn_adv_simple = ttk.Button(g_tool2, text="简洁 ▲", command=lambda: self.switch_ui_mode("simple"))
        self.btn_adv_simple.pack(side=tk.LEFT, padx=(8, 2))
        af.add_group(g_tool2)

    def _build_text_area(self) -> None:
        self.main_pane = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        self.main_pane.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        area = ttk.Frame(self.main_pane)
        pane = ttk.PanedWindow(area, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)

        # 深色模式下 tk.Text 默认白底黑字，必须显式跟随调色板（ttk 管不到它）
        _txt_colors = dict(bg=theme.INPUT_BG, fg=theme.TEXT_MAIN,
                           insertbackground=theme.TEXT_MAIN,
                           selectbackground=theme.ACCENT, selectforeground="white")

        f1 = ttk.LabelFrame(pane, text="原文", padding=4)
        self.txt_original = tk.Text(f1, wrap=tk.WORD, font=theme.MONO_FONT,
                                    undo=True, relief=tk.FLAT,
                                    highlightthickness=1, highlightbackground=theme.BORDER,
                                    **_txt_colors)
        sb1 = ttk.Scrollbar(f1, orient=tk.VERTICAL, command=self.txt_original.yview)
        self.txt_original.configure(yscrollcommand=sb1.set)
        sb1.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_original.pack(fill=tk.BOTH, expand=True)
        pane.add(f1, weight=1)

        # 译文区同样可编辑：OCR 难免有错字、机翻难免有生硬处，
        # 让人当场改掉再复制走，比「只读 + 粘到别处再改」顺手得多。
        self.trans_frame = ttk.LabelFrame(pane, text="译文（可编辑）", padding=4)
        self.txt_translated = tk.Text(self.trans_frame, wrap=tk.WORD, font=theme.UI_FONT,
                                      undo=True, relief=tk.FLAT,
                                      highlightthickness=1, highlightbackground=theme.BORDER,
                                      **_txt_colors)
        sb2 = ttk.Scrollbar(self.trans_frame, orient=tk.VERTICAL,
                            command=self.txt_translated.yview)
        self.txt_translated.configure(yscrollcommand=sb2.set)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_translated.pack(fill=tk.BOTH, expand=True)
        pane.add(self.trans_frame, weight=1)

        for box in (self.txt_original, self.txt_translated):
            self._enable_editing(box)

        self.main_pane.add(area, weight=3)
        self.text_area = area

    # ---- 让 tk.Text 具备一个「编辑框该有的样子」 ----
    def _enable_editing(self, box: tk.Text) -> None:
        """Tk 的 Text 默认缺 Ctrl+A / 右键菜单，中文用户会以为控件坏了。"""
        menu = tk.Menu(box, tearoff=0)
        menu.add_command(label="剪切", command=lambda: self._edit_event(box, "<<Cut>>"))
        menu.add_command(label="复制", command=lambda: self._edit_event(box, "<<Copy>>"))
        menu.add_command(label="粘贴", command=lambda: self._edit_event(box, "<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="全选", command=lambda: self._select_all(box))
        menu.add_command(label="清空", command=lambda: box.delete("1.0", tk.END))
        menu.add_separator()
        menu.add_command(label="翻译选中内容",
                         command=lambda: self.ui.do_translate())

        def _popup(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return "break"

        box.bind("<Button-3>", _popup)
        box.bind("<Control-a>", lambda e: self._select_all(box))
        box.bind("<Control-A>", lambda e: self._select_all(box))

    @staticmethod
    def _edit_event(box: tk.Text, virtual: str) -> None:
        try:
            box.event_generate(virtual)
        except Exception:
            pass

    @staticmethod
    def _select_all(box: tk.Text) -> str:
        box.tag_add(tk.SEL, "1.0", tk.END)
        box.mark_set(tk.INSERT, "1.0")
        box.see(tk.INSERT)
        return "break"

    def _build_chat_area(self) -> None:
        """对话面板先构建、后隐藏 —— 构建即显示会把原文区压成几十像素（2.0 踩过）。"""
        from .chat_panel import ChatPanel
        self.chat = ChatPanel(self.main_pane, self.ui, self)
        self.chat_visible = False

    def _build_status_bar(self) -> None:
        self.status_label = ttk.Label(self.root, text="就绪", foreground=theme.TEXT_MUTED,
                                      padding=(8, 3), anchor=tk.W)
        self.status_label.pack(fill=tk.X, side=tk.BOTTOM)

    # ==================================================================
    # TkUi 约定的回调接口（全部只在主线程执行）
    # ==================================================================
    @ui_thread
    def set_status(self, text: str) -> None:
        try:
            self.status_label.config(text=text)
        except Exception:
            pass

    @ui_thread
    def show_original(self, text: str) -> None:
        self.last_text = text or ""
        try:
            self.txt_original.delete("1.0", tk.END)
            self.txt_original.insert("1.0", self.last_text)
            self.txt_original.edit_reset()
        except Exception:
            pass

    @ui_thread
    def show_translation(self, result) -> None:
        text = getattr(result, "text", "") or ""
        engine = getattr(result, "engine", "")
        target = getattr(result, "target_lang", "") or ""
        try:
            self.txt_translated.delete("1.0", tk.END)
            self.txt_translated.insert("1.0", text)
            self.txt_translated.edit_reset()      # 新译文另起一段撤销历史
        except Exception:
            return
        # 标题跟着实际译出的语种走，一眼能看出现在是中还是英
        try:
            label = f"译文 · {self.lang_label(target)}（可编辑）" if target else "译文（可编辑）"
            self.trans_frame.config(text=label)
        except Exception:
            pass
        if engine.startswith("(全部失败)"):
            self.set_status(f"翻译失败，已显示原文 — {engine[7:]}")
        elif engine:
            disp = self._engine_display(engine)
            elapsed = getattr(result, "elapsed", 0) or 0
            self.set_status(f"翻译完成 — {disp} · {elapsed:.1f}s")

    @ui_thread
    def set_busy(self, busy: bool) -> None:
        theme.set_button_enabled(self.btn_snap, not busy)
        for b in (self.btn_paste, self.btn_file):
            try:
                b.config(state=tk.DISABLED if busy else tk.NORMAL)
            except Exception:
                pass
        try:
            if busy:
                self.progress.pack(side=tk.RIGHT, padx=6)
                self.progress.start(12)
            else:
                self.progress.stop()
                self.progress.pack_forget()
            self.merged_band.relayout()      # 进度条出现/消失会改变信息组宽度
        except Exception:
            pass

    def refresh_engine_label(self) -> None:
        cfg = self.app.config.translate
        if cfg.engine == "auto":
            disp = "自动回退链"
        else:
            disp = self._engine_display(cfg.engine)
        target = self.lang_label(cfg.target)
        try:
            self.engine_label.config(text=f"翻译: {disp} → {target}")
        except Exception:
            pass

    def get_original(self) -> str:
        try:
            return self.txt_original.get("1.0", "end-1c")
        except Exception:
            return self.last_text

    def get_translation(self) -> str:
        try:
            return self.txt_translated.get("1.0", "end-1c")
        except Exception:
            return ""

    def get_selected_text(self) -> str:
        """返回原文/译文区里被选中的文字（没有则空串）。

        两个框都可编辑，谁有选区就用谁的 —— 让「只翻这一句」变成顺手的事。
        """
        for box in (self.txt_original, self.txt_translated):
            try:
                if box.tag_ranges(tk.SEL):
                    return box.get(tk.SEL_FIRST, tk.SEL_LAST).strip()
            except Exception:
                continue
        return ""

    # ---- 划词小贴条：选区/划词结果的一个独立悬浮窗 ----
    def _build_sticker(self) -> None:
        self.sticker = StickerWindow(self.root, ui=self.ui)

    @ui_thread
    def show_sticker(self, original: str, translation: str = "") -> None:
        """把原文+译文弹到划词小贴条（不抢主窗口焦点）。"""
        try:
            self.sticker.show(original or "", translation or "")
        except Exception as e:
            # 不再静默吞掉：写进 selection.log 便于远程定位（图贴卡死时关键线索）
            try:
                from .app import _sel_log_static
                _sel_log_static("show_sticker error: %r" % (e,), logging.ERROR)
            except Exception:
                pass
        else:
            try:
                from .app import _sel_log_static
                _sel_log_static(
                    "show_sticker ok orig=%d tran=%d"
                    % (len(original or ""), len(translation or "")), logging.DEBUG)
            except Exception:
                pass

    @staticmethod
    def lang_label(code: str) -> str:
        from ...core.types import Lang
        return Lang.label(Lang.normalize(code))

    # ==================================================================
    # 界面动作
    # ==================================================================
    def switch_ui_mode(self, mode: str) -> None:
        self.app.config.ui.mode = mode
        self.apply_ui_mode(mode)
        try:
            self.app.config.save()        # 单一 TOML，不再改写可执行的 .py
        except Exception as e:
            self.set_status(f"界面模式保存失败: {e}")

    def apply_ui_mode(self, mode: str) -> None:
        if mode == "advanced":
            self.simple_flow.pack_forget()
            self.advanced_flow.pack(fill=tk.X, after=self.merged_band)
        else:
            self.advanced_flow.pack_forget()
            self.simple_flow.pack(fill=tk.X, after=self.merged_band)
        # 切换栏后兜底重算换行（窗口尺寸未变也可能触发，这里确保布局正确）
        self.root.after(10, self._relayout_bands)

    def _relayout_bands(self) -> None:
        """让所有 FlowFrame 重新计算整组换行（窗口缩放/栏目切换后调用）。"""
        for f in (self.merged_band, self.simple_flow, self.advanced_flow):
            try:
                f.relayout()
            except Exception:
                pass

    @ui_thread
    def _refresh_project_bar(self) -> None:
        """项目变更回调（ProjectManager.on_change）：刷新标签栏高亮 + 引擎标签。"""
        try:
            self.project_tab_bar.refresh()
        except Exception:
            pass
        try:
            self.refresh_engine_label()
        except Exception:
            pass

    def toggle_chat(self) -> None:
        if self.chat is None:
            return
        if self.chat_visible:
            try:
                self.main_pane.forget(self.chat.frame)
            except Exception:
                pass
            self.chat_visible = False
        else:
            try:
                self.main_pane.add(self.chat.frame, weight=2)
            except Exception:
                return
            self.chat_visible = True
            self._ensure_chat_room()
            self.chat.focus_input()
        self._sync_chat_labels()

    def _sync_chat_labels(self) -> None:
        arrow = "▲" if self.chat_visible else "▼"
        for btn, tpl in ((self.btn_sim_chat, "AI 对话 {}"),
                         (self.btn_adv_chat, "AI 对话 {}")):
            try:
                btn.config(text=tpl.format(arrow))
            except Exception:
                pass

    def _ensure_chat_room(self) -> None:
        """展开面板时按需增高窗口，避免原文区与对话区互相挤压。"""
        try:
            self.root.update_idletasks()
            overhead = self.root.winfo_height() - self.main_pane.winfo_height()
            need = theme.TEXT_AREA_MIN_H + theme.CHAT_PANEL_MIN_H + max(overhead, 0)
            cur = self.root.winfo_height()
            if cur >= need:
                return
            new_h = min(need, int(self.root.winfo_screenheight() * 0.9))
            if new_h > cur:
                self.root.geometry(f"{self.root.winfo_width()}x{new_h}")
        except Exception:
            pass

    def clear_all(self) -> None:
        self.last_text = ""
        self.last_image = None
        try:
            self.txt_original.delete("1.0", tk.END)
            self.txt_translated.delete("1.0", tk.END)
            self.trans_frame.config(text="译文（可编辑）")
        except Exception:
            pass
        self.set_status("已清空")

    def save_to_knowledge(self) -> None:
        """把当前原文/译文沉淀进本地知识库（「同一批文档反复回看」的写入入口）。"""
        ocr = self.get_original().strip()
        tr = self.get_translation().strip()
        if not (ocr or tr):
            self.set_status("没有可保存的内容")
            return
        try:
            rec = {"source_type": "manual", "scene": "",
                   "ocr_text": ocr, "translate_text": tr}
            rid = self.app.pipeline.save_knowledge(rec)
            self.set_status(f"已保存到知识库（ID={rid}）")
        except Exception as e:
            self.set_status(f"保存失败: {e}")

    # ---- 剪贴板：用 Tk 自带能力，不依赖 pyperclip ----
    def _copy(self, text: str, what: str) -> None:
        if not text.strip():
            self.set_status(f"没有可复制的{what}")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()          # 保证退出后剪贴板内容仍在
            self.set_status(f"已复制{what}（{len(text)} 字）")
        except Exception as e:
            self.set_status(f"复制失败: {e}")

    def copy_original(self) -> None:
        self._copy(self.get_original(), "原文")

    def copy_translation(self) -> None:
        self._copy(self.get_translation(), "译文")

    def copy_all(self) -> None:
        self._copy(f"{self.get_original()}\n\n--- 翻译 ---\n{self.get_translation()}", "全文")

    # ==================================================================
    def _engine_display(self, name: str) -> str:
        disp = self.app.services.get("translate")
        return disp.engine_display(name) if disp else name

    def _drop_topmost(self) -> None:
        """拿到焦点后取消置顶，方便正常切窗口（沿用 2.0 的体验）。"""
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass
