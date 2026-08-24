# -*- coding: utf-8 -*-
"""知识库（sqlite3 + FTS5，中文检索）测试。"""
from __future__ import annotations

import pytest

from winocr.core.types import KnowledgeRecord
from winocr.services.persistence.knowledge import KnowledgeBase


@pytest.fixture
def kb(tmp_path):
    return KnowledgeBase(path=str(tmp_path / "knowledge.db"))


def test_save_and_search_chinese(kb):
    """中文全文检索（FTS5 trigram）命中。"""
    rid = kb.save_knowledge({"source_type": "file", "source_path": "t.pdf",
                             "ocr_text": "肌酐偏高 肾功能 检查报告"})
    assert rid > 0
    hits = kb.search("肌酐", limit=10)
    assert hits and "肌酐" in hits[0].ocr_text
    assert hits[0].source_path == "t.pdf"      # 溯源字段随记录带回


def test_short_query_falls_back_to_like(kb):
    """trigram 要求 >=3 字符，短查询自动降级 LIKE。"""
    kb.save_knowledge({"ocr_text": "ABC 测试"})
    hits = kb.search("AB")
    assert hits and hits[0].ocr_text == "ABC 测试"


def test_append_record_contract(kb):
    """Persistence 契约键名（ocr/translate）可入库。"""
    kb.append_record({"ocr": "契约原文", "translate": "contract"})
    hits = kb.search("contract")
    assert hits and hits[0].translate_text == "contract"


def test_empty_knowledge_not_saved(kb):
    """空内容不落库。"""
    assert kb.save_knowledge({"ocr_text": "   "}) == 0
    assert kb.search("") == []


def test_delete(kb):
    rid = kb.save_knowledge({"ocr_text": "将被删除"})
    kb.delete(rid)
    assert kb.search("将被删除") == []


def test_knowledge_record_object(kb):
    """KnowledgeRecord 对象可直接入库，字段 round-trip。"""
    rec = KnowledgeRecord(ocr_text="record 对象", scene="病历")
    rid = kb.save_knowledge(rec)
    hits = kb.search("record")
    assert hits and hits[0].scene == "病历" and hits[0].id == rid


# ----------------------------------------------------------------------
# 导入（import_records）
# ----------------------------------------------------------------------
def test_import_roundtrip_from_export(kb):
    """导出 JSON → 导入 → 再导出：往返内容一致（id 重建、字段保留）。"""
    kb.save_knowledge({"source_type": "file", "source_path": "t.pdf",
                       "scene": "病历", "ocr_text": "肌酐偏高",
                       "translate_text": "creatinine high",
                       "ai_explanation": "建议复查"})
    exported = kb.export("json", limit=100)
    import json
    records = json.loads(exported)
    assert records and records[0]["ocr_text"] == "肌酐偏高"

    kb2 = KnowledgeBase(path=str(kb.path) + ".imported.db")
    ok, skipped = kb2.import_records(records)
    assert (ok, skipped) == (len(records), 0)
    assert kb2.search("肌酐")[0].scene == "病历"          # 溯源字段带回
    assert kb2.search("creatinine")[0].translate_text == "creatinine high"
    assert kb2.search("肌酐")[0].created_at == records[0]["created_at"]


def test_import_to_specific_project(kb):
    """指定 project 时记录归入该项目，且不影响 default。"""
    records = [
        {"ocr_text": "房产调研笔记 A", "scene": "调研"},
        {"ocr_text": "房产调研笔记 B", "scene": "调研"},
    ]
    ok, skipped = kb.import_records(records, project="fangchan")
    assert (ok, skipped) == (2, 0)
    hits = kb.search("房产", project="fangchan")
    assert len(hits) == 2
    # 数据隔离：default 项目查不到
    assert kb.search("房产", project="default") == []
    # 空记录跳过计数
    ok2, skipped2 = kb.import_records([{"ocr_text": "  "}, {"translate_text": ""}],
                                      project="fangchan")
    assert (ok2, skipped2) == (0, 2)


def test_import_ignores_bad_input(kb):
    """非 dict / 缺字段 / 非法结构不崩溃，可导入的照常导入。"""
    records = [None, 42, "str", {"scene": "只有场景"}, {"ocr_text": "有效" * 3}]
    ok, skipped = kb.import_records(records)
    assert ok == 1
    assert skipped == 4
    assert kb.search("有效")


