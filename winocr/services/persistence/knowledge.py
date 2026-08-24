# -*- coding: utf-8 -*-
"""持久化：知识库（sqlite3 + FTS5，中文可检索，纯本地离线）。

「同一批文档反复回看与沉淀」的数据资产闭环：
  - **存**：把一次 OCR / 翻译 / AI 解读沉淀成一条可溯源知识
  - **查**：FTS5 全文检索（中文 trigram 分词，零额外分词库）+ LIKE 兜底
  - **溯源**：来源 / 原图哈希 / 场景 / 时间字段，始终知道这条知识从哪来

选 sqlite3 而非 JSON：检索是刚需；WAL 让「写入不阻塞读取」；
连接按线程加锁，多线程存/查互不踩踏。

FTS5 注意：trigram tokenizer 需要 SQLite ≥ 3.34（Python 3.10+ 自带的通常满足），
查询词 <3 字符时 trigram 无法命中，自动回退 LIKE。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import closing
from typing import Callable, List, Optional

from .base import Persistence
from ...core.types import KnowledgeRecord

_DEFAULT_LIMIT = 50


class KnowledgeBase(Persistence):
    name = "knowledge"
    display_name = "知识库"

    def __init__(self, path: Optional[str] = None,
                 project_getter: Optional[Callable[[], str]] = None) -> None:
        if path is None:
            from ...core.paths import knowledge_path
            path = str(knowledge_path())
        self.path = path
        self._project_getter = project_getter   # 真·项目栏：动态取当前项目 id
        self._lock = threading.RLock()
        self._init_db()

    # ------------------------------------------------------------------
    # 兼容迁移
    # ------------------------------------------------------------------
    @staticmethod
    def _migrate_legacy_columns(conn) -> None:
        """老 schema → 新 schema 的幂等迁移。

        - 旧 WinOCR-Portable-v1.0.0 用 ``scenario``（非 ``scene``），且多
          ``tags`` / ``note`` 列。把 ``scenario`` 数据拷到 ``scene``，
          缺列则 ALTER ADD；``tags`` / ``note`` 保留（代码层兼容读）。
        - 全程在调用方事务内执行，幂等（重复启动不会重复 ALTER）。
        """
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(knowledge)")}
        if "scene" not in cols:
            conn.execute(
                "ALTER TABLE knowledge ADD COLUMN scene TEXT DEFAULT ''")
            if "scenario" in cols:
                # 老数据：把 scenario 内容拷到 scene（只填空行，不覆盖已有）
                conn.execute(
                    "UPDATE knowledge SET scene = scenario "
                    "WHERE (scene IS NULL OR scene = '')"
                    " AND scenario IS NOT NULL AND scenario != ''")
        # 兼容字段：有则保留读，无则补默认空串（不影响 INSERT）
        if "tags" not in cols:
            conn.execute(
                "ALTER TABLE knowledge ADD COLUMN tags TEXT DEFAULT ''")
        if "note" not in cols:
            conn.execute(
                "ALTER TABLE knowledge ADD COLUMN note TEXT DEFAULT ''")

    # ------------------------------------------------------------------
    # 连接与建表
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        except OSError:
            pass
        with self._lock:
            with closing(self._connect()) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS knowledge (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_type  TEXT DEFAULT '',
                        source_path  TEXT DEFAULT '',
                        image_hash   TEXT DEFAULT '',
                        scene        TEXT DEFAULT '',
                        ocr_text     TEXT DEFAULT '',
                        translate_text TEXT DEFAULT '',
                        ai_explanation TEXT DEFAULT '',
                        created_at   TEXT DEFAULT (datetime('now', 'localtime'))
                    )""")
                # 中文检索：trigram（无需分词库）；老 SQLite 无 trigram 则回落
                # 默认 unicode61（英文/数字可查，中文靠 LIKE 兜底）
                try:
                    conn.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                            ocr_text, translate_text, ai_explanation,
                            tokenize = 'trigram'
                        )""")
                except sqlite3.OperationalError:
                    conn.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                            ocr_text, translate_text, ai_explanation
                        )""")
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_knowledge_created
                    ON knowledge(created_at)""")
                # 真·项目栏 MVP：给知识加 project 维度（旧库无此列，幂等迁移）
                try:
                    conn.execute(
                        "ALTER TABLE knowledge ADD COLUMN project TEXT "
                        "DEFAULT 'default'")
                except sqlite3.OperationalError:
                    pass                    # 列已存在（新装或已迁移）
                # 老 schema 兼容：3.4.18 之前的 WinOCR-Portable-v1.0.0 用的是
                # `scenario`（非 `scene`），并多 `tags` / `note` 列。把 scenario
                # 数据拷到 scene，避免 INSERT 报"无该列"丢数据。
                self._migrate_legacy_columns(conn)
                conn.commit()

    # ------------------------------------------------------------------
    # Persistence 契约（与 json_history 并存，各服务独立接线）
    # ------------------------------------------------------------------
    def append_record(self, record: dict) -> None:
        """兼容 Persistence 契约：普通历史记录也能沉淀进知识库（空记录不收）。"""
        if not (record.get("ocr") or record.get("translate")):
            return
        self.save_knowledge(record)

    def load_records(self, project: Optional[str] = None) -> list:
        """最近 1000 条，按时间倒序（UI 回看用）。按当前项目过滤。"""
        project = self._resolve_project(project)
        rows = self._query(
            "SELECT * FROM knowledge WHERE project = ? "
            "ORDER BY id DESC LIMIT ?",
            (project, _DEFAULT_LIMIT * 20))
        return [dict(r) for r in rows]

    def delete_project_records(self, project: str) -> int:
        """按项目删除全部知识（含 FTS 索引）。返回删除条数（「管理项目」真删用）。"""
        project = project or "default"
        with self._lock:
            with closing(self._connect()) as conn, conn:
                ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM knowledge WHERE project = ?", (project,))]
                if not ids:
                    return 0
                conn.execute("DELETE FROM knowledge WHERE project = ?", (project,))
                conn.execute(
                    "DELETE FROM knowledge_fts WHERE rowid IN ({})".format(
                        ",".join("?" * len(ids))), ids)
                return len(ids)

    def _resolve_project(self, project: Optional[str]) -> str:
        """当前项目解析：显式 project > project_getter > 'default'。"""
        if project is None and self._project_getter is not None:
            try:
                project = self._project_getter()
            except Exception:
                project = None
        return project or "default"

    # ------------------------------------------------------------------
    # 存
    # ------------------------------------------------------------------
    def save_knowledge(self, record, project: Optional[str] = None) -> int:
        """存一条知识，返回新记录 id。record 可为 dict 或 KnowledgeRecord。

        project 缺省时取 ``project_getter``（当前项目），再回落 'default'；
        Persistence 契约（append_record）传 ``ocr``/``translate``。
        """
        if project is None and self._project_getter is not None:
            try:
                project = self._project_getter()
            except Exception:
                project = None
        project = project or "default"
        data = record if isinstance(record, dict) else record.__dict__
        ocr_text = str(data.get("ocr_text") or data.get("ocr") or "").strip()
        translate_text = str(data.get("translate_text")
                             or data.get("translate") or "").strip()
        ai_explanation = str(data.get("ai_explanation") or "").strip()
        if not (ocr_text or translate_text or ai_explanation):
            return 0                                # 空知识不存
        created_at = (data.get("created_at") or ""
                      or time.strftime("%Y-%m-%d %H:%M:%S"))
        with self._lock:
            with closing(self._connect()) as conn, conn:
                cur = conn.execute(
                    "INSERT INTO knowledge (source_type, source_path, image_hash,"
                    " scene, ocr_text, translate_text, ai_explanation, project,"
                    " created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(data.get("source_type", "") or ""),
                     str(data.get("source_path", "") or ""),
                     str(data.get("image_hash", "") or ""),
                     str(data.get("scene", "") or ""),
                     ocr_text, translate_text, ai_explanation, project, created_at))
                rec_id = int(cur.lastrowid)
                # FTS 索引与主表同事务提交，保证一致性
                conn.execute(
                    "INSERT INTO knowledge_fts (rowid, ocr_text, translate_text,"
                    " ai_explanation) VALUES (?, ?, ?, ?)",
                    (rec_id, ocr_text, translate_text, ai_explanation))
            return rec_id

    # ------------------------------------------------------------------
    # 查
    # ------------------------------------------------------------------
    def search(self, query: str, limit: int = _DEFAULT_LIMIT,
               project: Optional[str] = None) -> List[KnowledgeRecord]:
        """按关键词检索，命中不足时自动降级 LIKE；空查询返回最新 limit 条。

        project 缺省时取当前项目（project_getter），只回看该项目知识。
        project="*" 表示跨项目查全部（不按 project 过滤）。
        """
        q = (query or "").strip()
        all_proj = project == "*"
        if not all_proj:
            project = self._resolve_project(project)
        with self._lock:
            if q:
                rows = self._fts_search(q, limit, project if not all_proj else None)
                if not rows:
                    rows = self._like_search(q, limit, project if not all_proj else None)
            else:
                if all_proj:
                    rows = self._query(
                        "SELECT * FROM knowledge ORDER BY id DESC LIMIT ?",
                        (limit,))
                else:
                    rows = self._query(
                        "SELECT * FROM knowledge WHERE project = ? "
                        "ORDER BY id DESC LIMIT ?", (project, limit))
        return [self._row_to_record(r) for r in rows]

    def delete(self, record_id: int) -> None:
        """按 id 删除一条知识（含 FTS 索引）。"""
        with self._lock:
            with closing(self._connect()) as conn, conn:
                conn.execute("DELETE FROM knowledge WHERE id = ?", (record_id,))
                conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?",
                             (record_id,))

    def import_csv(self, path, project: Optional[str] = None) -> tuple:
        """从 CSV 导入知识，返回 (成功条数, 跳过条数)。

        CSV 表头用字段名（与模板一致）：source_type / source_path / image_hash /
        scene / tags / note / ocr_text / translate_text / ai_explanation /
        created_at。编码自动探测（utf-8-sig → utf-8 → gbk，兼容 WPS/Excel 导出）；
        空行（原文/译文/AI 全空）自动跳过；其余复用 import_records。
        """
        import csv
        encodings = ("utf-8-sig", "utf-8", "gbk")
        text = None
        for enc in encodings:
            try:
                with open(path, "r", encoding=enc) as f:
                    text = f.read()
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        if text is None:
            raise ValueError(f"无法识别文件编码: {path}（试过 utf-8-sig/utf-8/gbk）")
        rows = list(csv.DictReader(__import__("io").StringIO(text)))
        return self.import_records(rows, project=project)

    # ------------------------------------------------------------------
    # 导入（JSON 回灌 / 批量沉淀）
    # ------------------------------------------------------------------
    _IMPORT_FIELDS = (
        "source_type", "source_path", "image_hash", "scene",
        "ocr_text", "translate_text", "ai_explanation", "created_at",
    )

    def import_records(self, records, project: Optional[str] = None
                       ) -> tuple:
        """批量导入知识记录，返回 (成功条数, 跳过条数)。

        records: 可迭代的 dict（与 export('json') 输出结构兼容，id 忽略、
        自增重建；created_at 保留；兼容老 schema 的 scenario/tags/note 字段）。
        空记录（原文/译文/AI 解读全空）跳过。
        单事务批量写入主表 + FTS 索引，保证一致性。
        project 缺省时取当前项目（project_getter），再回落 'default'。
        """
        if project is None and self._project_getter is not None:
            try:
                project = self._project_getter()
            except Exception:
                project = None
        project = project or "default"
        ok = skipped = 0
        with self._lock:
            with closing(self._connect()) as conn, conn:
                # 探测 schema：老库（未迁移）可能缺 scenario/tags/note 列
                cols = {row[1] for row in conn.execute(
                    "PRAGMA table_info(knowledge)")}
                write_scenario = "scenario" in cols
                write_tags = "tags" in cols
                write_note = "note" in cols
                # 动态构造 INSERT 列与占位符
                base_cols = ("source_type", "source_path", "image_hash",
                             "scene", "ocr_text", "translate_text",
                             "ai_explanation", "project", "created_at")
                extra_cols = ()
                if write_scenario:
                    extra_cols += ("scenario",)
                if write_tags:
                    extra_cols += ("tags",)
                if write_note:
                    extra_cols += ("note",)
                insert_cols = base_cols + extra_cols
                placeholders = ",".join("?" * len(insert_cols))
                sql = (f"INSERT INTO knowledge ({','.join(insert_cols)}) "
                       f"VALUES ({placeholders})")
                for rec in records:
                    data = rec if isinstance(rec, dict) else getattr(rec, "__dict__", {})
                    ocr_text = str(data.get("ocr_text") or "").strip()
                    translate_text = str(data.get("translate_text") or "").strip()
                    ai_explanation = str(data.get("ai_explanation") or "").strip()
                    if not (ocr_text or translate_text or ai_explanation):
                        skipped += 1
                        continue
                    # scene 兼容老 schema 字段 scenario（迁数据时已复制，优先用 scene）
                    scene = (str(data.get("scene") or "").strip()
                    or str(data.get("scenario") or "").strip())
                    values = [
                        str(data.get("source_type", "") or ""),
                        str(data.get("source_path", "") or ""),
                        str(data.get("image_hash", "") or ""),
                        scene,
                        ocr_text, translate_text, ai_explanation,
                        project,
                        str(data.get("created_at") or "")
                        or time.strftime("%Y-%m-%d %H:%M:%S"),
                    ]
                    if write_scenario:
                        values.append(str(data.get("scenario", "") or ""))
                    if write_tags:
                        values.append(str(data.get("tags", "") or ""))
                    if write_note:
                        values.append(str(data.get("note", "") or ""))
                    cur = conn.execute(sql, values)
                    rec_id = int(cur.lastrowid)
                    conn.execute(
                        "INSERT INTO knowledge_fts (rowid, ocr_text,"
                        " translate_text, ai_explanation) VALUES (?, ?, ?, ?)",
                        (rec_id, ocr_text, translate_text, ai_explanation))
                    ok += 1
        return ok, skipped


    # ------------------------------------------------------------------
    # 导出（JSON / Markdown / CSV，供「知识库」面板归档）
    # ------------------------------------------------------------------
    def export(self, fmt: str = "json", query: str = "",
               limit: int = 1000, project: Optional[str] = None) -> str:
        """把知识库记录导出为文本。

        fmt:
          - ``json``：结构化 JSON 列表，含全部溯源字段（默认）
          - ``md``  ：Markdown 表格，适合直接贴进文档
          - ``csv`` ：CSV，可被 Excel / WPS 打开（字段内换行转 \\n 防破行）
        query 非空时只导出命中记录；空则导出最近 limit 条。
        project="*" 导出全部项目；None 取当前项目。
        """
        fmt = (fmt or "json").strip().lower()
        rows = self.search(query or "", limit, project=project)
        if fmt == "md":
            lines = [
                "| ID | 时间 | 来源 | 场景 | 标签 | 备注 | 原文 | 译文 | AI 解读 |",
                "|---:|:-----|:-----|:-----|:-----|:-----|:-----|:-----|:--------|",
            ]
            for r in rows:
                def _cell(v: str) -> str:
                    return (v or "").replace("|", "\\|").replace("\n", "<br>")
                lines.append(
                    f"| {r.id} | {_cell(r.created_at)} | {_cell(r.source_type)}"
                    f" | {_cell(r.scene or r.scenario)} | {_cell(r.tags)}"
                    f" | {_cell(r.note)} | {_cell(r.ocr_text)}"
                    f" | {_cell(r.translate_text)} | {_cell(r.ai_explanation)} |")
            return "\n".join(lines)
        if fmt == "csv":
            import csv
            import io
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow(["id", "created_at", "source_type", "source_path",
                        "image_hash", "scene", "scenario", "tags", "note",
                        "ocr_text", "translate_text", "ai_explanation"])
            for r in rows:
                w.writerow([r.id, r.created_at, r.source_type, r.source_path,
                            r.image_hash, r.scene, r.scenario, r.tags, r.note,
                            r.ocr_text, r.translate_text, r.ai_explanation])
            return buf.getvalue()
        # 默认 json
        import json
        return json.dumps(
            [{"id": r.id, "created_at": r.created_at,
              "source_type": r.source_type, "source_path": r.source_path,
              "image_hash": r.image_hash,
              "scene": r.scene or r.scenario,
              "scenario": r.scenario, "tags": r.tags, "note": r.note,
              "ocr_text": r.ocr_text, "translate_text": r.translate_text,
              "ai_explanation": r.ai_explanation} for r in rows],
            ensure_ascii=False, indent=2)


    # ------------------------------------------------------------------
    def _query(self, sql: str, params: tuple = ()):
        with closing(self._connect()) as conn:
            return conn.execute(sql, params).fetchall()

    @staticmethod
    def _fts_quote(q: str) -> str:
        """把查询词包成 FTS5 短语（双引号转义，防注入/语法错误）。"""
        return '"' + q.replace('"', '""') + '"'

    def _fts_search(self, q: str, limit: int, project: Optional[str]):
        # trigram 要求查询词 ≥ 3 字符；短词在 init 时已建 trigram 表也会失败，回退 LIKE
        if len(q) < 3:
            return []
        try:
            if project is None:
                return self._query(
                    "SELECT k.* FROM knowledge k"
                    " JOIN knowledge_fts f ON f.rowid = k.id"
                    " WHERE knowledge_fts MATCH ?"
                    " ORDER BY k.id DESC LIMIT ?",
                    (self._fts_quote(q), limit))
            return self._query(
                "SELECT k.* FROM knowledge k"
                " JOIN knowledge_fts f ON f.rowid = k.id"
                " WHERE knowledge_fts MATCH ? AND k.project = ?"
                " ORDER BY k.id DESC LIMIT ?",
                (self._fts_quote(q), project, limit))
        except sqlite3.OperationalError:
            return []

    def _like_search(self, q: str, limit: int, project: Optional[str]):
        like = f"%{q}%"
        if project is None:
            return self._query(
                "SELECT * FROM knowledge"
                " WHERE ocr_text LIKE ? OR translate_text LIKE ?"
                " OR ai_explanation LIKE ?"
                " ORDER BY id DESC LIMIT ?",
                (like, like, like, limit))
        return self._query(
            "SELECT * FROM knowledge"
            " WHERE (ocr_text LIKE ? OR translate_text LIKE ?"
            " OR ai_explanation LIKE ?) AND project = ?"
            " ORDER BY id DESC LIMIT ?",
            (like, like, like, project, limit))

    @staticmethod
    def _row_to_record(r: sqlite3.Row) -> KnowledgeRecord:
        # sqlite3.Row 没有 .keys()，用「先试取」检测列是否存在（兼容老库）
        def _get(*names, default=""):
            for n in names:
                try:
                    v = r[n]
                except (IndexError, KeyError):
                    continue
                return v if v is not None else default
            return default
        return KnowledgeRecord(
            id=int(r["id"]),
            source_type=_get("source_type"),
            source_path=_get("source_path"),
            image_hash=_get("image_hash"),
            # scene 兼容老库的 scenario（迁移后两者都有；未迁移库读 scenario）
            scene=_get("scene", "scenario"),
            scenario=_get("scenario"),
            tags=_get("tags"),
            note=_get("note"),
            ocr_text=_get("ocr_text"),
            translate_text=_get("translate_text"),
            ai_explanation=_get("ai_explanation"),
            created_at=_get("created_at"),
        )
