# WinOCR 3.4 「项目栏 + 数据隔离」改造蓝图

> 状态：已落地（2026-08-24，任务 1-8 + 知识库导入 + 快捷键全部完成）
> 日期：2026-08-24
> 范围：外观（分组工具栏 + Flow 换行 + WPS 式合并窄带）+ 真·项目栏（标签页）+ 按项目隔离知识库/历史/对话 + 知识库 JSON 导入

## 0. 已确认的决策

1. **关闭项目 = 仅关闭标签、保留知识**（数据不删）。真正的删除走独立的「管理项目」入口。
2. **MVP 范围扩大**：历史（`history.json`）与 AI 对话（`chat_history.json`）也按项目隔离（不只是知识库 + 译向）。
3. **知识库导入可选目标项目**（08-24 追加）：导入 JSON 时不固定当前项目，弹窗列出全部项目（默认当前）。
4. **知识库配快捷键**（08-24 追加）：新增全局热键动作「打开知识库」（`open_knowledge`，默认 `Ctrl+Shift+K`）。

## 1. 问题根因（已查证）

- **UI 被遮盖**：`winocr/ui/tk/main_window.py` 三排栏（`info_bar` / `action_bar` / `mode_bars`）都是 `Frame.pack(fill=X)` + 子按钮 `pack(side=LEFT)`。Tk 的 `pack` **不换行、不出横向滚动条**，窗口一窄右侧按钮被推出可视区 → 用户看到的「被遮盖」。
- **无项目概念**：知识库是单表 `knowledge.db`（`knowledge` + `knowledge_fts`，无项目维度）；配置是单全局 `AppConfig` TOML；历史是全局 `history.json`；对话是全局 `chat_history.json`。

## 2. 最终布局（自上而下）

1. **系统标题栏**：保留，不碰 `overrideredirect`（避开 borderless 风险）。
2. **项目标签页栏**（新增）：`默认` `病历编码` `房产调研` `+`，点击切换、`×` 关闭（默认不可关）。
3. **合并窄带**（替代原 `info_bar` + `action_bar`）：左侧菜单/系统按钮，中部分组按钮（竖线分隔），**窄窗口整组 Flow 换行**，主操作「截图识别」保持 `#1677ff` 蓝。
4. **模式栏**（原 `mode_bars`，保留 simple/advanced 切换）：同样包进 FlowFrame，窄窗换行。
5. 原文/译文内容区 + 对话面板 + 状态栏（不变）。

### 分组方案（按钮全保留，仅重排+加分隔）

| 组 | 按钮 |
|----|------|
| 输入 | 截图识别(蓝)、粘贴图片、打开文件 |
| 输出 | 复制原文、复制译文、复制全部 |
| 翻译 | 译中、译英、重新翻译、切换引擎 |
| 工具 | AI 对话、朗读、清空、清场 |
| 系统 | 历史记录、知识库、插件、关于、API 设置、热键设置、**管理项目** |

## 3. 数据模型

### 3.1 配置（`winocr/core/config.py`）
新增 `ProjectConfig` dataclass：
```python
@dataclass
class ProjectConfig:
    id: str = "default"            # 稳定标识（"default" 保留给默认项目）
    name: str = "默认"             # 标签页显示名
    open: bool = True              # 是否在标签栏展开（关闭标签 = open=False）
    translate_target: str = ""     # 译向覆盖；空 = 用全局 config.translate.target
```
`AppConfig` 增加两个字段：`projects: List[ProjectConfig]`（默认 `[ProjectConfig()]`）、`current_project: str = "default"`。
- 序列化：`sections()` 增加 `("projects", self.projects)`；`to_dict` 经 `asdict` 递归成列表字典。
- 反序列化：`from_dict` 对 `projects` 走专门的 `_coerce_projects(raw)`（通用 `build()` 不处理 dataclass 列表）；**强制保证存在一个 `id=="default"` 项目**，并保证 `current_project` 指向存在的项目，否则回落 `"default"`。
- 兼容性：旧 TOML 无 `projects` 段 → 回落 `[ProjectConfig()]`，`_apply_legacy_migration` 无需改动。

### 3.2 路径（`winocr/core/paths.py`）
新增（与现有 `history_path()`/`chat_history_path()` 并列）：
```python
def project_history_path(project_id: str) -> Path:
    return user_dir() / "history" / f"{project_id}.json"
def project_chat_history_path(project_id: str) -> Path:
    return user_dir() / "chat_history" / f"{project_id}.json"
```
- **一次性迁移**：`ProjectManager` 初始化时，若 `默认(default)` 项目文件不存在、但旧全局 `history.json` / `chat_history.json` 存在 → 复制到对应项目文件（保留历史不丢）。

### 3.3 知识库（`winocr/services/persistence/knowledge.py`）
- `knowledge` 表加 `project TEXT DEFAULT 'default'`（`_init_db` 里幂等 `ALTER TABLE ... ADD COLUMN`，旧数据归「默认」）。
- `save_knowledge(record, project=None)`：写入时带 `project`（默认取当前项目）。
- `search/load_records` 经 `JOIN knowledge_fts f ON f.rowid=k.id` + `WHERE k.project = ?` 过滤。
- `delete(record_id)`：不变（按 id 删，天然落在当前项目上下文里）。
- `knowledge_fts` 虚拟表不改（不过滤列）。
- `delete_project_records(project_id)`：新增，按 project 批量删（供「管理项目」真删用）。