# ----------------------------------------------------------------------
# 老 schema 兼容（来自 WinOCR-Portable-v1.0.0：scenario/tags/note）
# ----------------------------------------------------------------------
def test_legacy_schema_is_migrated_on_init(tmp_path):
    """老库 schema（无 scene，有 scenario + tags + note）→ _init_db 后能 save_knowledge。"""
    import sqlite3
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(p))
    conn.executescript("""
        CREATE TABLE knowledge (
            id INTEGER PRIMARY KEY,
            created_at TEXT,
            source_type TEXT,
            source_path TEXT,
            image_hash TEXT,
            ocr_text TEXT,
            translate_text TEXT,
            ai_explanation TEXT,
            scenario TEXT,
            tags TEXT,
            note TEXT
        );
        CREATE VIRTUAL TABLE knowledge_fts USING fts5(
            ocr_text, translate_text, ai_explanation
        );
        INSERT INTO knowledge (id, ocr_text, scenario, tags, created_at)
            VALUES (1, '老库原文', '病历', 'ICD', '2025-12-01 10:00:00');
    """)
    conn.commit()
    conn.close()

    kb = KnowledgeBase(path=str(p))
    # 迁移后：scene 列存在、scenario 数据已拷过来
    rows = kb.search("老库")
    assert rows and rows[0].scene == "病历"        # scenario → scene
    assert rows[0].scenario == "病历"               # 原列保留
    assert rows[0].tags == "ICD"
    # 新记录能正常保存（不会再报"no column named scene"）
    rid = kb.save_knowledge({"ocr_text": "新记录", "scene": "调研"})
    assert rid > 0
    assert kb.search("新记录")[0].scene == "调研"


def test_import_accepts_legacy_scenario_field(tmp_path):
    """导入 dict 含 scenario 字段时，自动归入 scene（兼容老导出格式）。"""
    kb = KnowledgeBase(path=str(tmp_path / "kb.db"))
    ok, skipped = kb.import_records([
        {"ocr_text": "病历编码笔记", "scenario": "ICD-10", "tags": "心血管"},
    ])
    assert (ok, skipped) == (1, 0)
    rec = kb.search("病历")[0]
    assert rec.scene == "ICD-10"
    assert rec.tags == "心血管"


# ----------------------------------------------------------------------
# CSV 导入 + 导入模板
# ----------------------------------------------------------------------
def test_import_csv_utf8_sig(tmp_path):
    """CSV 导入（utf-8-sig 编码、字段映射、空行跳过）。"""
    csv_p = tmp_path / "in.csv"
    csv_p.write_text(
        "id,created_at,source_type,scene,tags,ocr_text,translate_text\n"
        ',"",manual,病历,ICD-10,"肌酐偏高 肾功能","creatinine high"\n'
        ',"",manual,房产调研,"","",""\n',              # 空行 → 跳过
        encoding="utf-8-sig")
    kb = KnowledgeBase(path=str(tmp_path / "kb.db"))
    ok, skipped = kb.import_csv(str(csv_p))
    assert (ok, skipped) == (1, 1)
    rec = kb.search("肌酐")[0]
    assert rec.scene == "病历" and rec.tags == "ICD-10"
    assert rec.translate_text == "creatinine high"


def test_import_csv_gbk(tmp_path):
    """GBK 编码 CSV（WPS/Excel 另存常见编码）也能导入。"""
    csv_p = tmp_path / "gbk.csv"
    csv_p.write_bytes(
        "ocr_text,scene\n".encode("gbk")
        + "病历编码 规则,ICD-10\n".encode("gbk"))
    kb = KnowledgeBase(path=str(tmp_path / "kb2.db"))
    ok, skipped = kb.import_csv(str(csv_p))
    assert (ok, skipped) == (1, 0)
    assert kb.search("规则")[0].scene == "ICD-10"


def test_ensure_import_templates_idempotent(tmp_path, monkeypatch):
    """模板目录自动生成且幂等（已存在不覆盖）。"""
    from winocr.core import paths
    from winocr.ui.tk import dialogs
    monkeypatch.setattr(paths, "user_dir", lambda: tmp_path)
    # 清掉 dialogs 里的路径缓存影响（它每次调 paths.import_templates_dir）
    first = dialogs.ensure_import_templates()
    assert first, "应生成模板文件"
    for p in first:
        assert p.exists()
    # 幂等：再次调用不新增文件、内容不变
    before = {p: p.read_text(encoding="utf-8-sig") for p in first}
    again = dialogs.ensure_import_templates()
    assert again == []
    for p in first:
        assert p.read_text(encoding="utf-8-sig") == before[p]
