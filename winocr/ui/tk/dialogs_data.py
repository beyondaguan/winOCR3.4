# -*- coding: utf-8 -*-
"""数据面板：知识库 / 历史记录 / 导入。

从 dialogs.py 拆分而来。
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import theme
from .dialogs_common import _post_to_ui, _center, _modal, _ScrollableFrame

_IMPORT_FIELD_HINTS = """【WinOCR 知识库导入模板 · 字段说明】

每行一条知识（原文/译文/AI 解读至少填一个，全空行自动跳过）。
「id」留空即可（自动编号）；「created_at」留空 = 当前时间。

字段名（英文，保持表头不改）：
  source_type    来源类型：manual / screenshot / clipboard / file / chat（可自定）
  source_path    来源文件路径（文件类才有，可留空）
  image_hash     原图哈希（溯源用，可留空）
  scene          场景标签：如「病历」「房产调研」（老版本字段叫 scenario，已兼容）
  tags           标签（可留空）
  note           备注（可留空）
  ocr_text       原文（OCR 识别文本，**必填之一**）
  translate_text 译文（**必填之一**）
  ai_explanation AI 解读（可留空）
  created_at     时间，格式 2026-08-24 10:00:00（留空=当前）

使用：
  1. 用 Excel / WPS 打开 .csv 模板，删掉示例行，按列填你的数据；
  2. 按 Ctrl+Shift+I（或知识库面板「导入」）→ 选目标项目 → 选择这个文件；
  3. 导入完成后在知识库面板检索验证。