### 3.4 历史 / 对话
- **JsonHistory**（`json_history.py`）：构造支持 `project_getter`（零参回调返回当前项目 id），路径按操作动态算 `project_history_path(...)`；不传则回落旧全局路径（兼容）。
- **AI provider**（`openai_chat.py` / `glm_chat.py`）：保持 `history_path` 构造参数，但运行时切换项目需重指向。新增 `set_history_path(p)` 方法：先 `_persist()` 旧项目，再改 `self.history_path` 并 `_load_history()` 载入新项目对话。
- **app.py 装配**：`pcls(project=...)` 改为接受当前项目；`_make_ai` 传 `project_chat_history_path(current)`。

## 4. 核心：ProjectManager（单一真相源）

新增 `winocr/core/projects.py`，由 `App` 持有（`app.projects`）。职责：
- 持有 `config.projects` 列表与 `config.current_project`。
- `current_id` / `current()` 返回当前项目。
- `effective_translate_target()`：当前项目覆盖 or 全局 `config.translate.target`。
- `add(name, translate_target="")` → 生成 `id=uuid4().hex[:8]`，追加并设为 current，保存配置。
- `switch(id)` → **切换时依次**：(1) AI provider `_persist()` 旧对话；(2) JsonHistory 重指向新项目；(3) AI provider `set_history_path(新对话文件)` + `_load_history()`；(4) 应用译向覆盖（设 `config.translate.target`）；(5) `config.current_project=id` + `config.save()`；(6) 通知 UI 刷新标签高亮 + 知识库面板 + 引擎标签。
- `close_tab(id)` → 仅 `open=False`（保留数据与注册），切到另一个 open 项目，保存配置。（默认项目不可关）
- `reopen(id)` → `open=True`。
- `rename(id, name)` / `set_target(id, target)`。
- `delete(id)`（真删，仅「管理项目」调用）→ 从注册表移除 + `knowledge.delete_project_records(id)` + 删除 `history/<id>.json` 与 `chat_history/<id>.json`；若删的是 current，切到 default。默认项目不可删。

## 5. UI 改动（`winocr/ui/tk/main_window.py` + 新增 `project_bar.py`）

- **新增 `FlowFrame`**（`project_bar.py` 或 `widgets.py`）：监听 `<Configure>`，按行宽把「组」整体换到下一行（组不被拆散），约 30 行。解决「被遮盖」根因。
- **`_build_project_bar()`**（新，置于最顶）：用 `ttk.Notebook` 或自绘 Label 标签栏渲染 `app.projects` 的 open 项目 + `+`；点击切 `switch()`，`×` 切 `close_tab()`。
- **合并窄带**：原 `info_bar` 的右侧系统按钮（关于/插件/知识库/历史/API/热键）并入一个 `FlowFrame`，左侧加「截图识别(蓝)」等输入组，竖线分隔。
- **模式栏**：原 `mode_bars` 包进 `FlowFrame`，分组 + 换行。
- 主线程安全：标签切换/关闭走 `@ui_thread` 守卫或在主线程回调里执行（遵循项目铁律）。
- `apply_ui_mode` / `switch_ui_mode` 保持（simple/advanced 切换不变）。

## 6. 行为总表

| 动作 | 入口 | 结果 |
|------|------|------|
| 新建项目 | 标签栏 `+` → 小对话框（名 + 可选译向） | 注册 + 切到它 + 存配置 |
| 切换项目 | 点标签 | 重指历史/对话/知识 + 应用译向 + 刷新面板 |
| 关闭标签 | 标签 `×` | **仅隐藏（open=False），数据全留**；切到别的 open 项目 |
| 管理项目 | 合并带「管理项目」按钮 → 对话框 | 列出全部（含已关），可重开/改名/设译向/**真删** |
| 删除项目 | 「管理项目」真删 | 删注册表 + 知识 + 两个历史文件（默认不可删） |
| 重开程序 | 启动 | 标签栏只显示 `open=True` 的项目；current 还原 |

## 7. 风险与验证

- **低风险**：`ALTER TABLE` 幂等；配置新段 `from_dict` 忽略未知键、坏配置不崩；`default` 项目强制存在。
- **历史不丢**：旧全局 `history.json`/`chat_history.json` 一次性迁移进 `默认` 项目（复制，旧文件保留）。
- **线程安全**：所有 Tk 操作走主线程；历史文件读写本就是后台线程 + `os.replace` 原子写。
- **验证**：
  1. 新建「病历编码」→ 识别一段 → 存知识库 → 切到「默认」→ 知识库面板应不含该条；切回 → 含。
  2. 「病历编码」里对话几句 → 切走再切回 → 对话上下文仍在；「默认」里看不到。
  3. 关闭「病历编码」标签 → 数据仍在（重开管理项目可见并可重开）。
  4. 窄窗拖动 → 整组换行，无按钮被裁切。
  5. 旧用户（无 projects 段）重开 → 历史自动归入「默认」，不丢。

## 8. 落地顺序（建议）

1. `config.py`：`ProjectConfig` + `AppConfig` 两字段 + 序列化/反序列化 + 校验。
2. `paths.py`：两个 `project_*_path` + 迁移逻辑（迁移放 ProjectManager）。
3. `projects.py`：ProjectManager 全量方法。
4. `knowledge.py`：`project` 列 + 过滤 + `delete_project_records`。
5. `json_history.py` + 两个 AI provider：`project_getter` / `set_history_path`。
6. `app.py`：装配接 ProjectManager，`switch()` 重指服务。
7. UI：`FlowFrame` + 项目标签栏 + 合并窄带 + 模式栏包 FlowFrame + 「管理项目」对话框。
8. 单测 + 手动验证（对照第 7 节）。
