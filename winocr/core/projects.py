# -*- coding: utf-8 -*-
"""真·项目栏核心：单一真相源（ProjectManager）。

负责「项目注册表 + 当前项目」的全部增删改查与跨服务重指：
  - 切换项目时，把 JsonHistory / AI 对话文件 / 知识库过滤 / 译向覆盖 一次性重指；
  - 关闭标签 = 仅 open=False（数据全留），真删走 delete()（移除注册 + 知识 + 历史文件）；
  - 默认项目（id=="default"）不可关、不可删，且始终存在于注册表。

UI 只需调 add/switch/close_tab/reopen/rename/set_target/delete，并在 on_change 回调里刷新标签栏。
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from typing import Callable, Optional

from .config import ProjectConfig
from .paths import project_chat_history_path, project_history_path

logger = logging.getLogger(__name__)

_DEFAULT_ID = "default"


class ProjectManager:
    def __init__(self, app) -> None:
        self.app = app                            # 核心 App（提供 config + services）
        self.config = app.config
        # 全局译向「基准值」：项目无覆盖时回落到它（而非上一个项目的覆盖）
        self._base_target = self.config.translate.target
        self._on_change: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------
    # 订阅：任何项目变更后刷新 UI
    # ------------------------------------------------------------------
    def on_change(self, cb: Callable[[], None]) -> None:
        self._on_change = cb

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    @property
    def current_id(self) -> str:
        return self.config.current_project

    def current(self) -> Optional[ProjectConfig]:
        return self._by_id(self.current_id)

    def open_projects(self):
        return [p for p in self.config.projects if p.open]

    def _by_id(self, pid: str) -> Optional[ProjectConfig]:
        return next((p for p in self.config.projects if p.id == pid), None)

    def effective_translate_target(self) -> str:
        cur = self.current()
        if cur and cur.translate_target:
            return cur.translate_target
        return self._base_target

    def sync_translate_target(self) -> None:
        """启动时把当前项目的译向覆盖应用到配置（不重指其他服务）。"""
        cur = self.current()
        self._apply_translate_target(cur.translate_target if cur else "")

    # ------------------------------------------------------------------
    # 变更
    # ------------------------------------------------------------------
    def add(self, name: str, translate_target: str = "") -> ProjectConfig:
        from_id = self.config.current_project   # 先捕获，避免下方改写后取错
        pid = uuid.uuid4().hex[:8]
        while any(p.id == pid for p in self.config.projects):
            pid = uuid.uuid4().hex[:8]
        proj = ProjectConfig(
            id=pid,
            name=(name or "").strip() or "未命名",
            open=True,
            translate_target=(translate_target or "").strip(),
        )
        self.config.projects.append(proj)
        self.config.current_project = pid
        self._repoint_services(from_id, pid)
        self._persist()
        self._notify()
        return proj

    def switch(self, pid: str) -> None:
        proj = self._by_id(pid)
        if proj is None or not proj.open:
            return
        self._repoint_services(self.config.current_project, pid)
        self.config.current_project = pid
        self._persist()
        self._notify()

    def close_tab(self, pid: str) -> None:
        """关闭标签 = 仅隐藏（open=False），数据（知识/历史/对话）全部保留。"""
        if pid == _DEFAULT_ID:
            return
        proj = self._by_id(pid)
        if proj is None:
            return
        proj.open = False
        if self.config.current_project == pid:
            nxt = next((p.id for p in self.config.projects
                        if p.open and p.id != pid), _DEFAULT_ID)
            self._repoint_services(pid, nxt)
            self.config.current_project = nxt
        self._persist()
        self._notify()

    def reopen(self, pid: str) -> None:
        proj = self._by_id(pid)
        if proj is None:
            return
        proj.open = True
        self._repoint_services(self.config.current_project, pid)
        self.config.current_project = pid
        self._persist()
        self._notify()

    def rename(self, pid: str, name: str) -> None:
        proj = self._by_id(pid)
        if proj is None:
            return
        proj.name = (name or "").strip() or proj.name
        self._persist()
        self._notify()

    def set_target(self, pid: str, target: str) -> None:
        proj = self._by_id(pid)
        if proj is None:
            return
        proj.translate_target = (target or "").strip()
        if self.config.current_project == pid:
            self._apply_translate_target(proj.translate_target)
        self._persist()
        self._notify()

    def delete(self, pid: str) -> None:
        """真删（仅「管理项目」入口调用）：移除注册 + 删知识 + 删两个历史文件。

        默认项目不可删；删当前项目则回落 default 并相应重指服务。
        """
        if pid == _DEFAULT_ID:
            return
        proj = self._by_id(pid)
        if proj is None:
            return
        # 1) 知识库记录
        kb = self.app.services.get("knowledge")
        if kb is not None and hasattr(kb, "delete_project_records"):
            try:
                kb.delete_project_records(pid)
            except Exception as e:
                logger.warning("删除项目知识失败 %s: %s", pid, e)
        # 2) 历史文件
        for p in (project_history_path(pid), project_chat_history_path(pid)):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError as e:
                logger.warning("删除项目历史失败 %s: %s", p, e)
        # 3) 注册表
        self.config.projects = [p for p in self.config.projects if p.id != pid]
        # 4) 删的是当前 → 回落 default
        if self.config.current_project == pid:
            self.config.current_project = _DEFAULT_ID
            self._repoint_services(pid, _DEFAULT_ID)
        # 5) 保证 default 始终存在
        if not any(p.id == _DEFAULT_ID for p in self.config.projects):
            self.config.projects.insert(0, ProjectConfig())
        self._persist()
        self._notify()

    # ------------------------------------------------------------------
    # 启动一次性迁移：旧全局 history.json / chat_history.json → default 项目
    # ------------------------------------------------------------------
    def migrate_legacy_history(self) -> None:
        try:
            from .paths import chat_history_path, history_path
            default_h = project_history_path(_DEFAULT_ID)
            legacy_h = history_path()
            if not default_h.is_file() and legacy_h.is_file():
                shutil.copyfile(legacy_h, default_h)
            default_c = project_chat_history_path(_DEFAULT_ID)
            legacy_c = chat_history_path()
            if not default_c.is_file() and legacy_c.is_file():
                shutil.copyfile(legacy_c, default_c)
        except Exception as e:
            logger.warning("历史迁移跳过: %s", e)

    # ------------------------------------------------------------------
    # 内部：跨服务重指 + 持久化 + 通知
    # ------------------------------------------------------------------
    def _repoint_services(self, from_id: str, to_id: str) -> None:
        # AI 对话：先持久化旧项目，再重指并载入新项目对话
        ai = self.app.services.get("ai")
        if ai is not None and hasattr(ai, "set_history_path"):
            try:
                ai.set_history_path(str(project_chat_history_path(to_id)))
            except Exception as e:
                logger.warning("重指对话历史失败: %s", e)
        # JsonHistory：动态路径，按当前项目重指
        jh = self.app.services.get("persistence")
        if jh is not None and hasattr(jh, "set_project"):
            try:
                jh.set_project(to_id)
            except Exception as e:
                logger.warning("重指翻译历史失败: %s", e)
        # 知识库：save_knowledge/search 经 project_getter 动态取当前项目，无需重指
        # 译向覆盖
        cur = self._by_id(to_id)
        self._apply_translate_target(cur.translate_target if cur else "")

    def _apply_translate_target(self, target: str) -> None:
        t = target or self._base_target
        if self.config.translate.target != t:
            self.config.translate.target = t

    def _persist(self) -> None:
        try:
            self.config.save()
        except Exception as e:
            logger.warning("项目配置保存失败: %s", e)

    def _notify(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception as e:
                logger.warning("项目变更通知失败: %s", e)