"""



def ensure_import_templates() -> list:
    """在固定模板目录生成导入模板（CSV + JSON + 说明），幂等：已存在不覆盖。

    返回生成的模板文件路径列表。首次调用创建，之后直接返回既有文件。
    """
    from ...core.paths import import_templates_dir
    d = import_templates_dir()
    made = []

    csv_p = d / "知识库导入模板.csv"
    if not csv_p.exists():
        csv_text = (
            "id,created_at,source_type,source_path,image_hash,scene,tags,note,"
            "ocr_text,translate_text,ai_explanation\n"
            ',"2026-08-24 10:00:00",manual,"","","病历","ICD-10","",'
            '"肌酐偏高 肾功能检查","creatinine high","建议复查"\n'
            ',"",manual,"","","房产调研","","","梅东路大院 1992 年 无电梯",'
            '"Meidong Road compound built 1992 no elevator",""\n'
        )
        try:
            with open(csv_p, "w", encoding="utf-8-sig", newline="") as f:
                f.write(csv_text)
            made.append(csv_p)
        except OSError:
            pass

    json_p = d / "知识库导入模板.json"
    if not json_p.exists():
        import json
        sample = [{
            "id": 0, "created_at": "2026-08-24 10:00:00",
            "source_type": "manual", "source_path": "", "image_hash": "",
            "scene": "病历", "scenario": "", "tags": "ICD-10", "note": "",
            "ocr_text": "肌酐偏高 肾功能检查",
            "translate_text": "creatinine high",
            "ai_explanation": "建议复查",
        }]
        try:
            with open(json_p, "w", encoding="utf-8") as f:
                json.dump(sample, f, ensure_ascii=False, indent=2)
            made.append(json_p)
        except OSError:
            pass

    readme_p = d / "导入模板说明.txt"
    if not readme_p.exists():
        try:
            with open(readme_p, "w", encoding="utf-8") as f:
                f.write(_IMPORT_FIELD_HINTS)
            made.append(readme_p)
        except OSError:
            pass

    return made


def _run_import(window, path: str, project: str, status_callback=None) -> None:
    """按扩展名分发导入（json / csv），后台线程执行，完成后回写状态。"""
    app = window.app
    kb = app.services.get("knowledge")
    if kb is None:
        try:
            messagebox.showinfo("导入知识库", "知识库服务未初始化。",
                                parent=window.root)
        except Exception:
            pass
        return

    def _post_status(text):
        try:
            if status_callback is not None:
                status_callback(text)
            else:
                window.set_status(text)
        except Exception:
            pass

    _post_to_ui(app, lambda: _post_status("导入中…"))
    ext = os.path.splitext(path)[1].lower() if isinstance(path, str) else ""

    def _work():
        try:
            if ext == ".csv":
                ok, skipped = kb.import_csv(path, project=project)
            else:
                import json
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data = data.get("records", [])
                if not isinstance(data, list):
                    data = [data]
                ok, skipped = kb.import_records(data, project=project)
        except Exception as e:
            _post_to_ui(app, lambda: _post_status(f"导入失败: {e}"))
            return
        _post_to_ui(app, lambda: _post_status(
            f"已导入 {ok} 条（跳过 {skipped} 条空记录）→ {project}"))

    threading.Thread(target=_work, daemon=True, name="kb-import").start()


def open_import_dialog(window, status_callback=None) -> None:
    """导入引导窗口：选目标项目 → 打开模板文件夹 / 选 JSON / 选 CSV 导入。

    固定模板目录由 ``ensure_import_templates`` 自动生成（幂等）。
    被知识库面板「导入」按钮和全局热键「导入知识库」共同调用。
    """
    app = window.app
    root = window.root

    ensure_import_templates()
    from ...core.paths import import_templates_dir
    tpl_dir = import_templates_dir()

    win = tk.Toplevel(root)
    win.title("导入知识库")
    win.resizable(False, False)
    win.transient(root)
    win.grab_set()

    body = ttk.Frame(win, padding=14)
    body.pack(fill=tk.BOTH, expand=True)

    ttk.Label(body, text="导入知识库", font=theme.TITLE_FONT).pack(anchor=tk.W)
    ttk.Label(body, text="选择目标项目，再选一个数据文件（JSON / CSV）。",
              foreground=theme.TEXT_MUTED).pack(anchor=tk.W, pady=(2, 10))

    # ---- 目标项目 ----
    ttk.Label(body, text="导入到项目").pack(anchor=tk.W)
    pm = app.projects
    projs = pm.config.projects
    cur = pm.current_id
    opts = []
    cur_idx = 0
    for i, p in enumerate(projs):
        mark = "（当前）" if p.id == cur else ""
        if not p.open:
            mark += "（已关闭）"
        opts.append(f"{p.name} — {p.id}{mark}")
        if p.id == cur:
            cur_idx = i
    pid_var = tk.StringVar(value=opts[cur_idx] if opts else "")
    ttk.Combobox(body, textvariable=pid_var, state="readonly", width=34,
                 values=opts).pack(anchor=tk.W, pady=(2, 10))

    # ---- 模板提示 ----
    tpl_box = ttk.LabelFrame(body, text="导入模板（系统自动生成，可编辑）",
                             padding=8)
    tpl_box.pack(fill=tk.X, pady=(0, 10))
    ttk.Label(tpl_box, text=f"目录：{tpl_dir}",
              foreground=theme.TEXT_MUTED, wraplength=360).pack(anchor=tk.W)

    def _open_tpl_dir():
        try:
            import os
            os.startfile(str(tpl_dir))
        except Exception as e:
            messagebox.showwarning("打开失败", str(e), parent=win)

    bar1 = ttk.Frame(tpl_box)
    bar1.pack(fill=tk.X, pady=(6, 0))
    ttk.Button(bar1, text="📂 打开模板文件夹", command=_open_tpl_dir).pack(side=tk.LEFT)
    ttk.Label(bar1, text="用 WPS/Excel 编辑 CSV 模板，或参考 JSON 模板结构",
              foreground=theme.TEXT_MUTED).pack(side=tk.LEFT, padx=(8, 0))

    # ---- 动作 ----
    def _pick(ext_filter, title):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=win, title=title,
            filetypes=[(f"{ext_filter.upper()} 文件", f"*.{ext_filter}"),
                       ("所有文件", "*.*")])
        return path

    def _do_import(ext):
        path = _pick(ext, f"选择要导入的 {ext.upper()} 文件")
        if not path:
            return
        pid = None
        val = pid_var.get().strip()
        for i, p in enumerate(projs):
            if opts[i] == val:
                pid = p.id
                break
        if pid is None:
            pid = cur
        win.destroy()
        _run_import(window, path, pid, status_callback)

    bar = ttk.Frame(win, padding=(14, 0, 14, 14))
    bar.pack(fill=tk.X, side=tk.BOTTOM)
    theme.accent_button(bar, "导入 JSON…", lambda: _do_import("json")).pack(side=tk.RIGHT)
    ttk.Button(bar, text="导入 CSV…", command=lambda: _do_import("csv")
               ).pack(side=tk.RIGHT, padx=6)
    ttk.Button(bar, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=2)

    _center(win, root)


def open_knowledge(window) -> None:
    """知识库管理：检索（FTS5+LIKE 兜底）、列表回看、删除、导出三种格式、
    导入 JSON（可指定目标项目）。

    底层由 KnowledgeBase（sqlite3 + FTS5）驱动；本对话框只做表现层，
    所有操作都在后台线程执行后回写 Tk（项目铁律：非主线程不碰 Tk）。
    """
    app = window.app
    kb = app.services.get("knowledge")
    if kb is None:
        messagebox.showinfo("知识库", "知识库服务未初始化，无法打开。", parent=window.root)
        return

    win = tk.Toplevel(window.root)
    win.title("知识库")
    win.geometry("760x520")
    win.minsize(620, 380)
    win.transient(window.root)

    # ---- 顶部：检索框 + 动作按钮 ----
    top = ttk.Frame(win, padding=(10, 8))
    top.pack(fill=tk.X)
    q = tk.StringVar()
    ttk.Label(top, text="检索：").pack(side=tk.LEFT)
    ent = ttk.Entry(top, textvariable=q, width=32)
    ent.pack(side=tk.LEFT, padx=(0, 6))
    ent.bind("<Return>", lambda e: _load())
    ttk.Button(top, text="搜索", command=lambda: _load()).pack(side=tk.LEFT, padx=2)
    ttk.Button(top, text="全部", command=lambda: (q.set(""), _load())).pack(side=tk.LEFT, padx=2)
    show_all_proj = tk.BooleanVar(value=True)
    ttk.Checkbutton(top, text="所有项目", variable=show_all_proj,
                    command=lambda: _load()).pack(side=tk.LEFT, padx=2)
    ttk.Button(top, text="导出 JSON", command=lambda: _export("json")).pack(side=tk.RIGHT, padx=2)
    ttk.Button(top, text="导出 MD", command=lambda: _export("md")).pack(side=tk.RIGHT, padx=2)
    ttk.Button(top, text="导出 CSV", command=lambda: _export("csv")).pack(side=tk.RIGHT, padx=2)
    ttk.Button(top, text="导入", command=lambda: open_import_dialog(
        window, status_callback=_set_status)).pack(side=tk.RIGHT, padx=2)

    # ---- 中部：列表 + 详情预览 ----
    body = ttk.PanedWindow(win, orient=tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

    left = ttk.LabelFrame(body, text="记录", padding=4)
    cols = ("id", "created_at", "source_type", "scene", "snippet")
    tree = ttk.Treeview(left, columns=cols, show="headings", height=14)
    tree.heading("id", text="ID")
    tree.heading("created_at", text="时间")
    tree.heading("source_type", text="来源")
    tree.heading("scene", text="场景")
    tree.heading("snippet", text="内容摘要")
    tree.column("id", width=40, anchor=tk.E)
    tree.column("created_at", width=120, anchor=tk.W)
    tree.column("source_type", width=80, anchor=tk.W)
    tree.column("scene", width=60, anchor=tk.W)
    tree.column("snippet", width=280, anchor=tk.W)
    vsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(fill=tk.BOTH, expand=True)
    body.add(left, weight=3)

    right = ttk.LabelFrame(body, text="详情（原文 / 译文 / AI 解读）", padding=4)
    detail = tk.Text(right, wrap=tk.WORD, font=theme.UI_FONT_SMALL, relief=tk.FLAT,
                     bg=theme.CANVAS_BG, state=tk.DISABLED)
    dvsb = ttk.Scrollbar(right, orient=tk.VERTICAL, command=detail.yview)
    detail.configure(yscrollcommand=dvsb.set)
    dvsb.pack(side=tk.RIGHT, fill=tk.Y)
    detail.pack(fill=tk.BOTH, expand=True)
    body.add(right, weight=2)

    # ---- 底部：状态 + 删除 ----
    bar = ttk.Frame(win, padding=(10, 0, 10, 10))
    bar.pack(fill=tk.X)
    ttk.Button(bar, text="删除选中", command=lambda: _delete()).pack(side=tk.RIGHT)
    status = ttk.Label(bar, text="", foreground=theme.TEXT_MUTED)
    status.pack(side=tk.LEFT)

    _records = []          # 当前列表对应的记录（id → KnowledgeRecord）

    def _set_status(text: str) -> None:
        try:
            status.config(text=text)
        except Exception:
            pass

    def _show_detail(rec) -> None:
        detail.config(state=tk.NORMAL)
        detail.delete("1.0", tk.END)
        detail.insert("1.0",
                      f"【原文】\n{rec.ocr_text or '（无）'}\n\n"
                      f"【译文】\n{rec.translate_text or '（无）'}\n\n"
                      f"【AI 解读】\n{rec.ai_explanation or '（无）'}\n\n"
                      f"来源：{rec.source_type or '-'}  场景：{rec.scene or '-'}\n"
                      f"时间：{rec.created_at}  ID：{rec.id}")
        detail.config(state=tk.DISABLED)

    def _load() -> None:
        """后台检索 → 回写列表（不阻塞 UI）。"""
        query = q.get().strip()
        _set_status("检索中…")
        proj = "*" if show_all_proj.get() else None

        def _work():
            try:
                rows = kb.search(query, limit=200, project=proj)
            except Exception as e:
                rows = []
                err = str(e)
                _post_to_ui(app, lambda: _set_status(f"检索失败: {err}"))
                return
            _post_to_ui(app, lambda: _fill(rows, query))

        threading.Thread(target=_work, daemon=True, name="kb-search").start()

    def _fill(rows, query: str) -> None:
        for it in tree.get_children():
            tree.delete(it)
        _records[:] = rows        # 就地替换闭包列表（勿用 global，否则详情读到空）
        for i, r in enumerate(_records):
            snippet = (r.ocr_text or r.translate_text or r.ai_explanation or "").replace(
                "\n", " ").strip()
            if len(snippet) > 60:
                snippet = snippet[:60] + "…"
            tree.insert("", tk.END, iid=str(i), values=(
                r.id, r.created_at, r.source_type, r.scene, snippet))
        _set_status(f"共 {len(_records)} 条" + (f"（检索词：{query}）" if query else ""))

    def _delete() -> None:
        sel = tree.selection()
        if not sel:
            _set_status("请先在列表中选择要删除的记录")
            return
        idx = int(sel[0])
        if not (0 <= idx < len(_records)):
            return
        rid = _records[idx].id
        if not messagebox.askyesno("删除", f"确定删除 ID={rid} 这条知识吗？", parent=win):
            return
        try:
            kb.delete(rid)
            _set_status(f"已删除 ID={rid}")
        except Exception as e:
            _set_status(f"删除失败: {e}")
        _load()

    def _export(fmt: str) -> None:
        from tkinter import filedialog
        ext = {"json": "*.json", "md": "*.md", "csv": "*.csv"}[fmt]
        path = filedialog.asksaveasfilename(
            parent=win, title=f"导出知识库（{fmt.upper()}）",
            defaultextension=f".{fmt}", filetypes=[(fmt.upper(), ext)])
        if not path:
            return
        _set_status("导出中…")

        def _work():
            try:
                text = kb.export(fmt, q.get().strip(),
                                 project=("*" if show_all_proj.get() else None))
            except Exception as e:
                _post_to_ui(app, lambda: _set_status(f"导出失败: {e}"))
                return
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception as e:
                _post_to_ui(app, lambda: _set_status(f"写入失败: {e}"))
                return
            _post_to_ui(app, lambda: _set_status(f"已导出 {len(text)} 字符 → {path}"))

        threading.Thread(target=_work, daemon=True, name="kb-export").start()

    tree.bind("<<TreeviewSelect>>", lambda e: _show_detail(_records[int(tree.selection()[0])])
              if tree.selection() else None)

    _load()
    _center(win, window.root)


def open_history(window) -> None:
    """历史记录浏览：检索、列表回看、详情、复制原文/译文、导出 MD·TXT、清空。

    底层由 JsonHistory（原子写 JSON，上限 500 条）驱动；本对话框只做表现层，
    所有读取/导出都在后台线程执行后回写 Tk（项目铁律：非主线程不碰 Tk）。
    划词翻译与截图翻译均会写入历史（见 pipeline.record）。
    """
    app = window.app
    store = app.services.get("persistence")
    if store is None:
        messagebox.showinfo("历史记录", "历史记录服务未初始化，无法打开。", parent=window.root)
        return

    win = tk.Toplevel(window.root)
    win.title("历史记录")
    win.geometry("760x520")
    win.minsize(620, 380)
    win.transient(window.root)

    # ---- 顶部：检索框 + 导出 ----
    top = ttk.Frame(win, padding=(10, 8))
    top.pack(fill=tk.X)
    q = tk.StringVar()
    ttk.Label(top, text="检索：").pack(side=tk.LEFT)
    ent = ttk.Entry(top, textvariable=q, width=32)
    ent.pack(side=tk.LEFT, padx=(0, 6))
    ent.bind("<Return>", lambda e: _load())
    ttk.Button(top, text="搜索", command=lambda: _load()).pack(side=tk.LEFT, padx=2)
    ttk.Button(top, text="全部", command=lambda: (q.set(""), _load())).pack(side=tk.LEFT, padx=2)
    ttk.Button(top, text="导出 MD", command=lambda: _export("md")).pack(side=tk.RIGHT, padx=2)
    ttk.Button(top, text="导出 TXT", command=lambda: _export("txt")).pack(side=tk.RIGHT, padx=2)

    # ---- 中部：列表 + 详情 ----
    body = ttk.PanedWindow(win, orient=tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

    left = ttk.LabelFrame(body, text="记录（新 → 旧）", padding=4)
    cols = ("time", "orig")
    tree = ttk.Treeview(left, columns=cols, show="headings", height=14)
    tree.heading("time", text="时间")
    tree.heading("orig", text="原文摘要")
    tree.column("time", width=140, anchor=tk.W)
    tree.column("orig", width=300, anchor=tk.W)
    vsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(fill=tk.BOTH, expand=True)
    body.add(left, weight=3)

    right = ttk.LabelFrame(body, text="详情（原文 / 译文）", padding=4)
    detail = tk.Text(right, wrap=tk.WORD, font=theme.UI_FONT_SMALL, relief=tk.FLAT,
                     bg=theme.CANVAS_BG, state=tk.DISABLED)
    dvsb = ttk.Scrollbar(right, orient=tk.VERTICAL, command=detail.yview)
    detail.configure(yscrollcommand=dvsb.set)
    dvsb.pack(side=tk.RIGHT, fill=tk.Y)
    detail.pack(fill=tk.BOTH, expand=True)
    body.add(right, weight=2)

    # ---- 底部：复制 + 清空 ----
    bar = ttk.Frame(win, padding=(10, 0, 10, 10))
    bar.pack(fill=tk.X)
    ttk.Button(bar, text="复制原文", command=lambda: _copy_orig()).pack(side=tk.LEFT, padx=2)
    ttk.Button(bar, text="复制译文", command=lambda: _copy_tran()).pack(side=tk.LEFT, padx=2)
    ttk.Button(bar, text="清空历史", command=lambda: _clear()).pack(side=tk.RIGHT)
    status = ttk.Label(bar, text="", foreground=theme.TEXT_MUTED)
    status.pack(side=tk.LEFT)

    _records = []          # 当前列表对应的记录（dict）

    def _set_status(text: str) -> None:
        try:
            status.config(text=text)
        except Exception:
            pass

    def _show_detail(rec: dict) -> None:
        detail.config(state=tk.NORMAL)
        detail.delete("1.0", tk.END)
        detail.insert("1.0",
                      f"【原文】\n{rec.get('ocr', '') or '（无）'}\n\n"
                      f"【译文】\n{rec.get('translate', '') or '（无）'}\n\n"
                      f"时间：{rec.get('time', '')}")
        detail.config(state=tk.DISABLED)

    def _load() -> None:
        """后台读取 + 过滤 → 回写列表（不阻塞 UI）。"""
        query = q.get().strip()
        _set_status("加载中…")

        def _work():
            try:
                rows = list(reversed(store.load_records() or []))   # 新 → 旧
            except Exception as e:
                _post_to_ui(app, lambda: _set_status(f"读取失败: {e}"))
                return
            if query:
                rows = [r for r in rows
                        if query in (r.get("ocr", "") or "")
                        or query in (r.get("translate", "") or "")]
            _post_to_ui(app, lambda: _fill(rows, query))

        threading.Thread(target=_work, daemon=True, name="hist-load").start()

    def _fill(rows, query: str) -> None:
        for it in tree.get_children():
            tree.delete(it)
        _records[:] = rows
        for i, r in enumerate(_records):
            snippet = (r.get("ocr", "") or "").replace("\n", " ").strip()
            if len(snippet) > 60:
                snippet = snippet[:60] + "…"
            tree.insert("", tk.END, iid=str(i), values=(r.get("time", ""), snippet))
        _set_status(f"共 {len(_records)} 条" + (f"（检索词：{query}）" if query else ""))

    def _selected_rec():
        sel = tree.selection()
        if not sel:
            return None
        idx = int(sel[0])
        return _records[idx] if 0 <= idx < len(_records) else None

    def _copy(field: str) -> None:
        rec = _selected_rec()
        if rec is None:
            _set_status("请先选择一条记录")
            return
        text = rec.get(field, "") or ""
        if not text.strip():
            _set_status("该项为空")
            return
        try:
            win.clipboard_clear()
            win.clipboard_append(text)
            win.update()
            label = "原文" if field == "ocr" else "译文"
            _set_status(f"已复制{label}（{len(text)} 字）")
        except Exception as e:
            _set_status(f"复制失败: {e}")

    def _copy_orig() -> None:
        _copy("ocr")

    def _copy_tran() -> None:
        _copy("translate")

    def _clear() -> None:
        if not messagebox.askyesno("清空历史", "确定清空全部历史记录吗？此操作不可恢复。",
                                   parent=win):
            return
        try:
            store.clear()
            _set_status("已清空历史")
        except Exception as e:
            _set_status(f"清空失败: {e}")
        _load()

    def _export(fmt: str) -> None:
        from tkinter import filedialog
        from ...services.persistence.json_history import export_markdown, export_text
        path = filedialog.asksaveasfilename(
            parent=win, title=f"导出历史（{fmt.upper()}）",
            defaultextension=f".{fmt}",
            filetypes=[("Markdown", "*.md")] if fmt == "md" else [("Text", "*.txt")])
        if not path:
            return
        _set_status("导出中…")

        def _work():
            try:
                rows = store.load_records() or []
                if fmt == "md":
                    export_markdown(rows, path)
                else:
                    export_text(rows, path)
            except Exception as e:
                _post_to_ui(app, lambda: _set_status(f"导出失败: {e}"))
                return
            _post_to_ui(app, lambda: _set_status(f"已导出 {len(rows)} 条 → {path}"))

        threading.Thread(target=_work, daemon=True, name="hist-export").start()

    tree.bind("<<TreeviewSelect>>",
              lambda e: _show_detail(_records[int(tree.selection()[0])])
              if tree.selection() else None)

    _load()
    _center(win, window.root)


