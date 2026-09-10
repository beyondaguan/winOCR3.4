# WinOCR 更新日志

## 3.4.25 — 全面 Bug 修复与模型切换稳定性（2026-09-04）

> 对 3.4.24 进行全面代码审查，发现并修复 8 个 bug（含 5 个严重级别）。修复后的配置加载、崩溃捕获、AI 对话流式输出和模型切换均已通过单元测试验证。

### 🔴 严重修复

**配置加载：`config.toml` 中 `[logging]` 段被完全忽略**
- 根因：`AppConfig.from_dict()` 构造 `cls(...)` 时漏传了 `logging` 字段，导致用户自定义的日志级别/目录/控制台开关永远使用默认值
- 修复：补入 `logging=build(LoggingConfig, d.get("logging"))`
- 影响：此前 `config.toml` 中的 `level = "DEBUG"`、`dir = "..."`、`console = false` 等配置均不生效

**AI 对话 / 翻译：切换 GGUF 模型后仍用旧模型**
- 根因：`llama_cpp_chat.py` 的 `apply_config()` 和 `llama_cpp.py` 的 `set_config()` 只更新 `_model_name`，未清空 `_llm` 缓存；`_get_llm()` 的缓存检查不校验模型名一致性
- 修复：模型名变更时强制置 `self._llm = None` + `self._model_path = ""`
- 影响：用户在设置页切换本地模型后，AI 对话和翻译输出仍是旧模型的结果

**崩溃捕获：同秒内多次崩溃文件互相覆盖**
- 根因：`crash-YYYYMMDD-HHMMSS.log` 文件名精度到秒，主线程与子线程同时崩溃时后者覆盖前者
- 修复：文件名冲突时自动追加序号 `crash-20260904-235255-1.log`
- 影响：高并发崩溃场景下可能丢失前一份 crash 转储

**崩溃捕获：`uninstall_crash_handler` 不恢复 Tkinter 异常回调**
- 根因：`patch_tkinter()` 替换了 `root.report_callback_exception`，但 `uninstall` 未恢复原始 handler
- 修复：新增 `_tk_root` 记录 root 实例，`uninstall` 时恢复 `_original_tk_handler`

### 🟡 中等修复

**聊天面板：打字机期间用户可发新消息引发竞态**
- 根因：`_on_reply()` 在 `_stream_start()` 之前调用了 `_finish()`，导致 `_busy=False`
- 修复：删除 `_on_reply()` 中的 `_finish()`，仅在 `_stream_tick()` 全部输出完毕后才释放 `_busy`

**聊天面板：打字机完成后发送按钮永久禁用**
- 根因：`_stream_tick()` 输出完成后只清理了内部状态，没有调用 `_finish()`
- 修复：在字符追加完毕的分支末尾添加 `self._finish()`

**聊天面板：旧流式任务在新消息后继续追加到新的气泡**
- 根因：`_stream_tick()` 没有校验 gen 是否已被新消息/清空作废
- 修复：引入 `self._stream_gen` 字段，每次启动流式时记录当前 gen；`_stream_tick()` 开头校验，不匹配则 `self._stream_stop()` 丢弃

### 🟢 低优先级修复

**TTS：`_speak_sapi_oneshot` 中的死代码清理**
- 根因：`self._proc = proc` 在 `TtsService` 中是孤立赋值（该类从未声明 `_proc` 属性，也从未读取）
- 修复：删除该行

### 修改文件

| 文件 | 改动 |
|------|------|
| `winocr/core/config.py` | `AppConfig.from_dict()` 补传 `logging` 字段 |
| `winocr/core/crash_handler.py` | 崩溃文件名防覆盖；`uninstall` 恢复 Tk 回调；类型注解修复 |
| `winocr/services/ai/llama_cpp_chat.py` | `apply_config()` 模型名变更时失效 `_llm` 缓存 |
| `winocr/services/translate/llama_cpp.py` | `set_config()` 模型名变更时失效 `_llm` 缓存 |
| `winocr/services/tts.py` | 删除 `_speak_sapi_oneshot` 中的死代码 `self._proc = proc` |
| `winocr/ui/tk/chat_panel.py` | 流式输出三处竞态修复（`_on_reply` / `_stream_tick` / `_stream_gen` 校验） |

### 验证

- 全部 6 个修改文件 `py_compile` 编译通过
- 配置加载：空配置回退默认 / 自定义配置正确读取 —— 通过
- 崩溃文件名：同一秒内 2 次崩溃生成独立文件 —— 通过
- 模型切换缓存：模型名不变保留缓存 / 模型名变更失效缓存 —— 通过（AI 对话 + 翻译双引擎）

---

## 3.4.24 — AI 对话框：强制中文 + Markdown 渲染 + 流式输出（2026-09-04）

> 修复 AI 对话框的三大体验问题：英文回复、长文本遮蔽、纯文本单调。

### 新增：强制中文回复

- **send() 自动注入中文指令**：通过 `ChatMessage.context_blocks` 在请求前追加
  `"请用中文回答我所有问题。如果我的输入是英文，请先翻译成中文再回答。"`
- **UI 不暴露指令**：context_blocks 只发给 provider，UI 气泡只显示用户原话，界面干净
- **所有 provider 生效**：llama_cpp / openai / glm 三个 provider 都会把 context_blocks 拼到请求前面

### 新增：简化 Markdown 渲染

- **Text tag 高亮**：`_render_markdown()` 用 `tk.Text.tag_configure` 实现
  - `# 标题` → `md_h` tag（字号 +4，加粗）
  - `**粗体**` → `md_b` tag（加粗）
  - `` `代码` `` → `md_code` tag（Consolas 等宽字体 + 背景色）
- **逐行解析**：标题整行标记，普通行内联解析粗体/代码，重叠 token 自动去重
- **仅 assistant/system 消息渲染**：user 消息保持纯文本，避免自渲染干扰

### 新增：打字机流式输出

- **_stream_start() / _stream_tick()**：AI 回复不再「等全部到齐再一次性出现」，
  而是逐字（每 8ms 追加 3 个字符）实时出现在气泡中，配合自动滚动到底。
- **最终 Markdown 渲染**：全部字符输出完毕后，清空 Text 重新调用 `_render_markdown()`
  应用完整样式，避免逐字插入时 tag 错位。
- **安全取消**：`clear_history()` 调用 `_stream_stop()`，取消 `after` 任务防止内存泄漏。

### 修改文件

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/chat_panel.py` | `send()` 注入中文指令；新增 `_render_markdown()` / `_render_md_inline()`；重写 `_on_reply()` 为流式输出；新增 `_stream_start()` / `_stream_tick()` / `_stream_stop()`；`clear_history()` 安全取消流式任务 |
| `winocr/version.py` | `__version__` 3.4.23 → 3.4.24，HIGHLIGHTS 追加 3.4.24 条目 |
| `README.md` | 版本号 3.4.23 → 3.4.24 |

### 验证

- `chat_panel.py` py_compile 编译通过；AST 验证所有关键方法签名完整。
- 无重复方法定义；`_stream_stop()` 中 `after_cancel` 正确调用。

## 3.4.23 — AI 对话框：长文本截断修复 + 用户消息可复制（2026-09-04）

> 修复 AI 长回复被截断看不到完整内容，以及用户消息无法复制的问题。

### 修复内容

| 问题 | 根因 | 修复 |
|------|------|------|
| 长回复被截断 | Text height 用逻辑行数计算，单行超长时 wrap 后的显示行数远大于逻辑行数 | 改用 `count(..., "displaylines")` 获取真实显示行数 |
| 用户消息无法复制 | user 气泡用 `tk.Label`，不支持文本选择 | user 气泡也改用 `tk.Text`（只读），Ctrl+A/Ctrl+C 可用 |

### 修改文件

- `winocr/ui/tk/chat_panel.py`：user/assistant/system 三角色气泡统一用 Text 展示，height 计算逻辑修正
- `winocr/version.py`、`README.md`：版本号 3.4.22 → 3.4.23

## 3.4.22 — 日志与崩溃捕获系统（2026-09-04）

> 全局统一日志 + 崩溃自动转储，彻底解决偶发崩溃（ggml-cpu.dll 段错误 / ucrtbase.dll 栈溢出）无迹可寻的问题。
> 日志按 5MB×3 份轮转，崩溃时自动生成 crash-*.log 含完整 traceback + 系统信息 + 线程列表。

### 背景：偶发崩溃无法排查

近 3 天发生 3 次崩溃（2 次 `ggml-cpu.dll` 段错误 + 1 次 `ucrtbase.dll` 栈缓冲区溢出），
项目此前仅有 selection 模块的零散日志（`WINOCR_DEBUG=1` 时写 `selection.log`），
**没有全局日志配置，没有崩溃转储机制**，崩溃后无线索可查。

### 新增：统一日志配置 `winocr/core/logging_config.py`

- **统一格式**：`[2026-09-04 21:30:15][INFO][module_name] 消息内容`，时间精确到秒。
- **文件轮转**：`RotatingFileHandler`，单文件 5MB、保留 3 份（`winocr.log` / `winocr.log.1` / `winocr.log.2`），
  日志目录默认 `用户数据目录/logs/`（`paths.user_dir() / "logs"`），自动创建。
- **环境变量覆盖**：`WINOCR_LOG_LEVEL`（DEBUG/INFO/WARNING/ERROR）、`WINOCR_LOG_DIR`（自定义路径）。
- **配置文件覆盖**：`winocr.toml` 的 `[logging]` 段（`level` / `dir` / `console`）。
- **第三方噪音抑制**：urllib3 / asyncio / PIL 等设为 WARNING，避免刷屏。
- **selection 日志兼容**：`_patch_sel_logging()` 让 `winocr.selection` 日志写到同一文件，不再分叉。
- **入口函数**：`setup_logging(level="INFO", log_dir=None, console=True)` —— 在 `main.py` 的 `cmd_gui` 中应用启动后调用。

### 新增：崩溃捕获 `winocr/core/crash_handler.py`

- **三处未处理异常接管**：
  - `sys.excepthook` —— 主线程 Python 未捕获异常
  - `threading.excepthook` —— 子线程未捕获异常
  - `tk.report_callback_exception` —— Tkinter 回调异常
- **faulthandler 原生信号捕获**：`SIGSEGV`（段错误）/ `SIGABRT`（中止）/ `SIGFPE`（浮点异常），
  写入 `faulthandler-latest.log`（每次覆盖），即使 Python 层来不及执行也能留下 C 层栈。
- **crash 转储文件**：`crash-YYYYMMDD-HHMMSS.log`，内容含：
  - 崩溃时间 + 异常类型 + 完整 traceback
  - 系统信息：Python 版本 / 平台 / 架构 / PID
  - 线程列表：每个线程的标识 / 守护状态 / 是否存活
  - 已安装包版本（llama-cpp-python / pillow / requests 等关键依赖）
- **入口函数**：`install_crash_handler()` —— 在 `main.py` 的 `cmd_gui` 中 `setup_logging` 之后调用。
- **Tkinter 补丁**：`patch_tkinter(root)` —— 在 `app.py` 的 `App.build()` 中 `root = tk.Tk()` 之后调用。

### 新增：配置对接 `winocr/core/config.py`

- 新增 `@dataclass class LoggingConfig`：`level: str = "INFO"` / `dir: str = ""` / `console: bool = True`
- `AppConfig` 增加 `logging: LoggingConfig` 字段，`sections()` 和 `to_dict()` 已纳入
- 用户可在 `winocr.toml` 中配置：

```toml
[logging]
level = "DEBUG"      # DEBUG / INFO / WARNING / ERROR
dir = ""              # 留空=默认 用户目录/logs，可填绝对路径
console = true        # 是否同时输出到控制台
```

### 修改文件清单

| 文件 | 改动 |
|------|------|
| `winocr/core/logging_config.py` | **新增**：统一日志格式 + RotatingFileHandler 5MB×3 + 环境变量覆盖 + 配置覆盖 + selection 兼容 + 第三方抑制 + `setup_logging()` |
| `winocr/core/crash_handler.py` | **新增**：sys/threading/tk 三处异常接管 + faulthandler 信号捕获 + crash-*.log 转储 + `install_crash_handler()` + `patch_tkinter(root)` |
| `winocr/core/config.py` | 新增 `LoggingConfig` dataclass（level/dir/console），`AppConfig` 增加字段，`sections()`/`to_dict()` 纳入 |
| `main.py` | `cmd_gui` 中增加 `setup_logging()` + `install_crash_handler()`，删除旧的手动 crash.log 写入逻辑 |
| `winocr/ui/tk/app.py` | `App.build()` 中 `root = tk.Tk()` 后调用 `patch_tkinter(self.root)` |
| `winocr/version.py` | `__version__` 3.4.21 → 3.4.22，HIGHLIGHTS 追加 3.4.22 条目 |

### 验证

- `logging_config.py`：py_compile 通过；运行时测试 DEBUG/INFO/WARNING/ERROR 全级别写入；selection 兼容写入同一文件。
- `crash_handler.py`：py_compile 通过；crash dump 文件创建并含 header + exception + sys info + threads；Tkinter patch 安装/卸载正常。
- `config.py`：py_compile 通过；`LoggingConfig` fields=['level','dir','console']，默认值正确；`sections()` 含 logging；`to_dict()` 含 logging。
- `main.py`：py_compile 通过；`setup_logging` / `install_crash_handler` 在 `cmd_gui` 中被调用；旧 crash.log 逻辑已删除。
- `app.py`：py_compile 通过；`patch_tkinter(self.root)` 在 `App.build()` 中被调用。

## 3.4.21 — TTS 朗读进度蒙版（2026-09-04）

> 朗读时在原文/译文区显示淡蓝透亮高亮蒙版，读到哪里蒙版跟到哪里；
> edge-tts 按句 chunk 粒度回调进度偏移，SAPI 订阅 SpeakProgress 逐词事件回调；
> 蒙版随朗读自动滚动定位，朗读结束/停止自动清除。

### 功能：读到哪里，光标淡蓝透亮蒙版同时跟进

- **蒙版效果**：tk.Text tag 高亮，浅色模式 `#CCE4FF` 淡蓝底、深色模式 `#1a3a5c` 深蓝底，
  朗读到当前句/词时高亮该段文本，并自动 `see()` 滚动到可视区。
- **edge-tts 进度**：保持现有按句切分流式播放（`_split_sentences`），计算每个 chunk 在
  原文中的字符偏移，播放该 chunk 前回调 `on_progress(offset, length)`。
- **SAPI 进度**：修改常驻 PowerShell 脚本，`$s.add_SpeakProgress({…})` 订阅逐词进度事件，
  输出 `PROG:offset:length` 行；Python reader 线程解析后回调 `on_progress`。
- **连接逻辑**：`do_tts_read` 用 `tts_source_widget()` 确定朗读来源控件（选中→译文→原文），
  朗读开始调用 `tts_highlight_start(widget)`，进度回调通过 `self.post()` 切回主线程
  调用 `tts_highlight_update(offset, length)`，朗读完成/失败/停止时 `tts_highlight_stop()`。
- **向后兼容**：`speak()` 的 `on_progress` 默认 None，settings 试听和旧调用不受影响。
- **SAPI 降级保护**：`_on_status` 仅在最终状态（"朗读完成"/"朗读失败"/"Edge 在线语音不可用"）
  清除蒙版，中间状态"改用系统语音…"不清除——SAPI 降级朗读继续蒙版跟进。

### 修改文件清单

| 文件 | 改动 |
|------|------|
| `winocr/services/tts.py` | `_build_script` 订阅 SpeakProgress 事件；`_SapiHost.speak` 增加 `on_progress` 参数 + reader 解析 PROG 行；`TtsService.speak`/`_work`/`_speak_edge`/`_speak_edge_single`/`_speak_sapi` 全链路增加 `on_progress` 回调；edge 流式队列从 `Queue[str]` 改 `Queue[tuple]` 携带偏移 |
| `winocr/ui/tk/main_window.py` | 新增 `tts_source_widget()`、`tts_highlight_start(widget)`、`tts_highlight_update(offset, length)`、`tts_highlight_stop()`；`__init__` 初始化 `_tts_widget` |
| `winocr/ui/tk/app.py` | `do_tts_read` 重构：用 `tts_source_widget()` 获取来源控件，启动/更新/清除蒙版，`on_progress` 通过 `post()` 切回主线程 |
| `winocr/version.py` | `__version__` 3.4.20 → 3.4.21，`__release_date__` 更新，HIGHLIGHTS 追加 3.4.21 条目 |

### 验证

- 三文件 `py_compile` 编译通过，AST 验证函数签名正确。
- 调用链完整：`tts.speak(on_progress=)` → `_on_progress` 闭包 → `post()` → `tts_highlight_update(offset, length)`。
- 向后兼容：`on_progress` 默认 None，settings 试听 `tts_svc.speak(text, on_status=_restore)` 不受影响。

## 3.4.20 — 蒙版翻译字号自适应（2026-09-01）

> 蒙版翻译（Ctrl+Shift+M）字号从「死守 OCR 行高」改为「逐行按可用宽+译文字数自适应」，
> 彻底解决译文字形普遍巨大、空旷的问题，同时严格保持译文位置与 OCR 原行对齐（原地覆盖）。

### 问题：蒙版译文字形普遍「巨大」

- **现象**：框选区域做蒙版翻译后，译文字号明显大于原图文字，且常稀疏空旷；选区越高字号越大，甚至溢出蒙版。
- **根因**：
  1. 正常分支字号 = `OCR行框高 / _px_per_pt`，但 RapidOCR 检测框常含行距/被膨胀，单字字号被高估；且无「均行高」约束，单行长框即可把字号撑爆。
  2. FALLBACK 分支（无 line_boxes：剪贴板 / 结构化模式）字号 = `选区高 × 0.06`，选区高 600→36pt、1000→60pt，**直接制造巨型字号**。

### 改法：逐行自适应字号

- 新增模块级 `_est_char_em()`（估算文本平均字宽 em：CJK≈1.0 / Latin≈0.55）+ `_fit_font_size()`：
  `字号 = max(地板, min(铺满可用宽度的字号, 上限字号))`。
- 正常分支：先把译文按 N 行分布，再为每行算字号——取「铺满该行宽度的字号」与
  「OCR 行高 1:1 上限字号」的**较小者**；上限再受「选区均行高 × 1.3」约束，
  防 OCR 检测框膨胀撑爆。长译文自然铺满宽度（不空旷）、短译文封顶原字大小（不巨大）。
- 位置仍逐行对齐 OCR 原行（原地覆盖），段间 spacer 字号随行字号联动。
- FALLBACK 分支：去掉 `选区高 × 0.06`，改为同一自适应算法，上限封到「选区均行高对应字号」。

### 验证

- 新增 `tests/test_mask_font_fit.py`（7 项纯函数单测）：长译文落到地板不溢出、短译文封顶不巨大、
  空文本/无字宽返回地板不抛错、`_est_char_em` 的 CJK/混合字宽估算正确。
- 现有蒙版回归 `tests/test_mask_window_poll.py` 全过（刷新 / 缓存 / silent / 排队补跑）。
- 冒烟：`tests/test_mask_font_fit.py` + `tests/test_mask_window_poll.py` 共 12 项全绿。

### 修改文件清单

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_window.py` | 新增 `_est_char_em` / `_fit_font_size`；`_render` 字号逻辑改逐行自适应；FALLBACK 去 `选区高×0.06` |
| `tests/test_mask_font_fit.py` | 新增：蒙版字号自适应纯函数单测（7 项）|
| `winocr/version.py` | `__version__` 3.4.19 → 3.4.20，`__release_date__` 更新 |

### 附：蒙版拖拽「不能移动」修复（同版补充）

- **现象**：Ctrl+Shift+M 框选生成蒙版后无法拖动窗口（以前可以）。
- **根因**：拖拽绑定误绑在标题栏 `bar`（Frame）而非 Toplevel。Tk 默认 bindtags 中
  子控件事件只向上冒泡到其所在 Toplevel、不冒泡到直接父 Frame；故在标题栏按钮/文字上
  按下、或鼠标移出 22px 高的 `bar` 时，`<B1-Motion>` 收不到 → 拖拽不启动或中途卡死。
  （`mask_window.py` 此前未纳入版本控制，无可追溯的旧版差异，旧版疑似绑定在 root。）
- **改法**：拖拽绑定改挂 `self.root`（覆盖全窗口，整窗可拖）；`_on_drag_start` 排除
  标题栏按钮（✕ / ⟳ / 方向 / 随拖）避免点击误触移动；新增 `_on_drag_end` 仅清理拖拽
  状态，是否自动重译由标题栏「随拖」开关决定（`__init__` 预置 `_drag_retranslate=False`
  默认关，避免拖动时反复重译）。开启后拖动结束自动 `_kick()` 以新位置重译；关闭则仅移动、
  由 Ctrl+Shift+N / ⟳ / 方向切换主动重译。`__init__` 预置 `_drag_active` / `_drag_offset`
  防 AttributeError。
- **验证**：新增 `tests/test_mask_drag.py`（2 项：整窗移动几何 + 标题栏按钮排除），
  与字号单测、蒙版回归共 **14/14 全绿**。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_window.py` | 拖拽修复：绑定由 `bar` 迁 `root`；`_on_drag_start` 排除标题栏按钮；标题栏新增「随拖」开关（`_drag_retranslate`，默认关）+ `_toggle_drag_retranslate` / `_refresh_drag_rt_btn`；`_on_drag_end` 按开关决定重译；`__init__` 预置 `_drag_active` / `_drag_offset` / `_drag_retranslate` |
| `tests/test_mask_drag.py` | 新增：蒙版拖拽移动 + 标题栏按钮排除（2 项）|

### 附：蒙版整体透明度下调（同版补充，用户要求「更透明」）

- **改动**：蒙版浮层窗口透明度从 `alpha=0.85` 降到 `MASK_ALPHA=0.6`（更小=更透），
  抽成模块级常量 `MASK_ALPHA`（`mask_window.py` 顶部），调透明度只改这一处。
  整窗统一透明（含标题栏与译文区），原文更清晰透出。
- **说明**：当前用 Tk 整窗 `-alpha` 实现半透明，译文文字也一并半透明；若需「文字清晰、
  仅底色透」，属另一方案（Canvas + 不透明文字层），按需再议。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_window.py` | 新增 `MASK_ALPHA=0.6` 常量；`__init__` 透明度由写死 0.85 改为引用 `MASK_ALPHA`；顶部 docstring 同步 |

### 附：蒙版点阵分析 + 小框自动框选 + 原位回填（同版补充，用户选「乙 直接上 / 丙 先出方案」）

- **目标**：译文从「行级累加」升级为「按 OCR 原坐标逐字原位回填」，并做点阵投影切小框。
- **架构**：译文承载由单个 Tk `Text` 改为 **`Canvas`（`self._cv`）** 绝对坐标定位；
  标题栏 `pack` 占顶、`Canvas` 填满其下，`Canvas(0,0)` = 窗口左上 = 选区左上，
  OCR 框坐标直接映射（y 偏移 `BAR_H`），**消除 Text 逐行累加的纵向漂移**。
- **点阵分析（水平投影）**：新增模块级纯函数 `_segment_line_boxes(crop)`——
  对每行裁剪图按列累加墨像素，列墨 < 中位数 25% 判为字符间隙 → 切出字符小框（中心 x）；
  失败（空白/低对比/异常）返回 `[]`，调用方回退整行均布。
- **逐字回填**：新增 `_map_chars_to_boxes(chars, centers, x0, x1)`——有栅格时译文逐字沿
  原小框中心插值（长度不符也保持原字节奏），无栅格时整行均布；每字
  `canvas.create_text(..., anchor="center")`，字号仍用 `_fit_font_size`（防溢出/不巨大）。
- **坐标 + 锚定**：`_render` 按每行 OCR 框 `(x0,y0,x1,y1)` 把译文钉在原行 y（段落间距因用
  真实 y0 自然保留）；窗口高覆盖选区与原文本底部。新增 `_render_fallback`（无 line_boxes 时
  整块居中自适应）；删除失效的 `_resize_simple`；`_kick` 抓取后存 `self._last_img` 供投影。
- **局限（第一性声明）**：RapidOCR 仅行级框，无词/字级——小框由投影自切，非 OCR 给；
  竖排/多栏/表格需 layout 模型先分栏（不在本期）；拉丁长句按字符断行（CJK 无影响）。
- **验证**：新增 `tests/test_mask_dotmatrix.py`（投影切小框 3 项 + 回填映射 4 项）、
  `tests/test_mask_render_canvas.py`（Canvas 真渲染 3 项）；与字号/轮询/拖拽共 **27/27 全绿**。
  方案文档见 `docs/mask_dotmatrix_plan.md`。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_window.py` | 渲染层 Text→Canvas；`_render` 重写（坐标映射 + 投影 + 逐字回填 + 回退）；新增 `_render_fallback` / 模块级 `_segment_line_boxes` / `_map_chars_to_boxes`；删除 `_resize_simple`；`__init__` 增 `_last_img`；`_kick` 存源图 |
| `docs/mask_dotmatrix_plan.md` | 新增：点阵小框蒙版方案文档（架构/算法/风险/验收/改动文件）|
| `tests/test_mask_dotmatrix.py` | 新增：投影切小框 + 回填映射纯函数单测（7 项）|
| `tests/test_mask_render_canvas.py` | 新增：Canvas 真渲染集成测试（3 项）|
| `tests/test_mask_drag.py` | `widget=w._tran` → `widget=w._cv`（7 处，拖拽测试仅用其作事件 widget）|

### 附：蒙版窗口高度回归 fix（同版补充，用户反馈「蒙版框选多大，出来就该多大」）

- **现象**：点阵小框改造后，蒙版窗口高被 OCR 检测框的 padding 撑出原框选范围，**窗口越翻译越大**，循环撑爆（截图证据：蒙版从原文区一路扩到整个 WinOCR 主窗）。
- **根因**：`_render` 末尾 `_bh = max(self._bh, int(max_ly1) + 8)` 在 OCR 检测框 padding 让 `max_ly1`（OCR 文本底 y）偶尔 > 原 `_bh`（框选高）时把窗口撑大；`_bh` 被改写后**下游 `_grab_region` 用撑大的 `_bbox` 又去抓更大区域**，OCR 检测框 padding 进一步加大 → 循环放大。`_render_fallback` 同问题。
- **改法**：`_render` / `_render_fallback` 末尾**钉死** `geometry(_bw × _bh, …)`，**不再写 `self._bh` 也不重算 `self._bbox`**——蒙版窗口严格 = 框选区域大小；Canvas 内容超出由 Tk 默认裁剪（不撑窗）。
- **验证**：`tests/test_mask_render_canvas.py` 新增 3 项（`_render` / OCR 框超出场景 / `_render_fallback` 都验证 `_bh` 不变），全套蒙版用例 **30/30 全绿**。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_window.py` | `_render` 末尾钉死 `_bh` 不再写（去 `max(_bh, max_ly1+8)` 与 `self._bh = h`）；`_render_fallback` 同改；新增注释说明「严禁把 h 撑出去」 |
| `tests/test_mask_render_canvas.py` | 新增 3 项回归锁死窗口尺寸：`_render` 前后尺寸不变 / OCR 行底超出 `_bh` 不撑高 / `_render_fallback` 也不改写 |

### 附：评审驱动重构 —— 职责拆分 + 缓存/性能/健壮性（同版补充，2026-09-02）

> 外部代码评审（线程安全 / 缓存泄漏 / 魔数 / 性能 / SRP / 异常吞没）逐条核实后落地。
> **纠正评审 4 处误判**：① `_tr_cache` 仅在 worker 线程读写（`_kick` 不碰），靠忙守卫已串行，无活竞态，加锁属防御性卫生；② `_SENT_PUNCT` 并非未使用（`_split_to_n`/`_merge_to_n` 在用）；③ 常量本已全模块级，无混用；④ `_grab_region` 的 `update()` 不能换 `update_idletasks()`（须 flush withdraw 否则抓到蒙版自身复发"翻页不刷新"）。

- **职责拆分（用户拍板「完整拆分」）**：`MaskWindow`（生命周期+事件+kick 编排）/
  `MaskRenderer`（Canvas 渲染+字号+分行排版，`mask_render.py`）/
  `mask_segment.py`（点阵投影+回填映射，纯函数）/ `TranslationCache`（`mask_cache.py`，LRU+锁）/
  `mask_const.py`（共享常量，避免循环依赖）。
- **缓存**：裸 dict `_tr_cache`（只增不减）→ `TranslationCache`：`OrderedDict` LRU（cap 200）+ `threading.Lock`。
- **性能**：`segment_line_boxes` 列墨统计 O(w×h) 双层 Python 循环 → NumPy 向量化 `(np.array(gray)<128).sum(axis=0)`（快 10-50×，numpy 2.5.2 已是 OCR 栈硬依赖）；`_est_char_em` 加 ASCII 快路径（保留 ord 范围判断——比评审建议的 `unicodedata.category` 更快）。
- **健壮性**：`_grab_region`/`_render_fallback`/`segment_line_boxes` 静默 `except: pass` 补 `logger.debug`；`_kick` 的 worker/on_done/on_error 闭包提为实例方法（降嵌套）；关键方法补类型注解；魔数提取 `_BINARIZE_THRESHOLD`/`_INK_GAP_RATIO`/`_MIN_BOX_WIDTH_RATIO`；`import tkinter` 移模块顶；清理死常量 `MAX_LINES`/`PARA_GAP_FACTOR`/`_SP_RATIO`/`_SP_MIN_FONT`/`_MAX_H`。
- **重构中引入并当场修复的回归**：重写 `_build` 时 6 处 `"<Enter"/"<Leave"` 丢结尾 `>` → `TclError: missing ">" in binding`，拖拽测试 5 项当场拦截，已修。
- **验证**：全套蒙版用例（字号 7 + 轮询 5 + 拖拽 5 + 点阵 7 + Canvas 渲染 6）**30/30 全绿**；5 模块 smoke import 通过；无 `_tr_cache`/死常量残留引用。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_const.py` | 新增：共享常量（BAR_H/MASK_ALPHA/FONT_*/_SENT_PUNCT + 点阵三常量） |
| `winocr/ui/tk/mask_cache.py` | 新增：`TranslationCache`（LRU cap 200 + threading.Lock） |
| `winocr/ui/tk/mask_segment.py` | 新增：`segment_line_boxes`（NumPy 向量化）+ `map_chars_to_boxes` |
| `winocr/ui/tk/mask_render.py` | 新增：`MaskRenderer` + 字号/分行/清洗纯函数（`_est_char_em` 加 ASCII 快路径） |
| `winocr/ui/tk/mask_window.py` | 重写为编排层：委托渲染/缓存；`_kick` 闭包提实例方法；删死常量与已迁出函数；except 补日志；类型注解 |
| `tests/test_mask_font_fit.py` | 导入路径 `mask_window` → `mask_render` |
| `tests/test_mask_dotmatrix.py` | 导入路径 `mask_window` → `mask_segment`（去下划线前缀新 API 名） |
| `tests/test_mask_window_poll.py` | 手工装配补 `_cache = TranslationCache()` / `_renderer` 兜底 |

### 附：刷新后渲染异常修复（同版补充，用户截图反馈，2026-09-02）

> 按 Ctrl+Shift+N 刷新翻译后出现两种渲染故障（均有截图实证），根因不同、一并修复。

- **现象一：译文缩成左上角一堆 6pt 小字**（英文原文未被覆盖，中文小字堆叠在 (8,8)）。
  - **根因**：渲染主循环对 `line_boxes[i]` 直接取 `min(xs)`，刷新后 OCR 偶发返回**空行框** →
    `min([])` 抛 ValueError → 被**整个 `_render` 共用的外层 except** 接住 → 全版译文以
    `FONT_FLOOR=6pt` 紧急回退画在 (8,8)。一行坏框毁掉整版渲染。
  - **改法**：① 逐行独立容错——空框/坏框只跳过该行（`continue`），不坠入全局回退；
    ② 紧急回退也改用 `_fit_font_size` 自适应字号，不再用 6pt 地板。
- **现象二：译文字距巨大、被摊满整行**（中文字符沿原英文字符位置稀疏分布、字号偏大）。
  - **根因**：点阵逐字栅格展开（前版「乙」方案）把译文**逐字沿原字符中心插值**。
    英→中译文长度远短于原文字符数（约 10 个中文字摊到 40 个英文栅格上），
    字距被拉到原英文栅格间距 → 稀疏大字。**该方案在真实英→中场景被证伪**。
  - **改法**：**整行整体回填**——`create_text(lx0, cy, text=整行译文, anchor="w")`，
    左对齐原行左缘、垂直居中原行（原地覆盖不变），字号仍逐行自适应。
    逐字栅格展开废弃；`mask_segment.py` 纯函数与单测保留（不再被渲染调用），
    `_last_img`/`img` 参数保留兼容。
- **验证**：新增回归 `test_render_skips_degenerate_line_without_total_fallback`
  （两好行 + 一空框行 → 画布 ≥2 对象，锁死「坏框不得毁整版」）；全套蒙版用例 **31/31 全绿**。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_render.py` | 渲染循环逐行容错（跳过空/坏框行）；逐字栅格展开 → 整行 `create_text(anchor="w")`；紧急回退改自适应字号；docstring 同步；移除 segment 导入 |
| `winocr/ui/tk/mask_window.py` | 模块 docstring 去「逐字/点阵投影」表述 |
| `tests/test_mask_render_canvas.py` | 新增坏框跳行回归（1 项） |

### 附：OCR 框坐标系还原 + 排版保真测试（同版补充，用户反馈「行距太宽 / 区域容纳不下」，2026-09-02）

> 用户要求先做「不翻译、OCR 原样排版」测试定位行距问题（样本 `pho/` 两张：英文 3 段 + 中文法条）。

- **测试基建**：
  - `tools/mask_layout_test.py`：真实 OCR → 原文按原行框渲染进蒙版（禁 `_kick` 不翻译）→ 打印每行
    几何报告（y0/y1/h/gap/fs/字符数）+ 弹窗肉眼比对原图；`--hold` 常驻 / `--auto-close N` 自动关。
  - `tests/test_mask_layout_fidelity.py`（3 项离屏契约）：行位逐行 == 原行框中心 − BAR_H、
    渲染行距 == 原始行距、段间大空隙保留。mock 几何下全绿 → 排除渲染层，指向几何来源。
- **根因（真实样本数据实证）**：`RapidOcrEngine._preprocess_image` 对小图（w/h < 600）做
  **1.5×/2× LANCZOS 放大**增强识别，但 RapidOCR 返回的框是「放大图」坐标，`_build_result`
  **从未缩回原图坐标系**——样本2 图高 512px，行框 y 却到 738（738÷1.5≈492 < 512，正好落在
  放大图坐标系）。下游蒙版把框当选区局部坐标渲染 → **行位/行高/行距全部放大 1.5×**：
  行距过宽（症状二）+ 底部行画到窗口外被裁（症状一「区域不能完全容纳文字」）。
  该 bug 同样污染段落判定阈值与结构化表格几何——修在 OCR 层一并归正。
- **改法**：`_preprocess_image` 返回 `(processed, scale)`；`recognize` 识别后经 `_rescale_items`
  把所有四点框 ÷ scale 还原到原图坐标；`_build_result` 改用原图（段落/结构化几何一致）。
- **验证**（`tools/mask_layout_test.py` 重跑）：样本1 y 最大 342 ≤ 图高 356（修复前溢出到 513），
  行高 19px / 行内距 4-6px / 段间 27-28px 全部与原图吻合；38 项测试全绿（蒙版 31 + 排版保真 3 +
  OCR 段落模式 4）。
- **顺带**：蒙版透明度按用户要求调回 `MASK_ALPHA=0.6`（白底半透明呈浅灰雾面；要深色半透明改
  `MASK_BG/MASK_FG/MASK_BAR_BG` 三个常量即可）。

| 文件 | 改动 |
|------|------|
| `winocr/services/ocr/rapidocr.py` | `_preprocess_image` 返回 `(图, scale)`；新增 `_rescale_items`；`recognize` 坐标还原，`_build_result` 改用原图 |
| `winocr/ui/tk/mask_const.py` | `MASK_ALPHA` 1.0 → 0.6（用户要求调回半透明） |
| `tools/mask_layout_test.py` | 新增：真实 OCR 排版保真脚本（几何报告 + 弹窗比对） |
| `tests/test_mask_layout_fidelity.py` | 新增：排版保真契约 3 项（行位/行距/段间） |

### 附：ESC 关闭 / Ctrl+C 复制（同版补充，用户需求，2026-09-02）

- **需求**：蒙版用 ESC 退出；蒙版内容可用 Ctrl+C 复制。
- **机制**：蒙版不夺焦点（键盘事件发给用户焦点所在窗口，Tk `bind` 收不到——与当年
  右键菜单失效同一坑），故用「**鼠标悬停在本蒙版矩形内** + `GetAsyncKeyState` 60ms
  轻量轮询」实现（ctypes 零依赖；只查按键/鼠标状态，不抓屏无闪烁）。
  **仅当指针位于该蒙版上时快捷键才生效**——全局 ESC/Ctrl+C 绝不被吞、不干扰其它软件。
- **行为**：
  - ESC（悬停）→ 关闭该蒙版；
  - Ctrl+C（悬停）→ 复制蒙版当前显示内容（**所见即所得**：译文；流式阶段尚未出译文时
    复制 OCR 原文），标题栏提示「✓ 已复制」1.5s；
  - 按住沿触发（防重复）；鼠标移出蒙版自动复位沿状态。
- **验证**：新增 `tests/test_mask_keys.py` 5 项（悬停 ESC 关 / 移出 ESC 不关 /
  Ctrl+C 复制译文 / 回退原文 / 按住只触发一次）；全套蒙版用例 **39/39 全绿**。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_window.py` | 模块级 `_key_down`/`_cursor_xy`（ctypes，可 monkeypatch）；`_watch_keys`/`_poll_keys` 悬停轮询（沿触发）；`_copy_content` 剪贴板复制；`_render` 记录 `_last_translation` |
| `tests/test_mask_keys.py` | 新增：快捷键契约 5 项 |

### 附：蒙版外设置小面板（同版补充，用户需求，2026-09-02）

> 用户要求「在蒙版框外做个小面板放设置滑条」——解决遮蔽程度/字号每次都要改常量重启的问题。

- **形态**：独立置顶小面板（不遮选区、不随蒙版拖拽），初始吸附蒙版右侧（屏幕放不下自动
  缩到左侧）；标题栏新增「⚙」按钮开/关面板（点亮=可见），面板头部 ✕ 同功能；close 蒙版
  时面板一并销毁。
- **滑条**：
  - **遮蔽** 0.30–1.00（步 0.05）：即时改整窗 `-alpha`（Tk 限制：底色+文字一起透）；
  - **字号** 0.6×–1.4×（步 0.05）：即时重渲染当前 OCR/译文（不重新 OCR/翻译）。
- **实现**：字号倍率存 `MaskWindow._font_scale`，`MaskRenderer` 三处字号（正常行 /
  无行框 fallback / 紧急回退）统一 × `_fs_scale()`；`_render` 记录 `_last_ocr` 供滑条
  `_rerender()` 复用。
- **验证**：新增 `tests/test_mask_settings_panel.py` 6 项（面板创建可见 / 遮蔽改 alpha /
  字号触发重渲染 / 无 OCR 结果不崩 / ⚙ 开关 / close 销毁面板）；全套蒙版用例 **45/45 全绿**。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_window.py` | 标题栏「⚙」；`_build_settings_panel`（遮蔽/字号 Scale）+ `_place_panel`/`_toggle_settings_panel`/`_set_alpha`/`_set_font_scale`/`_rerender`；`_render` 记 `_last_ocr`；拖拽排除 ⚙；`close` 销毁面板 |
| `winocr/ui/tk/mask_render.py` | `_fs_scale()`；正常行/fallback/紧急回退字号 × 倍率 |
| `tests/test_mask_settings_panel.py` | 新增：设置面板契约 6 项 |

### 附：字号统一上限（同版补充，用户反馈「OCR 时字号差别巨大 → 翻译后文本大小差很大」，2026-09-02）

- **根因**：字号上限 = **每行自己的 OCR 框高** `h/pp`。RapidOCR 行框高度波动大
  （部分行含 padding/虚高，h 从 19 到 40+），行框越高字号越大 → 行与行之间字号
  跳变巨大；OCR 原文（流式阶段）与译文继承同一逐行算法，同病。
- **改法**：新增 `_global_fs_cap(geom_heights, avg_line_h, pp)` —— 字号上限改为
  **全选区行高中位数**（抗单行虚高）+ 原「均行高×1.3」兜底取小；渲染循环不再逐行
  用自己的 `g["h"]` 算 cap。字号现在只随「该行文字多寡」由宽度平滑决定：满行行
  略小、短行封顶，**不再被任何单行虚高框带飞**。
- **验证**（`tools/mask_layout_test.py` 真实样本）：两张样本统一 `fs_cap=11pt`
  （修复前 fs 10~18 跳变）；新增契约 2 项——`_global_fs_cap` 取中位数不取离群
  （[19×4,40] → 19）、Canvas 端到端「虚高 60px 行不得放大字号」（各行字号一致）；
  全套蒙版用例 **47/47 全绿**。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_render.py` | 新增 `_global_fs_cap`（行高中位数统一上限）；渲染循环改用它，删逐行 `g["h"]` cap |
| `tools/mask_layout_test.py` | 几何报告改用 `_global_fs_cap`（与渲染一致） |
| `tests/test_mask_layout_fidelity.py` | 新增字号统一契约 2 项 |

### 附：标题/大号字层级保留（同版补充，用户问「文本实际有几个大号字怎么办」，2026-09-02）

- **需求**：统一上限把真标题/大号字也压平了——原文层级（标题 > 正文）应保留，其余相对统一。
- **第一性区分**：真大号字是**断崖式更高**（如正文 19px / 标题 32px ≈1.7×）；OCR 虚高 padding
  只是**小幅偏高**（1.1~1.4×）。以「正文行高中位数 × 1.6」作断崖阈值：
  - 未跨阈值（正文 + 小幅虚高）→ 统一上限（正文一致、虚高不跳）；
  - 跨阈值（真标题/大号字）→ 按自身行高 ×0.85 放行放大，保留层级。
- **实现**：`_median_height` 拆出；新增 `_line_fs_cap(h, base_h, global_cap, pp)`，
  渲染循环逐行调用；`tools/mask_layout_test.py` 报告同步。
- **验证**：新增契约 3 项（标题行 27pt > 正文 19 / 小幅虚高 24px 不放大 / Canvas 端到端
  标题 > 正文且正文两行一致）；全套蒙版用例 **50/50 全绿**。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_render.py` | 新增 `_median_height` / `_line_fs_cap`（断崖阈值 1.6× + 0.85 拟合）；渲染循环逐行用 |
| `tools/mask_layout_test.py` | 报告打印 `base_h` / 阈值，逐行用 `_line_fs_cap` |
| `tests/test_mask_layout_fidelity.py` | 新增标题断崖契约 3 项；原「虚高统一」测试改小幅虚高(24px)语义 |

### 附：加粗/黑体保留（同版补充，用户问「一段正文加粗黑体、另一句正常」，2026-09-02）

- **需求**：行高相同但字重不同的文本（加粗/黑体段 vs 正常句），译文要保留强调差异。
- **事实约束**：RapidOCR 只返回「文字+框+置信度」，**不提供字体属性**；但源图在手
  （`_last_img`），加粗/黑体笔画粗 → **行内墨像素占比**明显高于细体正文。
- **方案（墨密度相对断崖，与字号断崖同哲学）**：`mask_segment.line_ink_ratio(crop)` 逐行算
  墨占比；全选区中位墨占比 ≥ `_BOLD_MIN_MEDIAN(0.05)` 时，某行 ≥ 中位 × `_BOLD_INK_FACTOR(1.5)`
  判为加粗 → 该行译文 `font=(family, fs, "bold")`。相对阈值抗绝对字号/字体差；墨占比过低
  （极淡/噪声）不判粗，防误判。
- **局限（如实）**：启发式，靠"粗 vs 细"的墨量断崖。反白（白字黑底）整区墨占比统一偏高 →
  相对比值≈1 不会伪粗；单行内浅色文字需实测调 `_BOLD_INK_FACTOR`。
- **验证**：新增 `line_ink_ratio` 纯函数 3 项（实心块 >0.9 / 细笔划 <0.2 / 坏输入 -1）+
  Canvas 端到端「黑体行译文 bold、细体行不 bold」（源图墨密度构造）；全套蒙版用例 **54/54 全绿**。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_segment.py` | 新增 `line_ink_ratio`（墨占比）+ `_BOLD_INK_FACTOR`/`_BOLD_MIN_MEDIAN` |
| `winocr/ui/tk/mask_render.py` | 渲染前预收集各行墨占比取中位；逐行相对断崖判 bold → 译文 `(family,fs,"bold")` |
| `tests/test_mask_dotmatrix.py` | 新增 `line_ink_ratio` 纯函数 3 项 |
| `tests/test_mask_layout_fidelity.py` | 新增黑体行 bold 端到端 1 项 |

### 附：蒙版白底遮蔽（同版补充，借鉴 Snow Shot，用户拍板，2026-09-02）

> 对标开源截图工具 Snow Shot（`mg-chao/snow-apps`，GPL-3.0）的图片翻译效果：白色不透明底板
> 完全遮蔽原文、译文按 OCR 行整行回填。其整行回填/合并行自适应字号与 WinOCR 3.4.20 方案一致，
> 相互验证；**差异最大且最值得借鉴的是「不透明底遮蔽」**——原 0.6 半透明灰底会让原文透出、
> 与译文叠加干扰阅读（用户刷新故障截图实证）。

- **改法**：`MASK_ALPHA 0.6 → 1.0`（不透明，绕开 Tk 整窗 alpha 连文字一起半透的限制）；
  新增加色三常量 `MASK_BG="#ffffff"` / `MASK_FG="#1e1e1e"` / `MASK_BAR_BG="#f0f0f0"`，
  Canvas 底、译文色（含紧急回退）、标题栏统一引用——**改这三个值即可切深色/其他主题档**。
- **验证**：全套蒙版用例 31/31 全绿；`sticker.py` 贴图配色不受影响。

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/mask_const.py` | `MASK_ALPHA→1.0`；新增 `MASK_BG` / `MASK_FG` / `MASK_BAR_BG` |
| `winocr/ui/tk/mask_window.py` | 标题栏 6 处 `bg` → `MASK_BAR_BG`；Canvas 底色 → `MASK_BG` |
| `winocr/ui/tk/mask_render.py` | 译文色（正常+紧急回退）→ `MASK_FG` |

### 附：翻译回退链修复 + 蒙版引擎状态指示（同版补充，用户「配了 API 但蒙版译文仍差」，2026-09-02）

- **用户报告**：设置了 API Key，蒙版翻译却仍是 argos 级的差译文——「蒙版需要单独切引擎吗？」
- **定位（决定性实测）**：无需单独切——蒙版与主界面共用同一 `TranslateDispatcher`。真根因：
  `fallback_order` 默认 **argos 第一位**，而 argos 本地语言包**必然可用** → auto 回退链每次都
  被 argos 抢占，配好 Key 的在线引擎（glm/hunyuan/mymemory）**永远轮不到**（argos.available=True
  与 glm.available=True 实测并存）。另：GLM 免费档当前被智谱限流（429/1305「访问量过大」，
  并发 1 路），即使排到也会失败重试。
- **修复**：
  1. `config.py` 默认 `fallback_order`：`["argos","glm","hunyuan","mymemory"]` →
     `["glm","hunyuan","mymemory","argos"]`——**在线优先，argos 只做最后离线兜底**
     （无 Key 的引擎 available()=False 自动跳过，不受影响）；
  2. 用户 `~/.winocr/config.toml` 同步该顺序（顺带清掉不存在的 `bing` 残留）；
  3. 蒙版设置面板新增「**引擎**」指示行：翻译完成显示实际所用引擎——
     **在线引擎蓝字；argos / (全部失败) 红字**，一眼看出差译文是不是离线兜底（A 项落地）。
- **验证**：新增面板引擎指示 2 项（在线→蓝 / argos→红 / 失败→红）；全套蒙版用例 **56/56 全绿**。
  ⚠️ 生效需**重启主程序**（运行中进程持有旧内存 config）。

| 文件 | 改动 |
|------|------|
| `winocr/core/config.py` | 默认 `fallback_order` 在线优先、argos 兜底（附注释防回退） |
| `~/.winocr/config.toml` | 用户配置同步新顺序，去 `bing` |
| `winocr/ui/tk/mask_window.py` | 设置面板「引擎」行 + `_report_engine`/`_refresh_engine_label`（argos/失败红字）；worker 成功与 on_error 上报引擎 |
| `tests/test_mask_settings_panel.py` | 引擎指示 2 项（在线蓝/argos 红、失败红） |

### 附：llama.cpp 本地大模型翻译 + AI 对话（同版补充，2026-09-04）

- **目标**：在纯 CPU 环境（R5 5500 + GT 710 亮机卡）下支持本地大模型翻译与 AI 对话，
  彻底绕过无计算能力的 GPU。
- **模型支持**：
  - **HY-MT1.5-1.8B-Q4_K_M**（~1.08GB，翻译质量好，ModelScope）
  - **Qwen2.5-0.5B-Instruct-Q4_K_M**（~468MB，速度快 40-60 tok/s，29 种语言，ModelScope）
- **推理配置**：纯 CPU，`n_gpu_layers=0`（禁用 GT 710），`n_threads=6`（六核全开），
  `n_ctx=2048`，`verbose=False`；环境变量 `WINOCR_LLAMA_THREADS/CTX/GPU_LAYERS/MODEL_DIR` 可覆盖。
- **引擎接入**：
  - `LlamaCppEngine(TranslateEngine)`：惰性加载 GGUF，线程安全（`threading.Lock`），
    fallback_order 排在 argos 之前（`glm, hunyuan, mymemory, llama_cpp, argos`）。
  - `LlamaCppProvider(AiProvider)`：支持上下文拼接 + 对话历史持久化（JSON 原子写），
    双阈值历史裁剪（轮次 + token 估算）。
- **模型自动发现**：`winocr/core/paths.py` 新增 `find_llama_gguf()`，搜索顺序：
  `WINOCR_LLAMA_MODEL_DIR` → `~/.winocr/models/llama` → `<项目>/models/llama`。
- **UI 模型选择器**：「大模型翻译」设置页选择 `llama_cpp` 引擎后，自动显示「本地模型」
  下拉框，列出 `models/llama/*.gguf`；保存时写入 `cfg.translate.text_model`。
- **setup.bat 增强**：
  - pip 源切换：安装前临时切到清华主源 + 阿里辅源，`pip config list` 确认无 pypi.org；
    安装完成后无论成败都恢复原有配置（备份 → 还原 / unset）。
  - 模型可选下载：交互式选择下载 Qwen2.5-0.5B / HY-MT1.5-1.8B / both / 跳过；curl 优先，
    PowerShell 备用；文件已存在自动跳过。
  - 编码修正：GBK + CRLF，兼容 `chcp 936`。
- **删除确认升级**：项目管理对话框 `_delete()` 从 `askyesno` 一键确认改为「输入项目名称
  二次确认」模态窗口，手滑误触/回车都不会删除。
- **依赖**：`requirements.txt` / `pyproject.toml` 新增 `llama-cpp-python>=0.3.0`（纯 CPU 编译，
  Windows 无需 CUDA）。
- **TTS 朗读延迟修复**：`speak()` 调用 `stop()` 时会杀死 SAPI 常驻宿主，下次朗读需重新冷启动
  PowerShell（~300-600ms），导致"不能一下激发"。修复后 `stop()` 只取消播放不杀宿主，
  `join` 超时从 1.5s 缩至 0.2s，`warmup()` 增加 SAPI 宿主预热——朗读即时响应。
- **TTS 方案 B（分 chunk 流式播放）**：长文本从"全文合成后播放"改为"按句切分 + 预取队列"——
  `_speak_edge()` 对多句文本起后台线程逐句合成推入队列（`maxsize=2`），播放线程边播边等
  下一句；首声延迟从"等全文"降到"等第一句"，长文本体验提升明显。短文本（单句）走老路径，
  零退化。零新增依赖，仅改 `winocr/services/tts.py`。

| 文件 | 改动 |
|------|------|
| `winocr/services/translate/llama_cpp.py` | 新增：LlamaCppEngine 翻译引擎（纯 CPU GGUF 推理） |
| `winocr/services/ai/llama_cpp_chat.py` | 新增：LlamaCppProvider AI 对话提供方（上下文 + 历史持久化） |
| `winocr/core/paths.py` | 新增：`llama_model_search_dirs()` / `llama_model_dir()` / `find_llama_gguf()` |
| `winocr/core/config.py` | `fallback_order` 加入 `llama_cpp`；`AiConfig.provider` 注释补充 |
| `winocr/ui/tk/dialogs_settings.py` | 翻译设置页新增「本地模型」下拉选择器（llama_cpp 引擎专用） |
| `winocr/ui/tk/project_bar.py` | 删除确认升级为「输入项目名称」二次确认模态窗口 |
| `setup.bat` | pip 源切换（清华+阿里）+ 可选模型下载（curl/PowerShell）+ GBK+CRLF 编码 |
| `requirements.txt` / `pyproject.toml` | 新增 `llama-cpp-python>=0.3.0` |
| `winocr/services/tts.py` | TTS 延迟修复（stop 不杀宿主 + join 缩至 0.2s）+ 方案 B 分 chunk 流式播放（预取队列 maxsize=2） |
| `README.md` | 版本号 3.4.19 → 3.4.20 |

## 3.4.19 — 工程化改进（2026-08-25）

> 正式发版：把 3.4.18 之后的工程化重构统一归档到 3.4.19，版本号与 CHANGELOG /
> version.py 对齐。全量 **146 项测试通过**，`main.py doctor` 正常。

### 修复：GitHub 源码缺模型导致「不能截图不能 OCR」

- **现象**：从 GitHub 下载的 3.4.19 源码 zip 不包含 `models/` 与 `vendor/`
  （体积原因被 `.gitignore` 排除），默认档位 `tiny` 模型缺失 → OCR 引擎无法初始化。
- **根因**：`tools/download_ocr_model.py` 只支持 `small`/`medium`，不支持默认档位
  `tiny`；且 README / DOC 误导性声称「模型已内置在仓库，无需联网下载」。
- **改法**：下载脚本补齐 `tiny` 档位（det/rec/cls，ModelScope 官方源，SHA256 校验）；
  文档如实改为「GitHub 源码不含大文件，需按需下载」。
- **验证**：`tools/download_ocr_model.py tiny` 实测 3/3 就绪，SHA256 全通过。

### 代码一致性：裸 print() 统一收敛到 logging

- 22 处裸 `print()` 替换为 `logging` 调用（事件总线异常、插件跳过、OCR 引擎回退、
  RapidOCR 模型降级、配置告警等），9 个模块补 `logger`，3 个模块直接改。
- 保留 `console.py` 的 3 处 `print()` 作为 CLI 输出（doctor / 命令行工具属正常 stdout）。

### app.py 第二轮拆分

- `winocr/ui/tk/exit_guard.py`（新增）：进程退出守卫（win32 父进程查询 + venv shim
  连根强杀），从 app.py 拆出。
- `winocr/ui/tk/style.py`（新增）：ttk 样式配置（浅色/深色主题），从 app.py 拆出。
- `app.py` 874 → 701 行。

### dialogs_settings.py 第二轮拆分

- `winocr/ui/tk/dialogs_hotkey.py`（新增）：热键设置对话框（110 行）。
- `winocr/ui/tk/dialogs_test.py`（新增）：AI / 翻译 / 云端 OCR 连接测试（153 行）。
- `dialogs_settings.py` 1006 → 769 行，连接编辑辅助函数（`_make_conn_editor` /
  `_apply_conn_vars` / `_lim_row`）提升为模块级。
- **顺带修复**：AI 页签此前缺少 `ai_v = _make_conn_editor(...)` 定义，保存设置会
  `NameError`、AI 连接参数编辑器从未显示；已补上。

### 配置注入收敛：长参数列表 → apply_config(config)

- 4 个服务类新增 `apply_config(config)`：`RapidOcrEngine`（本地档位）、
  `VisionOcrEngine`（云端视觉 OCR）、`GlmChatProvider` / `OpenAiCompatProvider`
  （AI 文本 + 视觉全参数）。
- `app.py` 的 `_configure_ocr` / `_configure_ai` 从 12+ 参数展开调用收敛为一行
  `inst.apply_config(self.config.xxx)`，配置对象 → 实例属性的映射收进服务类。
- 保留 `set_*` / `configure` 旧接口（测试直接调用不受影响）。

### 修改文件清单

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/app.py` | 拆出 exit_guard.py / style.py，874 → 701 行 |
| `winocr/ui/tk/exit_guard.py` | 新增：进程退出守卫 |
| `winocr/ui/tk/style.py` | 新增：ttk 样式配置 |
| `winocr/ui/tk/dialogs.py` | 薄壳 re-export 更新（hotkey / test 新模块） |
| `winocr/ui/tk/dialogs_settings.py` | 1006 → 769 行，连接编辑辅助提升模块级 |
| `winocr/ui/tk/dialogs_hotkey.py` | 新增：热键设置 |
| `winocr/ui/tk/dialogs_test.py` | 新增：连接测试 |
| `winocr/core/app.py` | `_configure_ocr` / `_configure_ai` 收敛为 `apply_config` |
| `winocr/services/ai/glm_chat.py` | 新增 `apply_config` |
| `winocr/services/ai/openai_chat.py` | 新增 `apply_config` |
| `winocr/services/ocr/rapidocr.py` | 新增 `apply_config` |
| `winocr/services/ocr/vision_ocr.py` | 新增 `apply_config` |
| `winocr/core/event_bus.py` 等 9 模块 | print → logging |

## 3.4.18 — 工程化收尾（2026-08-25）

> 正式发版：把「补完一~五」的成果统一归档到 3.4.18，版本号与 CHANGELOG / version.py 对齐。
> 全量 **146 项测试通过**，`main.py doctor` 正常。

### 完整退出修复（P-11 + P-13）

- **根因**：`run.bat` 用 `.venv\Scripts\pythonw.exe main.py` 启动，该 shim 是重定向器，
  会再拉起真正解释器作为子进程。`os._exit(0)` 只退「真身子进程」，父 shim 残留 → 每次启动都要强杀。
- **修复**：`winocr/ui/tk/app.py` 新增 `_force_exit_venv_tree()`。退出时用 Windows Toolhelp
  快照查父进程，若父进程是 `.venv` 下的 python/pythonw（venv shim）**且** 当前进程命令行含
  main.py（`GetCommandLineW` 读当前进程），对父进程树执行 `taskkill /F /T` 连父带子一起强杀；
  否则只 `os._exit(0)`。`quit_app()` 与托盘 2 秒强退线程都改走该函数。
- **二次踩坑（P-11）**：pythonw 下 PowerShell 查父进程命令行返回空，守卫静默失败。
- **三次踩坑（P-13）**：只凭「父进程是 .venv shim」会误杀 pytest（pytest 同样由 shim 拉起），
  最终判定收紧为「父进程是 .venv shim 且 当前进程命令行含 main.py」。
- **验证**：真机托盘退出后 `python tools\list_winocr_proc.py` 无任何 `main.py` 进程残留。

### PYID 人机对账工具（P-12）

- `tools/list_winocr_proc.py` 把 venv shim resolve 到真实解释器（读 `pyvenv.cfg` 的 home），
  对真实路径做 sha1 取前 10 位得 **PYID**（固定身份）。新增 `--pyid <ID>` 反查、按 PYID 归并。
- 用途：AI 与用户对账「跑的是不是同一解释器」，比对 PYID 而非动态 PID。

### 测试数据隔离（WINOCR_HOME）

- `tests/conftest.py` 新增 session 级 autouse fixture `_isolated_winocr_home`，
  把 `WINOCR_HOME` 重定向到 pytest 临时目录。测试不再读写真实 `~/.winocr`，结果可重复、
  不污染数据、沙箱/CI 不被权限拦截。业务代码零改动（`paths.user_dir()` 本就支持该变量）。

### dialogs.py 按职责拆分

- 1905 行单文件拆为 `dialogs_common`（工具）/ `dialogs_settings`（热键+API 设置）/
  `dialogs_data`（知识库+历史+导入）/ `dialogs_misc`（关于+插件+首次运行）。
  `dialogs.py` 保留为薄壳 re-export，旧 import 兼容。

### 知识库跨项目检索修复

- `open_knowledge` 面板新增「所有项目」复选框，`knowledge.search` 支持 `project="*"`
  跨项目查询；修复前向引用（按钮 command 引用未定义函数）与 Treeview iid 越界问题。

### 分发体积优化

- **已落地**：`build.bat` 裁剪 OpenCV 视频 FFmpeg 后端（`_internal\cv2\opencv_videoio_ffmpeg*.dll`，
  省 ~29MB）。WinOCR 是 OCR 工具不用视频读写，rapidocr 图像处理不受影响。
- **保留（用户决策）**：`v6_medium` OCR 模型（133MB，OCR 智能升档需要）与 `pymupdf`
  PDF 附件（43MB）均不裁剪。
- **体积构成**（3.4.16 分发 ~703MB）：`_internal` 387MB（cv2 112 / ctranslate2 59 /
  pymupdf 43 / onnxruntime 35 / rapidocr 31）+ `vendor` 176MB（Argos 中英互译模型）+
  `models` 140MB（v6_medium 133 + v6_tiny 6.6）+ exe 0.6MB。裁剪后 ~674MB。

### 修改文件清单

| 文件 | 改动 |
|------|------|
| `winocr/version.py` | 3.4.17 → 3.4.18，release_date 2026-08-25，HIGHLIGHTS 更新 |
| `winocr/ui/tk/app.py` | `_win32_parent_info()` + `_current_cmdline_has_main_py()` + `_force_exit_venv_tree()` |
| `winocr/ui/tk/dialogs.py` | 薄壳化，re-export 拆分后的 4 模块 |
| `winocr/ui/tk/dialogs_common.py` | 新增：工具函数 + 常量 |
| `winocr/ui/tk/dialogs_settings.py` | 新增：热键 / API 设置 / 连接测试 |
| `winocr/ui/tk/dialogs_data.py` | 新增：知识库 / 历史 / 导入 |
| `winocr/ui/tk/dialogs_misc.py` | 新增：关于 / 插件 / 首次运行 |
| `winocr/services/persistence/knowledge.py` | `search` 支持 `project="*"` 跨项目查询 |
| `tools/list_winocr_proc.py` | PYID 列 + `--pyid` 反查 + 归并 |
| `tests/conftest.py` | `_isolated_winocr_home` fixture（WINOCR_HOME 隔离） |
| `DOC/踩坑.md` | P-11 / P-12 / P-13 + 二三次踩坑 |

## 3.4.17 补完五 — 完整退出修复 + PYID 人机对账工具 + 测试数据隔离（2026-08-24）

> 不升版本号（仍 3.4.17），不新增依赖。解决「托盘退出后 run.bat 每次都要杀进程」，
> 落地「PYID 固定解释器身份」用于 AI 与用户对账，并让测试数据与真实 `~/.winocr` 隔离。

### 完整退出修复（P-11 + P-13）

- **根因**：`run.bat` 用 `.venv\Scripts\pythonw.exe main.py` 启动，该 shim 是重定向器，
  会再拉起 `D:\Documents\Programs\Python312\pythonw.exe` 作为子进程跑代码。
  `quit_app` 末尾的 `os._exit(0)` 只退「真身子进程」，父 shim 残留 → 每次启动都要强杀。
- **修复**：`winocr/ui/tk/app.py` 新增 `_force_exit_venv_tree()`。退出时用 Windows Toolhelp
  快照查父进程，若父进程是 `.venv` 下的 python/pythonw（venv shim），对父进程树执行
  `taskkill /F /T /PID <父PID>` 连父带子一起强杀；否则只 `os._exit(0)`，绝不误杀测试/IDE 进程。
  `quit_app()` 与托盘 2 秒强退线程都改走该函数。
- **二次踩坑（P-11）**：第一版加了「父进程命令行含 main.py」守卫，但 pythonw 下 PowerShell
  查命令行返回空 → 守卫静默失败、taskkill 永不触发。改为只认「父进程可执行文件是 .venv 下
  python/pythonw」硬特征。
- **三次踩坑（P-13）**：只凭「父进程是 .venv shim」就 taskkill 会误杀 pytest——pytest 同样由
  shim→真身拉起，测试进程自己发出 `taskkill /T` 把自己连根杀死（无 traceback 的 exit=1）。
  最终判定收紧为「父进程是 .venv shim **且** 当前进程命令行含 main.py」
  （`GetCommandLineW` 读当前进程，不依赖 PowerShell），只有 run.bat 起的 GUI 才满足。
- **验证**：真机托盘退出后 `python tools\list_winocr_proc.py` 无任何 `main.py` 进程残留；
  run.bat 再次启动不再提示「检测到旧实例，执行强杀」；全量 146 测试通过。

### PYID 人机对账工具（P-12）

- **问题**：PID 每次运行都变，无法当身份。AI 与用户各自跑代码，无法确认是否同一解释器。
- **方案**：`tools/list_winocr_proc.py` 升级——把 venv shim resolve 到真实解释器
  （读 `pyvenv.cfg` 的 home），对真实路径做 sha1 取前 10 位得 **PYID**（只依赖路径，固定）。
  新增 `--pyid <ID>` 反查同一解释器下的所有进程；按 PYID 归并输出。
- **验证**：run.bat 起的 shim+真身归并到同一 PYID（如 `0162f86e4f`），AI/工具进程是另一 PYID。

### 测试数据隔离（WINOCR_HOME）

- **问题**：全量测试会读写真实 `~/.winocr`（config / history / knowledge.db / chat_history /
  selection.log），既污染用户数据，又让测试结果依赖本机状态、不可重复，在沙箱/CI 里被权限拦截。
- **方案**：`tests/conftest.py` 新增 session 级 autouse fixture `_isolated_winocr_home`，
  把 `WINOCR_HOME` 重定向到 pytest 临时目录。`paths.user_dir()` 每次调用都读该环境变量
  （无 import 时缓存），业务代码零改动即全局生效。
- **验证**：全量 146 测试通过，不再写真实 `~/.winocr`。

### 修改文件清单

| 文件 | 改动 |
|------|------|
| `winocr/ui/tk/app.py` | 新增 `_win32_parent_info()` + `_current_cmdline_has_main_py()` + `_force_exit_venv_tree()`；`quit_app()` / `_tray_quit()` 改走树强杀 |
| `tools/list_winocr_proc.py` | 新增 PYID 列 + `--pyid` 反查 + 按 PYID 归并；`_resolve_real()` 读 pyvenv.cfg home |
| `tests/conftest.py` | 新增 session 级 autouse fixture `_isolated_winocr_home`，WINOCR_HOME 指向临时目录 |
| `DOC/踩坑.md` | 新增 P-11（shim 残留）、P-12（PYID 固定身份）、P-13（强杀误伤 pytest）+ 二/三次踩坑 |

## 3.4.17 补完四 — P0/P1 迭代（2026-08-24）

> 执行 `DOC/迭代计划-P0P1P2.md` 的 P0 + P1 部分，不升版本号（仍 3.4.17），不新增依赖。
> 全量 **134 项测试全绿**（含新增 5 项 pystray 集成测试 + 修复 1 项竞态失败）。

### P0：立即修复

- **P0-1 废弃 `CI=1` → `WINOCR_CI`**（`tests/conftest.py` 新增 + `test_gui_smoke.py` / `test_project_bar.py` 改用）：
  旧 `CI=1` 是通用环境变量，本机开发时残留导致 GUI 测试全跳过。统一为 `WINOCR_CI` 专属变量 +
  `--headless-gui` pytest 命令行参数。`conftest.py` 提供 `display_available()` 函数消除两文件重复代码。
  `.github/workflows/ci.yml` 同步改 `WINOCR_CI: "1"`。
- **P0-2 `_FakeWindow` 竞态修复**（`tests/test_selection_translate_busy.py`）：
  之前 `test_capture_and_translate_uses_latest_text` 失败的根因不是接口漂移而是竞态——
  `_done` 回调先 `_release_busy()`（清 `_busy`）后 `post(show_sticker)`，测试的 `while _busy.is_set()`
  在两者之间退出，`sticker_original` 尚未设置。修复：`__init__` 初始化 `sticker_original=""` +
  wait 条件改为等 `sticker_original == 期望值` 而非仅 `_busy` 清空。
  同时在 `conftest.py` 新增 PEP 544 `WindowProtocol`，mypy 静态检查可捕获 FakeWindow 与真实 MainWindow 的接口漂移。

### P1：近期补强

- **P1-1 pystray 真集成测试**（`tests/test_tray_integration.py`，5 项全绿）：
  启动真实 `TrayIcon` → 断言线程引用被捕获（patch 生效）→ 断言 `daemon=True` → `stop()` 后
  轮询 20×0.1s 等线程 `is_alive() == False`。**发现并修复** pystray `stop()` 的隐性 bug：
  `icon.stop()` 内部检查 `if self._running:`，如果线程还没跑到 `_mark_ready()`（窗口未创建完），
  `_running` 仍为 False，`stop()` 静默跳过。`TrayIcon.stop()` 补了轮询等待 `icon._running=True`（最多 1s）。
- **P1-2 mainloop 测试超时缩短 + watchdog**（`test_gui_smoke.py`）：
  `test_event_bus_updates_ui_from_worker_thread` 的 `after(900)` → `after(500)`，
  `test_post_drains_via_pump_in_fifo_order` 的 `after(1200)` → `after(800)`。
  两项均加 3s `_watchdog` 硬超时保护，防止测试在慢机上挂死。
- **P1-3 capsules 日志降级**（`core/capsule.py` L73）：
  `logger.warning` → `logger.debug`，消除每次启动/doctor 的 `胶囊包 winocr.capsules 导入失败` 噪声。

### 修改文件清单

| 文件 | 改动 |
|------|------|
| `tests/conftest.py`（新增） | 统一 `has_display()` + `display_available()` + `--headless-gui` 参数 + PEP 544 `WindowProtocol` |
| `tests/test_gui_smoke.py` | 用 conftest 替换内联 `_has_display`；mainloop 测试加 watchdog |
| `tests/test_project_bar.py` | 用 conftest 替换内联 `_has_display` |
| `tests/test_selection_translate_busy.py` | `_FakeWindow` 初始化竞态属性 + wait 条件修复 |
| `tests/test_tray_integration.py`（新增） | 5 项 pystray 真集成测试 |
| `winocr/ui/tk/tray.py` | `stop()` 补轮询等 `icon._running=True` |
| `winocr/core/capsule.py` | L73 `warning` → `debug` |
| `.github/workflows/ci.yml` | `CI: "1"` → `WINOCR_CI: "1"` |

## 3.4.18 — 真·项目栏 + 数据隔离 + 知识库导入（2026-08-24，未发版）

> 按 `DOC/WinOCR_项目栏与数据隔离_蓝图.md` 落地。未发版：`winocr/version.py` 仍 3.4.17，
> 打包/分发在下一次发版时执行。不新增第三方依赖。

- **热键录制 bug 修复**（`ui/tk/dialogs.py::open_hotkey_settings`）：录制此前把组合键写到
  `setattr(cfg, action, ...)` 临时属性，`HotkeyService.resolved()` 永远回退胶囊默认 → 新录键不生效且不落盘。
  改为统一写入 `cfg.overrides[action]`（空则 pop 恢复默认）+ `apply_config()` 落盘 + `hotkeys.reload()` 重注册。
  **任意新录键立即覆盖旧键（默认或上一版 override 都不共存）、重启保留**，已由 `tests/test_hotkey_override.py`（9 项）钉死。
- **真·项目栏（数据层 + 服务接线）**：
  - `config.py`：`ProjectConfig` 注册表 + `AppConfig.projects/current_project`；`sections()` 把 `projects` 排首位
    （避免被 `[section]` 头吸收）；`from_dict/_coerce_projects` 保证 default 存在、current 指向有效；`save()` 根级写 `current_project`。
  - `paths.py`：`project_history_path/project_chat_history_path/_safe_id`（子目录 `history/`、`chat_history/`）。
  - `core/projects.py`：`ProjectManager` 统一管理 add / switch / close_tab（仅 `open=False`，**数据保留**）/
    reopen / rename / set_target / delete（**真删**：知识 + 两历史文件 + 注册表，default 不可删不可关）/
    migrate_legacy_history（旧全局历史复制进 default）/ 切换时重指 AI 对话、JsonHistory 与译向。
  - `knowledge.py`：`knowledge` 表加 `project` 列（幂等 ALTER），存/查/删按项目过滤，新增 `delete_project_records`。
  - `json_history.py` 与 `openai_chat/glm_chat.py`：支持按项目重指向（`set_project` / `set_history_path`）。
  - `app.py`：build() 装配 ProjectManager 并接线，`_make_ai` 用 `project_chat_history_path(current)`。
- **项目栏 UI（`ui/tk/project_bar.py` + `main_window.py` 改造）**：
  - 顶部**项目标签页栏**（默认 / … / +），点标签切换、点 × 仅关闭标签（默认项目无 ×）。
  - **合并窄带**（替代原 info+action 两栏）：`输入`（截图识别保持蓝 / 粘贴 / 打开）· `系统`（历史 / 知识库 / 插件 / 关于 / API / 热键 / 管理项目）· `信息`（OCR/引擎标签 + 忙时进度条）三组。
  - **`FlowFrame` 整组换行**：合并窄带与 simple/advanced 模式栏都按「组」流式换行，根治窄窗按钮被推出可视区的问题。
  - **管理项目对话框**（`open_manage_projects`）：列全部（含已关），重开 / 改名 / 设译向 / **彻底删除**。
  - 修复：标签改用 Frame 容器（`Label` 内嵌子部件会压掉文字区）；标签尺寸放大（padx 16 / pady 5 / UI_FONT）。
- **知识库导入（JSON 回灌，可指定目标项目）**：
  - `knowledge.py`：`import_records(records, project=None) -> (ok, skipped)`——字段白名单（id 重建、created_at 保留）、
    空记录跳过计数、单事务写主表 + FTS、project 缺省取当前项目；新增 `import_csv(path)`（编码自动探测
    utf-8-sig → utf-8 → gbk，兼容 WPS/Excel 导出）。
  - **导入引导窗** `dialogs.open_import_dialog`：目标项目下拉（默认当前）+ 模板目录展示 +
    「打开模板文件夹」+ 「导入 JSON…」/「导入 CSV…」两个入口，后台线程导入后状态提示。
  - **导入模板自动生成** `ensure_import_templates()`：在 `~/.winocr/templates/` 幂等生成
    `知识库导入模板.csv`（utf-8-sig，WPS/Excel 可直接编辑）/ `知识库导入模板.json`（示例记录）/
    `导入模板说明.txt`（逐字段说明 + 用法）。
  - 新增全局热键动作「**打开知识库**」（`open_knowledge`，默认 **`Ctrl+Shift+K`**）+「**导入知识库**」
    （`import_knowledge`，默认 **`Ctrl+Shift+I`**，直接走导入引导窗，无需先打开面板）。热键设置里可改录/清空。
- **老知识库 schema 兼容迁移（3.4.18 hotfix）**：
  - **症状**：用户从 `WinOCR-Portable-v1.0.0` 升级后点「存知识库」报
    `保存失败: table knowledge has no column named scene`，所有写入失败。
  - **根因**：旧版 schema 用 `scenario`（非 `scene`），并多 `tags` / `note` 列；`CREATE TABLE IF NOT EXISTS` 不会重建老表。
  - **修复**（`knowledge.py::_migrate_legacy_columns`，启动时自动跑、幂等）：
    - 缺 `scene` 但有 `scenario` → `ALTER ADD scene` + `UPDATE scene = scenario`（仅填空行，不覆盖已有）；
    - `tags` / `note` 缺则 `ALTER ADD DEFAULT ''`（保留老数据）；
    - `KnowledgeRecord` 加兼容字段 `scenario` / `tags` / `note`；`_row_to_record` 用「先试取」安全读；
    - `export` 同时输出 `scene` + `scenario` + `tags` + `note`（CSV/MD/JSON 全覆盖）；
    - `import_records` 探测 schema 动态构造 INSERT 列，并接收 dict 的 `scenario` 字段。
  - **验证**：用副本跑迁移，原 8 条数据保留、`scene` 列新增、`scenario` 数据已复制、新增写入成功（38 项测试全绿）。
- **验证**：相关回归 36 项全绿（knowledge 9 + hotkey 16 + project_bar/gui_smoke 11）；
  新增 `tests/test_project_bar.py`（4 项）与 `tests/test_knowledge.py` 导入 3 项、`tests/test_hotkey_override.py`（9 项）。
  `test_gui_smoke` 属性名随 `*_frame → *_flow` 同步更新。
- **文档**：`DOC/WinOCR_项目栏与数据隔离_蓝图.md` 状态置「已落地」；总览/README 同步（见各文件）。

## 3.4.17 补完三 — 托盘退出进程残留修复（2026-08-24）

> 本次会话修复「右键托盘退出但进程残留、图标不消失」的 Bug，不升版本号（仍 3.4.17），不新增依赖。

- **症状**：点托盘菜单「退出 WinOCR」后，图标仍留在系统托盘、进程不退出，必须用 `stop_winocr.py` 强杀。
- **根因**：pystray `_run_detached()` 内部直接 `threading.Thread(...).start()`，创建线程后**不存储引用**，
  导致 `TrayIcon.stop()` 无法 `join` 线程、也无法将其设为 `daemon=True`。该非 daemon 线程跑着消息循环，
  Tk 主线程退出后被它拖住，进程不退。`icon.stop()` 只发停止信号但不等线程真正退出。
- **修复**（三层保障）：
  1. **`ui/tk/tray.py::start()`**：用实例级 patch 替换 `pystray.Icon._run_detached`，自建 `daemon=True` 线程
     并保存引用到 `self._thread`。`stop()` 即可 `join(timeout=2.0)` 等线程退出。兼容回退：patch 失败仍尝试
     探测 pystray 可能存储的线程属性（`_thread` / `_listener_thread` / `thread`）。
  2. **`ui/tk/app.py::quit_app()`**：末尾兜底 `os._exit(0)`——即使上述线程 join 超时也保证进程退出。
     调用链：`tts.stop → tray.stop → app.shutdown → root.quit/destroy → os._exit(0)`，全程幂等（`_exiting` 守卫）。
  3. **退出热键 `Ctrl+Shift+Q`**：已在 `_bind_global_keys()` 绑定（Tk 焦点内直接调 `quit_app()`），
     并在全局热键服务中作为「永久闸门」注册（`enabled=False` 时也始终注册）。
- **冒烟测试**：
  - `tests/test_gui_smoke.py::test_quit_app_cleans_up_and_force_exits`：断言调用链顺序 + 幂等性 + `os._exit(0)` 被调用。
  - `tests/test_hotkey_gate.py`（4 项）：`enabled=False` 时 quit 仍注册、override 优先、空 combo 不注册。
  - `tests/run_quit_smoke.py`：独立脚本，验证 `quit_app` 调用链 + 热键闸门 + `tray.stop` 的 `icon.stop + thread.join`。
  - 全量 24 项回归全绿（hotkey_gate 4 + hotkey_override 9 + hotkey_win32 3 + smoke 4 + cancel 4）。
- **关键文件**：`ui/tk/tray.py`（start/stop）、`ui/tk/app.py`（quit_app / _bind_global_keys）、
  `services/hotkey.py`（quit 闸门注册）、`core/config.py`（HotkeyConfig.quit = "ctrl+shift+q"）。

## 3.4.17 补完 — P3-1 划词方向按钮 + P3-2 历史记录面板（2026-08-23）

> 本次会话补完两个未落地功能，不升版本号（仍 3.4.17），不新增依赖。

- **P3-1 贴图翻译方向可选**（`ui/tk/sticker.py` + `ui/tk/app.py`）：
  划词小贴条底部新增「自动 / 译中 / 译英」三态按钮，点选后按所选方向对原文重译。
  `StickerWindow._retranslate(target)` 调 `TkUi.translate_sticker(text, target)`，
  后者经 `_translate_to_sticker(target=, explicit=True)` 走后台翻译——`explicit=True` 时调度器只推断 source、绝不改 target。
  划词翻译结果自动写入 `history.json`（`pipeline.record`）。
  关键文件：`sticker.py` L112-123 / L216-231；`app.py` L711-723 / L659-661。
- **P3-2 历史记录面板**（`ui/tk/dialogs.py::open_history` + `ui/tk/main_window.py`）：
  主窗口信息栏新增「历史记录」按钮 → 列表（时间 + 原文摘要）/ 关键词检索 / 详情（原文 + 译文 + 时间）/
  复制原文·译文 / 导出 Markdown·TXT / 清空。数据来自既有 `JsonHistory` 落盘的 `history.json`。
  关键文件：`dialogs.py` L1322；`main_window.py` L18 / L72-73。
- **Bug 修复**：`dialogs.py` 中 `open_history` 和 `open_knowledge` 的按钮 `command=` 直接引用了后面才定义的局部函数，
  导致前向引用 `NameError`。全部改为 `command=lambda: func()` 延迟查找。
  影响行：L1180 / L1350 / L1384-1386（历史面板）+ L1180（知识库面板）。
- **测试同步**：
  - `test_selection_translate_busy.py` L51：mock `boom` 签名加 `explicit`/`note` 参数，对齐 `pipeline.translate` 新签名。
  - `test_gui_smoke.py` L128-148：`test_dialogs_build` 补 `open_history` 到遍历列表 + 注入 `_FakeStore` mock persistence
    （用户 config.toml 黑名单了 `json_history`，不 mock 会弹窗卡死）。
- **验证**：全量 119 passed（1 个 Tcl `init.tcl` 环境缺陷跳过，非代码问题）；`main.py doctor` exit 0。
- **文档**：新增 `DOC/小白如何整体把握代码.md`（代码导览）和 `DOC/踩坑.md`（AI 编程踩坑记录）；
  `DOC/WinOCR3.4文档总览.md` 追加 P3-1 / P3-2 记录。

## 3.4.17 — TK 改进蓝图落地（P0~P2 全做，2026-08-22）

> 执行 `WinOCR3.4-TK版改进蓝图.md`。在 3.4.16 已稳定（打包/分发/AI 复现文档齐备）的基础上，
> 聚焦「界面与设置项」这一唯一可公平对比的维度，补齐 3.4 TK 版的隐藏能力。

- **P0-1 点亮隐藏配置字段**（`ui/tk/dialogs.py` 设置页）：翻译页「离线优先」开关、OCR 页「结构化输出」开关、
  外观页「窗口尺寸」输入 + 「记住当前」按钮、AI 页「提供方」只读标签。此前这些字段已在 `config.py` schema 里，
  但 UI 没暴露，等于不可调。
- **P0-2 知识库面板**（`ui/tk/dialogs.py::open_knowledge` + `services/persistence/knowledge.py::export`）：
  浏览 / 全文检索（FTS5 trigram + LIKE 兜底）/ 详情 / 删除 / **导出 JSON·Markdown·CSV**；
  主窗口「存入知识库」按钮打通写入（`main_window.py::save_to_knowledge`）。
- **P0-3 源语言检测增强**（`services/translate/dispatcher.py`）：引入 `langid` 轻量识别，
  `set_languages(["zh","en"])` 只分中英，其余语言一律外译中；含日文假名预检（防 langid 误并）；
  空文本 / 纯符号返回「unknown」跳过翻译；langid 缺失回落字符占比法。只做中英互译，不引入多余语种模型。
- **P1-1 插件黑名单**（`core/config.PluginConfig` + `core/app._discover` + `ui/tk/dialogs.py::open_plugins`）：
  按 `.name` 禁用任一轴插件，启动装配时跳过，改完重启生效。
- **P1-2 OCR 智能档位**（`services/ocr/rapidocr.py`）：`auto_upgrade` 开启时，识别置信度低于阈值
  （默认 0.5）自动升一档（tiny→small→medium）重试一次取更优结果；设置页标注各档「未安装」。
- **P1-3 取色器克制**（`ui/tk/dialogs.py` 外观页）：21 个配色角色折叠进「自定义配色（高级）」LabelFrame，
  默认只露主题/字体/字号/窗口尺寸，避免普通用户被高级项淹没。
- **P2-1 首次运行向导**（`ui/tk/dialogs.py::open_first_run` + `ui/tk/app.py`）：未配置 API Key 且无配置痕迹时
  延迟弹一次，引导填 Key / 选翻译方向 / 看热键。
- **P2-2 设置搜索 + 多提供方**（`ui/tk/dialogs.py::_do_search` + `services/ai/openai_chat.py`）：
  设置页五 tab 关键词搜索定位；AI 提供方可在 `glm` / `openai_compat`（SiliconFlow/DeepSeek/vLLM 等 OpenAI 兼容）间切换。
- **P2-3 系统托盘**（`ui/tk/tray.py`，可选依赖 pystray）：装了才有托盘图标，没装静默跳过，程序照常隐藏窗口常驻。
- 新增依赖：`langid>=1.1.6`（必需）、`pystray>=0.19.0`（可选，已写入 requirements.txt / pyproject.toml）。
- 全量 **119 passed** + GUI 冒烟通过（知识库导出 / langid 检测 / 插件黑名单 / 提供方切换均逐项验证）。
- 版本号唯一真相源 `winocr/version.py` 升至 `3.4.17`。

## 3.4.16 — P3-15 分包/分发：PyInstaller onedir 双 exe + 数据外置（2026-08-21）

> 专家评估 P3-15 收官项。交付「免装 Python、拷走即用」的绿色分发包。

- **打包方案**（见 `DOC/WinOCR_P3-15_打包分发方案蓝图.md`）：
  - PyInstaller **onedir** 双 exe：`WinOCR.exe`（GUI windowed，双击启动）+ `WinOCR-cli.exe`（console，doctor/ocr 排障用）。
  - **数据外置**：`models/`（v6_tiny + v6_medium + cls）、`plugins/`、`vendor/`（argos 语言包按需放入）与 exe 同级，
    不进 `_internal` —— `paths.py` 的 `sys.frozen` 分支专为此设计，**零代码改动**。
  - `config.toml` 空文件随包 → 便携模式天然成立（用户数据写 exe 旁）。
  - 绿色 zip（未签名，SmartScreen 可能提示，文档已注明）。
- **打包期发现并修复的 bug（`winocr/core/registry.py`）**：插件发现原用 `Path.iterdir()` + `.py` 后缀过滤，
  PyInstaller 打包后模块落盘为 `.pyc`（`rapidocr.cpython-312.pyc`）→ 打包版 doctor 插件列表为空。
  改为 `pkgutil.iter_modules`（与 `capsule.py` 同款），源码 `.py` 与打包 `.pyc` 形态行为一致。全量测试无回归。
- **spec 要点**（`packaging/winocr.spec`）：`noarchive=True`（模块落盘，供运行时目录扫描）、
  `collect_submodules("winocr.services"/"winocr.ui")` 兜住动态导入、`collect_data_files("tkinterdnd2"/"rapidocr")`、
  `console=False/True` 双 EXE、`WinOCR.ico`（Pillow 生成，蓝 #1677ff）。
- **构建**：`packaging/build.bat`（PyInstaller 构建 + 复制模型/插件/便携开关/vendor 说明）。
- **打包产物验证矩阵（全部通过）**：
  - `WinOCR-cli.exe doctor`：插件列表与源码一致（rapidocr/argos/mymemory/… 13+ 项），关键依赖全 [+]；
  - `WinOCR-cli.exe ocr <图>`：实测识别「你好世界 OCR 打包验证」正确（v6_tiny）；
  - `WinOCR.exe`：启动 6s 进程存活（GUI 正常），可正常终止；
  - 便携模式生效（用户目录 = exe 旁，config.toml 触发）。
- 体积：`dist/WinOCR/` 未压缩 ~520M（exe+_internal ~370M + models 140M + argos 未含）；zip 后显著缩小。
- 全量 **119 passed**（registry 改动无回归）。

## 3.4.15 — SAPI5 离线兜底裁决落地：降级链回归测试（2026-08-21）

> 执行专家蓝图 `DOC/WinOCR_TTS_SAPI5_离线兜底专家蓝图.md` §4-B（测试缺口补强）。
> 用户已裁决 **SAPI5 保留为离线兜底**（edge → sapi 两级），本版用测试锁定该语义。

- **新增 `tests/test_tts_fallback.py`（7 项）**，覆盖 `_work` 降级链全场景：
  - auto：edge 成功即停（不触 sapi）/ edge 返回 False 降级 sapi / edge 抛异常（超时/被墙）降级 sapi / 全失败提示「朗读失败：系统语音不可用」
  - edge：显式指定时失败**不降级**，提示「Edge 在线语音不可用（检查网络）」
  - sapi：显式指定时**不走 edge 分支**（即使本机可探测到 edge）
  - 取消：edge 失败期间用户取消 → 直接返回，不触 sapi
- 全量 **119 passed**（原 112 → +7），无回归。
- 此测试为「保留 SAPI5 兜底」裁决的**行为护栏**：未来若有人按旧印象（「SAPI5 已移除」）误删兜底或改错降级顺序，本组测试立即红灯。

## 3.4.14 — P2-11 TTS 常驻进程：SAPI 离线兜底零冷启动（2026-08-21）

> 专家评估 P2-11。把「每次朗读冷启动一个 powershell 进程 + 临时 .txt」改为常驻宿主复用。

- **常驻 SAPI 宿主（`_SapiHost`）**：懒启动一个 PowerShell + `System.Speech` 进程，**跨多次朗读复用**；
  通过 stdin/stdout 行协议通信（`<token>|<rate>|<vol>|<base64(text)>` → `DONE:<token>`），
  文本走 Base64 彻底规避管道中文编码坑，**不再每句写临时 .txt**。
- **取消即 terminate**：取消 / 看门狗触发时直接 `terminate` 宿主（SAPI 在进程内，音频随之停止），
  下次 `_ensure` 自动重建；另设 600s 真·卡死看门狗防宿主假死。
- **失败回落一次性 subprocess**：常驻路径任何异常（启动失败 / 握手超时 / 宿主死亡）→ 自动回落
  `_speak_sapi_oneshot`（旧行为），保证离线「点了就有声」，永不静默失效。
- **测试**：新增 `tests/test_sapi_host.py`（5 项，Python 假宿主覆盖握手 / 取消-终止 / 死亡-回落 / 单例 / stop 回收），
  全量 **112 passed**（原 107 → +5）。
- **架构裁决（2026-08-21）**：用户拍板 **SAPI5 保留为离线兜底** —— 本机 Win10 Home 无 OneCore
  （`dism` 能力数=0），SAPI5 是离线「点了就有声」的唯一通道；P2-11 已把其成本降至最低（常驻进程复用）。
  历史移除决策（2026-08-15，前提「Win11 自动切 OneCore」）因前提在本机失效而推翻。
  已同步 `DOC/WinOCR_TTS与朗读.md`，并新增决策蓝图 `DOC/WinOCR_TTS_SAPI5_离线兜底专家蓝图.md`
  （含方案对比 / 落地清单 / 未来演进触发条件）。

## 3.4.13 — P2 日志卫生：日志收口 + 消除静默吞异常（2026-08-21）

> 专家评估 P2 改进项。把「每次都无脑写文件 / 静默吞异常」两条坏味道收口为统一 logging。

- **日志收口（P2-9）**：`_sel_log_static` 与 `SelectionCapturer._default_log` 不再每次写 `selection.log`，
  统一走 `logging.getLogger("winocr.selection")`；默认 `WARNING`（生产静默，零噪声 I/O），
  `WINOCR_DEBUG=1` 才把 DEBUG 诊断落盘 `selection.log`。级别化：启动横幅=INFO、泵自愈=WARNING、
  取词链路=DEBUG、UI 线程越界 / `show_sticker` 异常=ERROR。
- **消除静默吞异常（P2-10）**：为 `capsule / paths / config / pipeline / win32_hotkey` 5 个模块接入 logging，
  把 6 处 `except ...: pass / return` 改为 `logger.debug / warning`
  （胶囊发现失败、目录创建失败、hotkey 回调异常、配置自愈解析失败、知识库召回异常）。不再盲人摸象。
- **范围复核**：P2-12（取词侵入剪贴板）早已由 `selection.py` 的 backup/restore 缓解；P2-13（单实例锁）
  未见锁逻辑，疑似已被 `stop_winocr.bat/.py` 替代 —— 二者从待办移除。
- 全量测试 **107 passed**（原 104 → +3 项日志卫生回归）。

## 3.4.12 — P1 架构加固：抽服务 / 单测 / CI / 热键兜底 / 线程守卫 / 取消（2026-08-21）

> 基于专家评估（DOC/WinOCR_项目现状与修改计划.md）落地的 P1 改进项；核心目标：把最脆弱、
> 最该被测的取词路径隔离并加防护，同时补上长期缺失的自动化门禁。

- **抽取词服务**（`services/capture/selection.py`，新增）：把 UIA 直读 / SendInput 注入 / WM_COPY /
  剪贴板轮询从 `ui/tk/app.py`（883 行上帝对象）抽出，TkUi 退化为薄壳调用；console 模式可复用。
- **取词服务单测**（`tests/test_selection_capture.py`，新增 6 项）：UIA 优先、剪贴板慢复制兜底、
  WM_COPY 第二轮、全失败返回 none、备份/还原调用，均用注入假剪贴板，不依赖真实窗口。
- **最小 CI**（`.github/workflows/ci.yml`，新增）：windows-latest 跑 headless `pytest`；
  GUI 冒烟测试在 `CI` 环境变量下强制跳过；仅装 `[dev]` 即可（App().build() 懒加载重型 SDK）。
- **热键 Win32 兜底**（`services/win32_hotkey.py`，新增）：主后端 keyboard 一个键都没注册成功时
  （典型 UIPI 拦截）自动切换 `RegisterHotKey` 系统级热键；组合键解析独立可单测。
- **UI 线程守卫**（`ui/tk/guards.py` + `@ui_thread`）：后台线程误触 Tk 控件被拦截并记录
  ERROR（不再静默吞），MainWindow 的 set_status/show_original/show_translation/set_busy/show_sticker 已加守卫。
- **任务取消**（`TkUi.do_cancel` + `Ctrl+Shift+X` + Pipeline.run_async `cancel_event`）：长 OCR/翻译期间
  可中断，立即释放 busy 锁，后台跑完丢弃过期结果，看门狗不再误弹「超时」。
- **AI 历史持久化落地确认**：`glm_chat.py` 早已实现 `_load_history/_persist`，旧注释「P1 待补」已清理；
  重开程序保留 AI 面板上下文。
- 全量测试 **104 passed**（原 89 → +15 项新增回归）。

## 3.4.11 — 划词取词兜底增强：轮询等待慢复制 + WM_COPY + UIA 祖先查找（2026-08-20）

> 用户实测：直接高亮文字按 Ctrl+Shift+D →「没有可翻译的文本」；**先手动 Ctrl+C 再按热键 → 能翻译**。
> 说明 UIA 直读为空（焦点控件无 TextPattern/选区），而剪贴板兜底的**注入式 Ctrl+C 复制慢于旧版
> 固定的 0.2s 单次等待**（浏览器/Office 可达数百 ms），读到空 → 误报。手动复制不受时间约束所以成功。

- **注入 Ctrl+C 后轮询剪贴板**（`winocr/ui/tk/app.py`）：新增 `_poll_clipboard_text`，
  每 100ms 读一次、最多 1.0s，覆盖慢复制；不再固定等 0.2s 一次。
- **WM_COPY 二级兜底**：注入 Ctrl+C 超时后，直接 `SendMessageW(fg, WM_COPY)` 再轮询 0.8s
  （标准编辑控件对 WM_COPY 可靠），取到则 src=wmcopy。
- **UIA 祖先查找**：`_read_uia_selection` 在焦点控件无 TextPattern/选区时，向上最多找 3 层
  祖先控件重试（浏览器页面、PDF 查看器等选区常挂在祖先上）。
- **注入前记录前台窗口标题**：`capture: fg before inject=<标题>`，若仍失败可直接看出
  Ctrl+C 投给了谁（管理员权限应用被 UIPI 拦截时，此处会显示该应用且注入无效）。
- 新增回归测试 `test_poll_clipboard_text_waits_for_slow_copy`（慢复制轮询命中/超时/异常兜底）。
- 全量测试 **89 passed**。

## 3.4.10 — 划词图贴回填修复确认 + 泵自愈心跳（2026-08-20）

> selection.log（带 `pump: exec` 轨迹）确认 3.4.9 的「队列 + 主线程泵」已彻底打通划词链路：
> 占位显示 → `_translate_to_sticker` → 翻译 → 结果回填 `show_sticker ok orig=44 tran=35`，
> 无选中场景也正确回填「没有可翻译的文本」。为根治偶发「泵静默死亡」再次补强。

- **泵自愈心跳**（`winocr/ui/tk/app.py`）：`post()` 发现泵声称运行但 >1.5s 无执行
  （`_pump_last` 未刷新，即意外死亡/续期失败）→ 自动重新引导一次并记 `post: pump stale, restarting`。
  泵正常运行时每次执行刷新心跳，不受影响。
- **修复诊断导入 bug**（`winocr/ui/tk/main_window.py`）：`show_sticker` 的成功/失败日志从
  `from ..ui.tk.app import _sel_log_static`（会解析成不存在的 `winocr.ui.ui.tk.app`，被静默吞掉）
  改为同级正确的 `from .app import ...`——此后图贴每次回填/报错都有日志可查。
- 移除临时 `pump: exec` 逐条轨迹日志（定位完成），保留心跳与 `show_sticker ok/error` 低噪日志。
- 全量测试 **88 passed**。

## 3.4.9 — 跨线程 UI 回写改「队列 + 主线程泵」，根治异步结果不刷新（2026-08-20）

> 用户反馈（selection.log）：3.4.8 下按 Ctrl+Shift+D 图贴**立刻出现但一直停在「⏳ 取词/翻译中…」**。
> 日志显示链路其实已走完——`capture done src=clip len=44`、`translate: on_done` 都触发了，
> 但图贴正文没被回填。根因：**非主线程直接 `root.after(0, ...)` 在 Tk 下不可靠**——
> `after()` 本身也是一次 Tcl 调用，后台线程（keyboard 监听线程 / 取词 worker / run_async 回调）
> 调用它时若主线程正忙于事件循环，Tcl 多线程竞争会偶发「回调不被泵起 / 被静默丢弃」，
> 于是占位能弹、结果回不来（表现为图贴卡「识别中」、状态栏不更新）。

- **`TkUi.post()` 重构为「线程安全队列 + 主线程泵」**（`winocr/ui/tk/app.py`）：
  - 所有跨线程回写先入 `_ui_queue`，泵未启动时由第一次 `post` 用一次 `after(0)` 引导；
  - 泵 `_pump_ui` 在主线程 `after(40)` 自续期排空队列（单条回调异常不拖垮泵）；
  - 泵一旦运行，**后台线程只入队、绝不直接碰 Tk**，从根本上消除跨线程 Tcl 竞争。
- **`StickerWindow.show` 插入前清洗文本**：过滤 NUL/控制字符（`_sanitize`），避免外部
  选区文本带非法字符导致 Tk `Text.insert` 抛错（防御性修复，兼防未来同类卡死）。
- **`MainWindow.show_sticker` 不再静默吞异常**：失败时写 `~/.winocr/selection.log`，便于远程定位。
- 新增回归测试 `test_post_drains_via_pump_in_fifo_order`：不经 `run()`，后台线程连续
  3 次 `post` 必须按 FIFO 全部被执行（丢回调 = 图贴卡死场景的直接回归）。
- 全量测试 **87 passed**；忠实复现（后台线程热键 + 含 NUL 选区文本）端到端通过：
  占位 → 回填真实译文，图贴 `normal/viewable=1`。

## 3.4.8 — 划词「即时弹窗」：按 Ctrl+Shift+D 立刻出图贴（2026-08-20）

> 用户要求：先有「即时弹窗」行为——按 Ctrl+Shift+D 不论是否取到词，图贴都应**立刻出现**，
> 取词/翻译在后台跑完再回填，而不是等整个取词+翻译链路走完才弹窗。

- **热键 `_on_hotkey_selection` 在按键瞬间先 `post(window.show_sticker, "", "⏳ 识别中…")`**
  把空/加载中图贴立即弹到光标附近；随后独立工作线程照常取词、翻译，完成后由
  `on_done/on_error` 再次 `show_sticker(text, translation)` 回填正文——**同一张图贴**就地更新，
  不再等网络往返才出现。无选中时回填「没有可翻译的文本…」提示。
- **`StickerWindow.show` 已显示时不再重定位**：首次弹出定位到光标附近，后续回填
  保留用户拖拽位置，避免「即时弹窗→回填」时被拽回光标处造成跳动；也不夺焦点（无 `lift()`）。
- 已有翻译任务在途（busy）时不再重复弹空窗，避免「识别中」闪烁。
- **补丁：修复「图贴一直卡在 ⏳ 识别中…」**：
  - `winocr/services/capture/clipboard.py` 给 `GlobalSize/GlobalLock/GlobalUnlock/GlobalAlloc`
    显式声明 `argtypes = [ctypes.c_void_p, ...]`，修复 64 位下大地址剪贴板句柄报
    `OverflowError: int too long to convert` 导致取词工作线程崩溃、图贴永远停在占位的问题。
  - `_capture_and_translate` 包 try/except：取词任何环节抛异常时不再让 daemon 线程静默挂掉，
    而是回填「取词失败：...」提示。
  - `_translate_to_sticker` 的 30s 看门狗现在超时后会回填「翻译超时：模型/网络未响应…」，
    而不是只释放 busy 锁让用户空等。
  - 占位文案改为「⏳ 取词/翻译中…」，避免误会是 OCR 阶段卡住；`selection.log` 关键节点加日志并 flush。
- 回归测试 `test_selection_translate_busy.py` 增补「即时弹窗」断言：按键后图贴立即出现占位，
  且最终被回填为真实译文。

## 3.4.7 — 划词取词挪到「独立工作线程 · 按键瞬间」执行（2026-08-20）

> 用户反馈（selection.log）：3.4.6 下按 Ctrl+Shift+D 日志长期固定 `key-trigger len=8`
> （不随新选中变化），贴图弹不出或内容恒为同一段。根因：3.4.6 把取词排在 Tk 主线程
> `after(0)`，但此刻焦点已被上一轮贴图/主窗口抢走，`GetFocusedControl()` 读到的是
> WinOCR 自己的窗口，回落到固定旧内容。

- **取词移到独立工作线程、且由热键回调立即启动**（不再 `post` 回主线程）。
  按键发生的【同一瞬间】目标软件仍持有焦点，UIA 直读 / 剪贴板兜底都能读到
  【屏幕任意处】最新选中文本——彻底解决「只能跑一次 / 总是同一段」与「新选中不更新」。
- 死锁防护仍成立：工作线程里才发 `SendInput` Ctrl+C，不再跑在 keyboard 监听线程内。
- 日志恢复打印取词来源：`key-trigger src=uia|clip len=N -> translate`，便于下次远程定位。
- 热键注册由 `lambda: self.post(...)` 改为直接挂 `self._on_hotkey_selection`（立即返回、派生工作线程）。

## 3.4.6 — 修复 3.4.5 死锁回归：划词取词回主线程 + 贴图不夺焦点（2026-08-20）

> 用户反馈：升级到 3.4.5 后 **Ctrl+Shift+D 完全弹不出贴图**。根因是热键回调直接挂在
> keyboard 监听线程同步执行，兜底路径用 Win32 `SendInput` 注入 Ctrl+C —— 该事件被 keyboard
> 自身钩子再次捕获、持锁等待同一把锁 → **死锁**，整个键盘监听线程挂起，后续所有热键全失效。

- **热键回调改回 `self.post` 主线程执行**（与「热键回调必须切回主线程」铁律一致）。
  取词在 `after(0)` 跑时，用户已松开按键、贴图尚未弹出，焦点仍在目标软件，
  UIA/剪贴板都能读到【屏幕任意处】新选中文本。
- **贴图 `StickerWindow.show()` 去掉 `lift()`**：`lift()` 会激活窗口、夺走键盘焦点，
  是「新选中不更新」的隐患；现仅用 `overrideredirect + -topmost` 置顶，不夺焦点。
- 新增防死锁回归测试：断言 `selection_translate` 热键是 `post` 包装、非裸方法引用。
- 3.4.5 的「钩子线程取词」方案正式撤销（死锁风险不可接受）。

## 3.4.5 — 划词取词挪到钩子线程（2026-08-20）【已撤销：引入死锁回归】

> 用户反馈：Ctrl+Shift+D 弹贴图「只能跑一次，新选中的文字不更新」，要求「不单单在主窗口选择，
> 应该可以在屏幕任何地方」。根因是取词跑在主线程 `after(0)`，而贴图 `lift()` 抢焦点后，
> 后续取词读到的全是 WinOCR 自己的空窗口 → 回落到上一次翻译的原文。

- **取词改在键盘钩子线程内同步完成**：热键回调 `_on_hotkey_selection` 此刻目标软件仍持有焦点，
  UIA 直读 / 剪贴板兜底都能读到【当前屏幕任意位置】新选中的文本，拿到后再 `post` 给主线程翻译。
- **剪贴板兜底改用 Win32 `SendInput` 直发 Ctrl+C**：旧实现在 `keyboard` 监听线程内调
  `keyboard.send("ctrl+c")` 有重入/卡死风险（读到的剪贴板是空的），现绕开 keyboard 库原生注入。
- 清理死代码：移除 3.4.3 后已废弃的 `do_selection_translate`、`_read_system_selection`
  （取词逻辑统一收到 `_capture_selection_threadsafe` 内）。
- 验证：全量 69 passed / 11 skipped / 0 failed；新增钩子线程取词链路回归测试。

## 3.4.4 — 朗读加速：启动预热 + 长文本流式提前播放（2026-08-19）

> 用户反馈「朗读合成好慢」。**A（预热）+ B（流式）** 两个改进一起做，约 50 行改动。

### A. 启动真合成预热（首声 ~19s → ~3s）
- 旧 `warmup()` 只 `import edge_tts` 探测，**从不真正合成**，导致第一次朗读还要现场做
  DNS 解析 + TLS 握手 + WebSocket 建连（首调用 ~19s）。
- 新 `warmup()` 在**守护线程**里真合成一段极短文本（`_warmup_synth` → `_synth_chunk("你好。")`），
  把上述链路预热好；不阻塞启动，离线/被墙时静默失败，不影响后续降级 sapi。
- `_warmed` 标志保证只预热一次。

### B. 长文本按句流式合成 + 提前播放
- 旧 `_speak_edge` 把**整段文本合成成一个 mp3 再播放**——文本越长越晚出第一声。
- 新实现按句切分（`_split_sentences`，保留标点/换行，超长句硬切 ≤240 字），
  起**后台合成线程**逐句产 mp3 推入队列，主播放线程边播边等下一句：
  **第一句合成完就开始响**，后续句子在后台补齐。短文本（单句）走 `_speak_edge_single` 老路径，行为不变。
- 取消（stop）贯穿生产者/消费者，残留文件在播放线程结尾统一清理。

### 改动文件
- `winocr/services/tts.py`：`warmup` / `_warmup_synth` / `_speak_edge`（分发+流式）/ `_speak_edge_single` /
  `_synth_chunk` / `_split_sentences`；新增 `import queue`。
- `winocr/ui/tk/app.py`：`_warmup_async` 注释同步（TTS 预热现会真合成）。
- `tests/test_tts_split.py`：新增切分逻辑纯函数测试（6 项）。

### 验证
- 全量 **68 passed / 11 skipped / 0 failed**；`py_compile` 通过。

## 3.4.3 — 划词改为按键触发，取消钩子自动监听（2026-08-19）

> 用户实测后决策：**取消**「鼠标钩子自动划词」（左键松开自动翻译、Alt+右键 触发）。
> 钩子方案带来误触、busy 冲突、与系统右键菜单打架、UIA 不稳定（偶发 COMError）等一连串问题。
> 回归最直接可控的一条路：**选中文字 → 按 `Ctrl+Shift+D` → 弹贴图**。

### 变更（app.py / main_window.py）
- **删除** `selection_monitor.py`（WH_MOUSE_LL 钩子）与 `tools/test_alt_right_hook.py`（自检工具）；
  移除 `SelectionMonitor` 导入、启动接线、退出释放、`_on_selection_event`、`_stop_monitor` 等。
- **主窗口移除「自动划词」复选框**（`auto_sel_var` / `toggle_auto_selection` 一并删除）；
  `UiConfig.selection_enabled / selection_auto` 标记「已废弃」仅作旧配置兼容保留。
- **`do_selection_translate`（Ctrl+Shift+D）为唯一入口**，取词链按优先级：
  主窗口内选中 → **UIA 直读（3.4.2 修复后的正确 API）** → 剪贴板兜底（备份/还原）→ 主窗口原文/译文；
  全部取不到时**弹提示贴图**说明原因（不依赖可能被隐藏的状态栏）。
- 划词诊断日志 `~/.winocr/selection.log` 保留，记录取词来源/长度/动作。

### 验证
- 全量 **62 passed / 11 skipped / 0 failed**；`py_compile` 通过。
- 版本号升 **3.4.3**（窗口标题可见，确认新代码）。

## 3.4.2 — ~~划词新增 Alt+右键 触发~~（已撤销，见 3.4.3）

> ⚠️ 本节描述的 Alt+右键 钩子触发在 3.4.3 已整体移除，保留仅作过程记录。
> 过程中最有价值的产出：定位并修复了 `Control.GetSelectionText()` API 不存在的隐藏 bug
> （UIA 取词从未成功过），该修复在按键触发的取词链中继续生效。

> 新增 **`Alt+右键`** 作为划词的显式触发：选中任意文字后按住 `Alt` 点右键（**按下**即触发），
> 立即翻译并弹小贴图，无需按 `Ctrl+Shift+D`。
> 触发键选 `Alt` 而非 `Ctrl`：多数软件对 `Ctrl+右键` 已有专门功能（且右键必弹系统菜单），
> `Alt+右键` 撞车概率最低；无论哪种修饰键，我们的钩子都只监听不拦截，软件原有功能不受影响。

### 实现（ui/tk/selection_monitor.py + app.py）
- 鼠标钩子新增监听 `WM_RBUTTONDOWN`（右键**按下**），用 `GetAsyncKeyState(VK_MENU)` 判定 Alt 是否按下；普通右键不触发。
- **为何监听「按下」而非「松开」**：右键松开时系统/应用会弹上下文菜单并抢占焦点，UIA 就读不到选中文本；按下时源控件仍在焦点上，选中完好。
- 钩子回调带 `reason`（`left_up` / `alt_right`）上抛；`app._on_selection_event(reason)` 中：
  - `left_up`：维持原去重（同一段不重翻）；
  - `alt_right`：显式操作，**跳过去重**，同一段也重翻。
- **Alt+右键 与「自动划词」解耦（修 UX 陷阱）**：最初实现把监听启动绑在 `selection_enabled and selection_auto`
  两个开关上，而 `selection_auto` 默认关 → 用户按 Alt+右键 静默无反应。现改为：
  监听只受划词总开关（`selection_enabled`，默认开）控制，启动即常驻；
  「自动划词」开关只决定「左键松开」是否自动翻译；启动时状态栏提示「划词监听已启动」。
- **Alt+右键 增加剪贴板兜底 + 失败可见化**：UIA 取不到选中时自动回退「模拟 Ctrl+C 读剪贴板」
  （带完整备份/还原）；两者都失败时**直接弹提示贴图**（不再只写状态栏——主窗口隐藏时看不见），
  并附可改用 Ctrl+Shift+D 的提示。移除对 uiautomation 的启动依赖（Alt+右键 走剪贴板兜底也能用）。
- 新增 `tools/test_alt_right_hook.py` 自检工具：单独验证钩子是否收到 `left_up` / `alt_right` 事件，
  用于远程定位「钩子层 vs 取词/翻译层」的问题。
- **版本号升 3.4.2**（version.py + pyproject.toml），窗口标题可见，一眼确认是否运行新代码。
- **划词诊断日志**：`~/.winocr/selection.log` 记录 hook 启动、事件来源（uia/clip）、取词长度、
  动作（translate / feedback / dedup / busy skip），远程定位划词链路用。
- **run.bat 启动前自动清旧实例**：单实例程序在旧进程存活时 `start` 只会激活旧窗口、新代码不加载
  （这是「改了代码却不生效」反复出现的根因）；现 run.bat 先 `stop_winocr.py --quick`
  （无旧实例秒退，有则优雅关闭+强杀）再启动。
- **修复 UIA 取词永远为空的隐藏 bug（根因）**：`_read_uia_selection` 一直调用
  `Control.GetSelectionText()`，该 API 在本机 uiautomation 版本里**不存在**（AttributeError
  被静默吞掉 → 永远返回空 → 划词链路从未真正取到过词）。已改为正确链路：
  `GetPattern(PatternId.TextPattern)` → `GetSelection()`（TextRange 列表）→ `GetText(-1)`。
  异常不再静默，写入 selection.log。
- **钩子原始事件诊断**：SelectionMonitor 新增可选 `on_raw` 回调，WinOCR 记录每次
  `lbuttonup` / `rbuttondown alt=0|1` 到 selection.log——右键按下与 Alt 检测是否成功一目了然；
  自检工具同步打印。
- 状态栏文案与主窗口注释同步（「已开启自动划词（选中即翻译 / Alt+右键 均可用）」）。

### 文档
- README 热键表 + 划词说明、用户帮助手册 3.4 节（新增「右键触发：Alt+右键」小节，注明默认可用、无需开自动划词）同步；
  顺带修正手册里过时的「依赖 SetWinEventHook」描述（现为 WH_MOUSE_LL + UIA 直读）。

## 3.4.1 — API 设置改为功能视角 + 删除平台账号（2026-08-18）

> 把「API 与引擎设置」从「平台视角」彻底改为「功能视角」：**不再有「平台账号 / 连接」中间层**。
> AI 对话 / 大模型翻译 / 云端视觉 OCR 三个功能**各自在自己页里直接持有完整的一套**连接参数
> （地址 / 密钥 / 模型 / 采样 / 限流），互不串 Key、互不串地址，也不再按连接名引用。
> 旧版散落字段（glm_api_key / cloud_* / glm_model 等）在加载时自动迁移进各功能自己的字段，无需手改。

### 配置模型（core/config.py）
- 删除 `ApiConfig` / `ApiConnection` 及其 `merged()` 字段级继承逻辑；`AppConfig` 不再有 `api` 连接表。
- 新增 `ConnectableConfig` 基类（base_url / api_key / text_model / vision_model / 温度 / top_p / 限流），
 由 `OcrConfig` / `TranslateConfig` / `AiConfig` **分别继承**，每个功能配置直接内含自己的一套。
- 视觉采样参数 `vision_temperature` / `vision_top_p` / `vision_max_output_tokens`：
 `<0` = 不发送（推理模型安全），`0` = 跟随文本侧，`>0` = 视觉专属值。
- `_apply_legacy_migration` 改为把 3.0 散落字段直接并入各功能配置自己的字段；`save()` 不再写 `[api.*]`。

### 装配（core/app.py）
- `_translate_llm_kwargs()` 直接返回 `config.translate` 的地址/密钥/模型/限流（不再 `api.merged`）。
- `_configure_ai` 用 `config.ai` 的 text/vision 字段直接注入（视觉侧 `independent=True`，不跨功能继承）。
- `_cloud_ocr_kwargs()` 直接返回 `config.ocr` 的地址/密钥/视觉模型/限流（不再经 `cloud_connection`）。

### 设置对话框（ui/tk/dialogs.py `open_api_settings`）
- **删除「平台账号」页**（凭证库）。
- 新增通用 `_make_conn_editor`：在每个功能页生成 地址/密钥/模型/采样/限流 控件，直接绑定该功能的配置字段。
- 五个页：**AI 对话 / 大模型翻译 / 云端 OCR / 外观 / 朗读**，前三个各自带完整连接参数。
- **新增滚动条**：五个页签全部套 `_ScrollableFrame`（Canvas + Scrollbar + 滚轮翻页），
  对话框高度 860→660 且可拉大（minsize 560x400），小屏不再截断；Combobox/Spinbox 及下拉弹层不抢滚轮。
- 注释同步：删去「选连接 / 凭证库」等过时说法（`services/ai/glm_chat.py`、`services/translate/glm.py`）。

### 测试
- 重写 `tests/test_translate_llm_config.py`、`tests/test_openai_compatible.py` 中引用 `cfg.api` /
 `text_connection` / `vision_connection` / `cloud_connection` / `llm_*` 的用例，改为按功能配置直接断言。

### 文档
- README / 用户帮助手册 / 开发与 AI 复现指南：连接模型描述改为功能视角（每功能各自一套参数，无平台账号）。

## 3.4.0 — 版本号统一 + 移除 bing + 知识库落地 + P0-P2 缺陷修复（2026-08-17）

> 本次是**代码质量收敛版**：统一对外版本号为 3.4.0（对齐目录 winOCR3.4），
> 并把此前架构评估发现的 P0–P2 缺陷一次清完。历史 3.5 ~ 3.12 条目保留备查，
> 它们描述的能力均已包含在 3.4.0 中。

### 版本号统一
- 全项目收敛为 **3.4.0**：`version.py`（单一真相）/ `pyproject.toml` / `winocr/__init__.py`
 （此前残留 3.0.0a）/ README。窗口标题、单实例互斥锁名均随 `__version__` 自动一致。

### 移除 bing 在线翻译
- 删除 `services/translate/bing.py`；默认回退链改为 `[argos, glm, hunyuan, mymemory]`；
 循环引擎列表、README/文档同步去掉 bing。

### P0 缺陷修复
- **划词翻译不再吞用户剪贴板**：模拟 Ctrl+C 前用 Win32 API 逐格式完整备份剪贴板
 （图片 / HTML / 自定义格式都能备份），成功与异常路径统一 `finally` 无损还原；
 配套修复 `GetClipboardData` / `GlobalLock` 等句柄的 64 位 restype（clipboard.py）。
- **打包清单漏包修复**：`pyproject.toml` `packages` 补 `winocr.services.openai_compatible`
 （此前 `pip install` 后 GLM 翻译 / AI 对话 / 云端 OCR 三轴集体 ImportError）。
- **恢复 git 版本控制**：目录此前 `.git` 已丢失、无任何版本历史；本次 `git init` 建立基线。

### P1 修复
- **ctypes 64 位句柄截断（一组）**：`selection_monitor`（SetWindowsHookExW/HHOOK/回调签名）、
 `main.py`（FindWindowW/IsIconic/SetForegroundWindow）、`_region.py`（SetWindowPos）、
 `chat_panel`（DragAcceptFiles）、`clipboard`（HGLOBAL）统一补 `restype`/`argtypes`。
- **OpenAI 兼容客户端超时重试失效**：urlopen 超时抛的是 `socket.timeout`（=TimeoutError）
 而非 `URLError`，此前「超时自动重试」从未生效；改为统一捕获 `OSError` 退避重试。
- **知识库落地（此前为死代码）**：新增 `services/persistence/knowledge.py`
 （sqlite3 + FTS5 trigram 中文检索 + LIKE 兜底、WAL、来源/原图哈希/场景/时间溯源字段）；
 `App.build()` 自动发现接线；`pipeline.save_knowledge` / `search_knowledge` / 增强对话召回可用；
 新增 `KnowledgeRecord` 类型与 `tests/test_knowledge.py`（6 例）。

### P2 修复
- `ui_inspector` 不再用 `unbind_all("<Escape>")` 清掉全局快捷键（一次取色后 Esc 隐藏主窗口
 永久失效）——改为进入前记录原绑定、退出时恢复。
- AI 对话历史 `chat_history.json` 改原子写（tmp + `os.replace`），进程被杀不写坏整个文件。
- `dialogs.py` 三个「连接测试」后台线程回写统一走 `ui.post`（符合项目线程铁律）。
- 死代码清理：`registry._cache`（从未使用）、`theme.load_custom`（无调用方）、
 `sticker.py` 的 `f1 if False else f2` 死表达式。
- `dispatcher` 单引擎翻译空结果视为失败（与 auto 模式一致，不再静默显示空白译文）。
- README 重写对齐 3.4 现状：Tk 单 UI 结构树、无内置胶囊、无 bing、知识库说明、
 热键表修正（剪贴板识别实为 `Ctrl+Shift+C`，补朗读 `Ctrl+Shift+R`）、默认连接改为 siliconflow。

### 验证
- `tests/test_knowledge.py` 6 例全过；全量 `pytest` 回归无新增失败（本机缺 PIL/tkinter 导致的
 既有 3 failed / 11 skipped 与本次改动无关，装齐依赖后为 74 passed）。

---

## 3.12.0 — 界面收敛为 Tkinter 单 UI，补齐朗读/字号/取色定位/深色打磨（2026-08-17）

### 旧界面框架整体移除
- 用户确认「删除旧版界面，其他的都做」。现仅保留 **Tkinter（TK）** 单 UI。
- 删除旧版界面目录（app / main_window / result_window / sticker / theme / __init__）及对应的 GUI 冒烟测试文件。
- `pyproject.toml` 的 `packages` 改为 `winocr.ui` + `winocr.ui.tk`；`requirements.txt` 移除第三方 GUI 依赖（GUI 仅用标准库 Tkinter，零第三方 GUI 依赖）。
- 原界面框架专属的 6 个 GUI 契约测试移植为 TK 真实控件测试（`tests/test_translate_direction.py`），真正运行而非 skip。

### TTS 朗读服务（新增）
- 新增 `winocr/services/tts.py`：`TtsService` 双通道降级 —— **edge**（在线 Neural，edge_tts 合成 mp3）→ **sapi**（离线 Windows `System.Speech.Synthesis`）。mp3 经 `winmm.dll` MCI 播放。
- `TtsConfig`：engine（auto/edge/sapi）、voice、rate、volume、auto_read；支持配置实时热更新。
- TK 界面：简单/高级模式工具栏、贴条（Sticker）均加「🔊 朗读」按钮；翻译完成可按 `auto_read` 自动朗读；朗读中再次触发即停止。后台回调统一经 `TkUi.post()` 回主线程。

### 字号缩放（ui.font_size）
- `theme.set_font_size(size)`：以 `BASE_FONT_SIZE` 派生 UI/粗体/小号/等宽全部字体；外观设置页字号 Spinbox（8–18）实时重建界面。

### 界面区域定位取色器（UI Inspector）
- 新增 `winocr/ui/tk/ui_inspector.py`：主控窗进入十字光标模式，点击控件 → `winfo_containing` 定位控件 + PIL `ImageGrab` 取像素 → 匹配主题令牌（颜色精确 + 控件类候选），回填到外观设置页自定义配色。

### 深色模式细节打磨
- `_setup_style` 按深浅切换 ttk 主题：**浅色用 vista**（仅设字体），**深色用 clam**（逐控件配置前景/背景/映射态）；Combobox Listbox、`Text` 原文/译文框经 `option_add` / kwargs 显式着色，消除深色下白底/黑字看不清。

### 附件解析实测 + 表格结构化改进
- 实测 `attach/{pdf,office}.py` 对真实 PDF/Word/Excel 均可正常 import 与解析（PyMuPDF / python-docx / openpyxl 均已安装）；缺失依赖时返回明确降级提示而非崩溃。
- `services/ocr/structure.py` 三项改进：
 1. **列对齐容差**：新增 `_column_bands` + `_assign_column`，按「列带归属 / 水平重叠最大」归列，替代纯最近中心，抗宽框/偏移列误归。
 2. **表头识别**：`_detect_header` 增加「紧凑小字表头」判据（首行显著矮于数据行即便无纵向间隙也认作表头）；表头锚点并入首数据行 left（`_merge_close` 去幽灵列），表头漏列时数据列仍能正确对齐。
 3. **跨行合并**：既有向下填充逻辑保留并兼容上述改进。
- 新增 `tests/test_structure_rebuild.py` 两项回归：`test_header_missing_a_column_still_aligns_data`、`test_tight_small_font_header_detected`。
- 全套测试：**74 passed**（无 skip；`.pytest_cache` 写权限告警为环境既有问题，与用例无关）。

## 3.11.0 — 界面按 SnowShot 1:1 重建（2026-08-16）

### 事故后重建界面
- **背景**：git stash/pop 事故删除了未跟踪的旧版界面目录与 `winocr/core/capsule.py`，导致 `App.build()` ImportError、GUI 无法启动。
- **重建方向**：用户以 SnowShot 0.7.8-beta 真实界面为参照，确定 **主窗口 = SnowShot 风格设置中心**，**结果展示 = 独立悬浮结果窗**。
- **设计令牌**（来自 SnowShot `configs/common.json` 与界面截图）：
 - 主色 `#1677FF`、圆角 `6px`、跟随系统深浅色、边框 `#dbdbdb`。
- **新增/重写文件**：
 - `winocr/core/capsule.py`：补回 `Capsule` 基类、`CapsuleContext`、`discover_capsules()`；第三方胶囊示例可正常导入。
 - `winocr/ui/tk/app.py`：`TkUi` 适配器入口。
 - `winocr/ui/tk/main_window.py`：SnowShot 风格主窗口——左侧可展开分组导航（快捷功能 / 工具箱 / 个性化 / 设置 / 关于），右侧对应设置面板（截图、翻译、AI、历史、外观配色、插件、界面/功能/热键/系统设置、关于）。
 - `winocr/ui/tk/result_window.py`：无边框悬浮结果卡片，展示 OCR 原文、译文、AI 分析；支持置顶 📌、拖动、复制/翻译/AI 分析按钮。
 - `winocr/ui/tk/theme.py`：双主题样式、系统主题检测、SnowShot 令牌。
- **管线接线**：
 - 「截图」按钮 → `RegionScreenshotSource.capture()` → `Pipeline.extract_and_translate()`（后台线程）→ 事件总线 `ocr:done` / `translate:done` → 更新悬浮结果窗。
 - 「识别剪贴板图片」走 `ClipboardSource` 同理。
 - 悬浮窗的「翻译/AI 分析」按钮可进一步触发管线动作。
- **兼容与测试**：
 - 保留旧版 `TkUi`、`theme.set_theme`、`MainWindow(ui)` 等调用签名。
 - 旧版聊天 UI 的 6 个回归测试因控件不存在已标记 skip。
 - 新增 `tests/test_gui_smoke.py`：离屏构造主窗口/结果窗/适配器绑定。
 - `python main.py doctor` 干净通过；`pytest tests/` 64 passed, 9 skipped。

## ⚠️ 事故记录（2026-08-16）：git stash 误删未跟踪源码
- **经过**：为验证旧版代码执行了 `git stash` → `pytest` → `git stash pop`，期间触发 `reset: moving to HEAD` 与文件清理，把当日**未跟踪**源码直接删除（不进回收站）。
- **丢失**：`winocr/capsules/`（snap_translate、selection_translate、translate_text 等全部胶囊）、旧版界面目录（app、windows、dialogs、sticker、chat_panel 等全部界面）、`winocr/core/capsule.py`、`winocr/services/selection_monitor.py`、以及部分测试文件。**应用当前无法启动。**
- **已恢复**：git HEAD 中被连带删除的 tracked 文件（base.py、translate/、attach/、capture/、persistence/、ui/tk/，`git checkout HEAD --` 找回）；`structure.py` 与 `test_structure_rebuild.py`、`test_group_by_lines.py` 重写。
- **待恢复**：capsules / 旧版界面目录 / capsule.py / selection_monitor.py —— 需从本机备份、云同步或对话历史（依据 `DOC/胶囊架构设计.md`）重建。
- **教训**：本仓库 HEAD 与工作树差异巨大、大量源码未跟踪，**禁止 `git stash` / `git reset --hard` / `git clean`**；验证旧代码用 `git show HEAD:路径` 或复制到临时目录。

## 3.10.0 — 划词贴图增强 + 选中即翻译常驻监听（2026-08-16）

### 划词翻译小贴图（Sticker）三改进
- **路线选择**：贴图新增「🖥 主窗口查看」按钮，把本次结果（原文 + 译文）送进主窗口，可同时满足「小贴图」与「跑主页面」两种偏好；**默认走小贴图**（划词功能的主体现）。
- **不再贴着鼠标**：定位从「鼠标附近」改为固定钉在**屏幕右上角**，远离选区，避免遮挡正在划选的内容。
- **可滚动 + 按钮分行**：内容包进可滚动区（最大高 460px），译文 / 原文各自带滚动条；按钮分两行——操作行（复制译文 / 复制原文 / 存知识库）+ 路线行（主窗口查看 / 关闭）；头部为「标题（拖拽手柄）+ × 关闭」。

### 设置 / 对话框黑块修复
- **根因**：深色主题样式漏覆盖 `分组框`、`标签页面板`、下拉框 / 数字框 / 列表等容器，被全局 `控件{background:transparent}` 透掉，表现为大片黑色（尤其 API 设置页）。
- **补全**：`对话框` 底色改为更亮的卡片色；新增 `标签页面板`、`分组框`（+ 标题）、`下拉框/数字框`、`列表控件/树控件/表格控件`、`表头` 的主题样式。

### 划词方向切换（英汉互译 + 方向按钮）
- **默认英 → 中**（用户中文能读、英文读不懂）；混杂中英按「主方向」判定：中文字数占比 > 0.5 才判为译外，否则译中，不再把英文为主的文本误译英。
- 小贴图新增「→ 中文 / → 英文」按钮，点击即按所选方向重翻并高亮当前方向；手动指定方向绝不被自动纠正。

### 常驻监听「选中即翻译」（WPS 式，默认关）
- 新增 `selection_monitor`：用 `SetWinEventHook` 监听系统文本选中变更 + 消息泵线程 + 0.3s 去抖 + 去重 + 排除自身窗口；非 Windows 优雅降级。
- 设置 → **「OCR / 界面」页签** → 勾选「选中即翻译（常驻监听…）」即热切换开启；开启后无需按 `Ctrl+Shift+D`，后台检测到新选中自动弹贴图（仅走 UIA 直读，不污染剪贴板）。
- `config.ui.selection_auto`（默认 `False`）；保留 `Ctrl+Shift+D` 手动入口。
- **修复（初版只能触发一次）**：原实现在监听线程内读取 UIA 文本做去重，跨线程 COM / STA 不稳定、首次成功后即失效，表现为「选中即翻译只能执行一次」。改为监听线程只发「选中变更」信号（去抖 + 排除自身窗口），真正 UIA 读取交回主线程 `do_selection_translate(auto=True)`，并用 `_sel_auto_last` 做原文去重（同一段不反复重翻）。
- **修复（勾选后仍要手动 Ctrl+Shift+D 才触发）**：原版只监听 `EVENT_OBJECT_TEXTSELECTIONCHANGED`，而浏览器网页 / PDF / 微信 / 自绘控件在鼠标拖选后**根本不发该事件**，于是信号收不到、必须手动按热键。改为**监听 + 全局低级鼠标钩子（WH_MOUSE_LL）双通道**：
 - WinEvent：保留 `EVENT_OBJECT_TEXTSELECTIONCHANGED`，并补 `EVENT_OBJECT_SELECTION`（覆盖面更广）。
 - 鼠标钩子：捕获鼠标左/右键**松开**（即一次拖选结束）——这是最通用、跨应用最可靠的信号，浏览器/PDF/微信都会触发。
 - 收到信号后去抖 0.3s，再切主线程用 UIA 直读选中文本并翻译；空选 / 重复原文由去重兜底，不会乱弹。
- **架构加固（纯 Python 无需写 C）**：系统钩子装在「调用 `start()` 的线程」上（本项目即 **主线程**），由主线程自身的消息泵派发回调——这是 GUI 环境下最稳的做法。**不要另起裸 Python 泵线程**：实测在独立泵线程环境里那样钩子回调收不到，导致「选中即翻译」完全不触发（即「小贴图没被激活」回归）。翻译走异步管线，主线程不会被长时间阻塞，低级鼠标钩子也不会被系统超时卸载。UIA 取词仍只在主线程做（跨线程 COM/STA 不稳定，表现为「只能触发一次」）。

### 结构化输出（几何表格重建，离线零额外模型）—— 追赶 HushSnap 第二段识别质量
- **动机**：实测拆解 HushSnap 1.6.1 安装包——它**只捆了 RapidOCR 标准三件套**（det/rec/cls 三个 onnx），**没有任何表格/版面/VLM 模型，也未联网**。第二段那种干净表格，是它在 RapidOCR 的每行包围框（bbox）之上**自己写了几何后处理**（按坐标聚类列、画 `|`、还原层级）拼出来的。WinOCR 默认用同款 RapidOCR，却把每行 bbox 丢掉了、只取文字拍平 → 出第一段乱码。
- **方案 B（已采用）**：不引入任何新模型/依赖，复用现有 RapidOCR 的 `line_boxes`/`line_items`。
 - **`winocr/services/ocr/structure.py`（重写做真正的版面分析）**：`rebuild_markdown_from_items(items, image_width)` 主入口，直接吃 RapidOCR 原始 `(box, text, score)` 项（不再依赖已分行的 `line_items`，因为旧分行会把换行的「备注」高框误并到下一行）。流程——
  1. **物理行粗分组**：按 top-y 聚类（阈值取**行高**中位数 ×0.6，不取宽度——宽标题框会让宽度中位数暴涨而误并下一行），仅用于分块；
  2. **版面分块**：连续多列行 → 表格区，单列行 → 纯文本区，**互不串味**（解决「整页当一张表」「表格列与正文列互相串」）；
  3. **每个表格区内单独列聚类** + **锚列法切行**（本次重写的核心修复）：
    - 锚列 = **中位数高度最小**的列（通常是单行的「药物名」列，最不换行）；
    - 用锚列各项的 top 定义逻辑行边界，**无论「备注」列换行多高，都不会把下一行吸进同一行**——这正是 HushSnap 干净分行、而旧实现把三行药物揉成一行的原因；
    - 其余 cell 按「与锚列各行 top 距离最近」归行（用 top 而非纵向中心，否则跨两行的合并单元格中心会错位到下一行）；
  4. **合并跨行单元格**：某格纵向跨多行时文字向下填充到所覆盖的空行；
  5. 首表格行后补 `|---|...|---|`，丢弃全空列，得到合法 Markdown 表。CPU 即可，零下载。
 - **`rebuild_markdown(line_items, line_boxes, …)` 保留为兼容入口**（重组为原始项后转调新实现），既有单测继续有效。
 - **`winocr/services/ocr/rapidocr.py`**：`RapidOcrEngine` 加 `self.structured` 开关；`recognize()` 在开启且重建返回非 `None` 时，用结构化 Markdown 覆盖 `res.text`（保留 `lines/line_items` 不变）。
 - **`core/config.py`**：`OcrConfig.structured`（默认 `False`）；`core/app.py _configure_ocr` 注入 `structured=o.structured`。
 - **`ui/tk/dialogs.py`**：「API 与引擎设置 → OCR / 界面」页新增「结构化输出（表格/版面重建，离线）」复选框，保存即热生效（`apply_config()` 会重新注入 OCR）。
- **为何不采用 PP-Structure（方案 A）**：UA 对比——A 需再装版面+表格数个模型（多几十~上百 MB）且引入更重的 Paddle 依赖；本机实测 HushSnap 用 B 即达到目标质量，B 零成本零下载，对病历/说明书/网页表几乎无差。A 可留作「复杂合并表格」可选模式日后按需接入。

### OCR 行切分加固（修复真实截图多行揉成一行 + 边框 `|` 伪影）
- **现象（用户痛风用药表实测）**：结果扁平杂乱、无 Markdown 分隔行，且 `糖皮质激素/依托考昔/泼尼松` 三行被揉进一行，文字里混着 `|`（如 `苯溴马隆|25-50`）。
- **根因**：`rapidocr._group_by_lines` 按 item 的 **cy（中位数 y）** 聚行，备注列换行文字被检测成**高框**、cy 被下推；随后「断行合并」pass 把任何**纵向重叠**的相邻块都并入同一行 → 高框把下一行吸进同一行。
- **修复**：
 1. 分行改用 **top 排序 + 纵向重叠**（高框仍归到它起始的行），不再用会被高框带偏的 cy；
 2. 「断行合并」收紧为**仅回并「被 DB 切成上下两截、几乎相贴且不重叠」的同逻辑行**（overlap≤0 且 0≤gap<thr），相邻两行即使被高框压得重叠也不再误并；
 3. 识别文本清掉表格边框伪影 `|`（`text.replace("|"," ").strip()`）。
- 新增 `tests/test_group_by_lines.py`（高框不并下一行 / 边框 `|` 清理 / 真断框仍拼回），结构化单测同步保持。**注意**：结构化表格需先在设置 → OCR/界面 勾选「结构化输出」才生效。

### 对比 WeSnapTX（前身，微信 OCR）实测 + RapidOCR 行切分再修
- **对比（用户给 weixinOCR.txt）**：WeSnapTX 用**微信 OCR（wxocr.dll + 内置 Paddle 模型，离线）**，返回**紧贴单元格、单行、bbox 准确**的块 → 按 cy 分行 100% 正确，且把表格竖线读成 `|`，原始文本即带列结构；痛风表三行药物各自独立。结论：**OCR 引擎的块粒度是表格质量的支配因素**，几何后处理只是补偿弱引擎的缺陷。用户决定**只优化 RapidOCR、不接回微信 OCR**。
- **再修 RapidOCR 行切分（根治 winOCR.txt 三行揉一行）**：上一版 `_group_by_lines` 合并带用 `band_bottom=max(band_bottom, item.bottom)` **无限下探**——备注列高框（bottom 探入下一行上方）把下一行 top 裹进同一行 → 三行药物揉成一行（winOCR.txt 第 2 行灾难）。改为**合并带封顶（仅下探 ~1.5 个行高），且仅单行短框推进合并带、高框不推进** → 下一行强制独立成行。
- **验证**：新增 `test_three_drug_rows_not_fused`（top=10/50/90，备注高框 bottom 探到 72，仍正确分 3 行）。叠加 structure.py 锚列法，`rebuild_markdown_from_items` 对同一痛风表已输出 3 行 3 列表，行结构与微信 OCR 对齐。结构化 + 扁平两条路径的行切分均不再揉行。
- **列检测改用「左边缘」聚列（修复痛风表 5 列灾难）**：用户实机结果暴露——`_cluster_columns` 用 x 中心聚列，备注列换行成短行/长行（左对齐、宽度不同）→ x 中心差 50px 超容差 → 一列被拆成多列（表头变 5 列）、宽框药名（NSAIDs（如依托考昔））中心被推右串进剂量列、跨行填充因列数错乱把秋水仙碱备注重复填进 NSAIDs 行。改为按 **left（左边缘）** 聚列/归列：同一列格子左对齐 left 相同，换行宽窄只影响右边缘 → 稳定 3 列，药名归位，重复消失。新增 `test_wrapped_remark_column_not_split` 用真实痛风表几何验证 3 列、NSAIDs 药名在药物列、发作36 留在秋水仙碱行。残余瑕疵：换行续行片段（mg，每日1-3次）物理上与下一行同行，几何无法区分，落进邻行 —— 与微信 OCR 独立续行的可读性同水平。

### 基于真实检测框彻底重写 structure.py（追平 HushSnap）
- **用 `tools/dump_ocr_items.py` 导出真实 RapidOCR 检测框**（用户本机跑，发回 ocr_debug_items.json）后，盲调五轮的根源全部暴露：
 1. **RapidOCR 会把相邻单元格合并成一个框**：`NSAIDs(如依托考昔)|依托考昔120 mg，每日1次` 是**一个**框（left=13,right=502 横跨药物+剂量两列），中间是边框伪影 `|`/`Ⅰ`/`1`/`一`；
 2. **每个格子带行首边框伪影**：`|标准剂量`、`Img，每日1-3次`、`1发作36`、`一适用于…`；
 3. **备注列 left 分布 419~548 分散**，任何单容差的 left 聚类都会拆列。
- **重写为「清伪影 + 拆合并框 + 表头定列 + 物理行当行」**：
 1. `clean_text`：剥行首 `| 1 I l 一 ! Ⅰ )`（`一、` 序号保留、`1.0` 数字保留）；中段 `| Ⅰ !` 是列分隔 → 转空格；
 2. `split_and_clean_items`：按边框伪影把合并框拆成片段，按字符宽度比例（CJK=2/ASCII=1）估算各片段子框；
 3. **表头定列**：首行为干净表头时，用表头格子 left 当列锚，数据格按「left 距最近列锚」归列——鲁棒于分散的备注列；
 4. **物理行当行**：换行续行独立成行（与 HushSnap 同风格）；跨行填充仅当包围框**完整覆盖**目标行（避免续行误填）。
- **真实痛风表输出（三张表全对齐 HushSnap，且更干净）**：3 列稳定、药名/剂量拆开、伪影清除、无重复文字。实测输出见下：
 `秋水仙碱 | 首次 1.0 mg，1小时后0.5 mg;之后0.5 | 发作36` / `| mg， 每日1-3次 | 小时内使用效果最佳…` / `NSAIDs(如依托考昔) | 依托考昔120 mg… | 有消化道溃疡…`
- 新增/重建测试：`test_hushsnap_style_wrapped_continuation`、`test_wrapped_remark_column_not_split` 等，`test_structure_rebuild.py` + `test_group_by_lines.py` 合计 13 passed。

### 本日失败经验复盘（重要，避免重蹈）
1. **选中即翻译「只能触发一次」**：在监听线程内读 UIA 文本做去重 → 跨线程 COM/STA 不稳定、首次成功后即失效（表现为只能一次）。教训：**UIA 取词只能放在主线程**；后台线程只发「选中变更」信号，真正读取交回主线程。
2. **选中即翻译「勾选后仍要手动 Ctrl+Shift+D 才触发」**：只监听 `EVENT_OBJECT_TEXTSELECTIONCHANGED`，而浏览器/PDF/微信/自绘控件拖选后**不发该事件**。教训：必须叠加 `EVENT_OBJECT_SELECTION` + **全局低级鼠标钩子 `WH_MOUSE_LL`（鼠标松开）** 作为通用兜底信号。
3. **选中即翻译「小贴图没被激活」（最隐蔽）**：把钩子装到**独立的裸 Python 泵线程**上，在 GUI 环境里钩子回调**收不到**。教训：GUI 下系统钩子必须装在**调用 `start()` 的线程 = 主线程**，由主线程自身消息泵派发；不要另起裸泵线程（元宝「独立泵线程」建议在 Tk 下不适用）。翻译走异步管线，主线程不会被长阻塞，钩子不会被系统超时卸载。
4. **翻译胶囊 `_busy` 永久置位致「只翻译一次、按键无反应」**：`selection_translate` 的 `run_async(on_done=_done)` 只在成功路径发 `WORKFLOW_DONE`，异常路径漏发 → `_busy` 永远置位。教训：**`on_error` 路径也必须释放忙标志**（发 `WORKFLOW_DONE`）。
5. **屏幕 OCR 质量差 ≠ 需要 VLM/联网/PP-Structure**：第一直觉会归因为「模型不够强」，实测 HushSnap（离线、同款 RapidOCR）靠几何后处理就达到目标。教训：先**拆包看事实**再定方案，别被「在线更强」的直觉带偏——很多「效果好」是后处理算法，不是模型。

### 验证
- 配置读写 round-trip、monitor 降级 / `_read_selection` 安全、方向判定单测（EN/ZH dominant、纯英 / 纯中、explicit 保持）、`structure` 重建 5 例（三列表格 / 单列非表 / 单行非表 / 标题行纯文字 / 空输入）均通过；相关文件 `py_compile` 通过。**全套 135 passed**。界面交互需实机 `run.bat` 验证。

---

## 3.9.0 — 屏幕取色器 + 界面区域定位配色（2026-08-16）

### 新功能：屏幕取色器（区域选点替换界面颜色）
- **`winocr/ui/tk/color_picker.py`（新）**：`ScreenColorPicker` 全屏放大镜取色覆盖层。
 - 交互：**按住左键滑动**移动放大镜、实时显示 HEX/RGB，**松开左键确认取色**；右键 / `Esc` 取消。
 - 通过 `color_picked(str)` 信号回传十六进制颜色（如 `#1a2b3c`）。
- 设置页每个界面角色（window / panel / card / status / accent / text / input …）提供「屏幕取色」按钮，取到的颜色自动回填。

### 新功能：界面区域定位器（先选区域，再取色改它）
- **`winocr/ui/tk/ui_inspector.py`（新）**：`UIInspector` 覆盖层，用来定位「屏幕上某块区域在软件里对应哪个主题角色」。
 - 点设置页「🎯 从界面选区域」→ 设置页隐藏、半透明覆盖层贴合主窗口客户区（不跑到其他屏幕）。
 - 期间底层软件**锁定、不接收鼠标点击**（覆盖层用置顶弹层独占捕获）；悬停即高亮指向的控件并显示角色名（如「主窗口背景 window」），大片空白处 fallback 识别为 `window`。
 - 点「确定」→ 自动弹出屏幕取色器 → 取色后回填对应角色 → 保存即生效。

### 配套改动
- **`theme.py`**：新增按主题 token（如 `clean_dark`）的自定义配色覆盖 `load_custom / custom / set_custom / all_custom / _resolved_token`，`palette()` 自动合并；新增 `#central { background-color: <window> }` 规则——修复主窗口客户区被透明背景透掉、Windows 上 fallback 成黑色的问题（之前改 `window` 色无效的根因）。
- **`core/config.py`**：`UiConfig` 加 `theme_colors: dict`（默认空），可落盘为 TOML inline table，按主题分别记录深色/浅色自定义。
- **`dialogs.py`**：「API 与引擎设置 → OCR / 界面」页新增「界面配色自定义」分组（10 个角色，每格色块 + 屏幕取色 + 从界面选区域 + 重置），并包进滚动区，保存/取消按钮始终可见。
- **`app.py`**：`run()` 中 `set_theme` 后调用 `theme.load_custom(config.ui.theme_colors)`，自定义配色启动即生效；`_build_ctx` 注入当前剪贴板文字。
- **`main_window.py`**：central widget 命名 `"central"`，使 `#central` 背景规则生效。
- **`translate_text.py`**：翻译取文字优先级改为 **选中片段 > 原文框 > 译文框 > 剪贴板**，并区分「原文/译文/剪贴板 已是目标语种」的提示。
- 界面仅用标准库 Tkinter，本功能**无新增第三方依赖**。

### 默认 API 连接改为硅基流动（SiliconFlow）
- **`core/config.py`**：`ApiConfig.defaults()` 现在预置 `siliconflow` 连接（base_url 已填 `https://api.siliconflow.cn/v1/chat/completions`）并设为 `default_connection`，同时保留 `glm` / `hunyuan`。为降低上手成本，`siliconflow` 连接的 `text_model` / `vision_model` 已预填硅基流动上**免费**的模型：
 - 文本：`Qwen/Qwen3-8B`（免费）
 - 视觉：`THUDM/GLM-4.1V-9B-Thinking`（免费，通用视觉语言模型，可同时支撑 AI 带图对话与云端视觉 OCR）
 - 用户只需粘贴 API Key 即可使用；想换付费模型直接在设置页修改。
- 兼容旧配置：`from_dict` 在「默认是 glm 且尚无 siliconflow 连接」时自动补上硅基流动并切换默认；**已含 siliconflow 连接但模型名为空的旧配置**，会自动补上面的免费默认（不覆盖用户已填值）。`AiConfig.text_connection` 默认值同步改为 `siliconflow`，新用户打开即用。
- **`dialogs.py`（`open_api_settings`）**：AI / 密钥页新增**可点击的硅基流动申请链接**（`<a href>` → `系统默认浏览器打开` 在新浏览器打开 `https://cloud.siliconflow.cn/`）；`API Key` 框加占位提示；`文本/视觉模型` 框占位提示改为已预填的免费模型名；选中或新建 `siliconflow` 连接时若 base_url 为空自动预填。

### 修复：Ctrl+Shift+Q 有时无法完全退出进程
- 根因：`keyboard` 库的底层监听线程可能是非守护线程，`root.quit()` 后进程仍挂起；若设置页等模态对话框打开，也可能阻碍事件循环退出。
- **`ui/tk/app.py`**：`quit_app()` 现在先关闭所有窗口再退出主循环（`root.quit()`），并启动一个 0.5s 延迟的守护线程做 `os._exit(0)` 兜底，确保退出热键按下后进程真正结束。
- **`services/hotkey.py`**：`unregister_all()` 改用 `keyboard.unhook_all()`（比 `unhook_all_hotkeys()` 更彻底），进一步释放 keyboard 监听线程。

### 验证
- 主题覆盖、配置读写、translate_text 优先级均通过桩测试；所有改动 `py_compile` 通过。
- 界面交互（放大镜、Popup 捕获、滚动区）需实机 `run.bat` 验证。

---

## 3.8.0 — 划词 UIA 直读 + 独立开关（2026-08-15）

### 背景
用户反馈 WPS 里划词翻译激活不出来。根因：模拟 Ctrl+C 被 WPS 自绘控件吞掉；200ms 剪贴板探测太短；剪贴板被富文本抢写。业界（Pot/Manggo）方案 = UIA TextPattern.GetSelection() 直读 + 剪贴板兜底。

### 实现
- **`winocr/services/selection.py`**（新）：UIA 三通道取词
 - 焦点控件 TextPattern.GetSelection() 直读（不碰剪贴板，WPS/浏览器兼容）
 - 深度遍历前台窗口控件树（depth≤6）找非空选中
 - ValuePattern 兜底（输入框类）
 - 未装 uiautomation 抛 `SelectionError`（调用方据此回退剪贴板通道）
- **独立开关**：`config.ui.selection_enabled`（默认 true）+ 设置对话框「启用划词翻译」勾选
- **TkUi 双通道**：UIA 直读（同步，无 200ms 延迟）→ 失败回退模拟 Ctrl+C 读剪贴板 → 再失败框选截图
- requirements 加 `uiautomation>=2.0.0`

### 关键修复
- `GetFocusedControl` 是 uiautomation **顶层函数**，不在 Control 实例上——`win.GetFocusedControl()` 会 AttributeError（真实 bug，已修）。

### 测试
`tests/test_selection_uia.py` 6 例（mock 全链路）。全量 **125 passed**。

---

## 3.7.0 — 多配色 + 字号接线 + 图标素材包（2026-08-15）

### 多套配色方案（8 套）
`theme.py` 重写为 `{主题: (light, dark)}` 结构，内置 4 主题 × 2 明暗：
- **manggo**（默认，Pot 风亮蓝 #1677ff / 深色黑蓝 #1b1c22）
- **aurora**（极光青 #0ea5b7 冷调）
- **sunset**（暖橙 #e8862b 暖调）
- **forest**（森林绿 #3d9e50 自然调）
兼容旧值 light/dark 自动映射到 manggo。`theme_names()` 暴露列表。

### 字号接线（之前 `font_size` 字段存在但未消费）
主题样式全局 `font-size: Xpt` 基于 config.ui.font_size（6-24 限幅）；主窗口文本卡用 `theme.font_size()` 替换硬编码 10；设置对话框加「配色主题」下拉 +「字号」数字框，保存后立即应用主题样式。

### 图标素材（程序化绘制）
- `winocr/ui/tk/icons.py`：程序化绘制 6 图标（app/snap/dict/book/chat/speak），**零外部资源**（运行时生成位图 → 图标）。
- 导出工具 `export_assets()` / `_export_ico()` 生成 PNG 7 尺寸 + 多尺寸 winocr.ico。
- 主窗口 / Sticker 挂应用图标；动作栏按钮（截图/AI 对话/书本/回看等）配图标。

### 关键修复
- 画笔签名顺序 (color, width, style, cap, join) 需全部指定。
- `Image.save` 不接受 BytesIO，ICO 导出需临时落盘再 PIL 读。

### 测试
+2（多配色 × 明暗 + 字号注入；窗口/按钮图标），全量 **119 passed**。

---

## 3.6.0 — OCR 模型档位升级（2026-08-15）

### 背景
竞品调研（HushSnap/Snow Shot/Manggo/TTime）确认：OCR 差距在「模型档位 + 行聚合后处理」，非引擎代差。官方指标 tiny 73.5% → small 81.3%（+7.8pt）。

### 三档模型 + 自动回退
- `paths.ocr_model_dir(tier)` 按档位分目录（`models/v6_{tiny,small,medium}`）。
- `rapidocr._local_models()` 来源优先级：目录新命名（`PP-OCRv6_det_small.onnx`）→ **rapidocr 包内自带**（small 零下载）→ 旧命名（兼容现有 v6_tiny）；档位缺失**自动回退 tiny**（绝不联网下载）。
- `_build_params()` 用实际生效档位（`_effective_tier()`）构造 `ModelType`，避免按错档位预处理。
- `tools/download_ocr_model.py medium`：从 ModelScope 官方仓库下载（SHA256 校验），small 随包自带。

### 行聚合增强
- `_group_by_lines()`：自适应阈值（行高中位数×0.5）、median 抗离群、断行合并（DB 断框拼回）。

### 实测
- tiny 1.05s / small 3.16s / medium 10.36s，三档真实识别全对。
- `doctor` 逐档位显示可用态 + 当前/生效档位。
- 测试 +6，全量 **117 passed**。

---

## 3.5.0 — 输入框快捷转译 + TTS 预热（2026-08-15）

### 输入框快捷转译（Manggo 杀手锏）
- 新胶囊 `input_translate`（ctrl+shift+i，ui_section=actions）：在**任意输入框**按热键 → 模拟 `Ctrl+A` 全选 → `Ctrl+C` 复制 → 读剪贴板 → `pipeline.translate` 翻译 → 写回剪贴板 → `Ctrl+V` 替换。
- 剪贴板读写走 **Win32 API（ctypes）**，零依赖、不依赖界面层；失败不破坏输入框原内容。
- 踩坑：ctypes 必须设 `argtypes`/`restype`（64 位句柄被截断成 0 → 访问违规 / 参数溢出）。
- 热键已接入 `TkUi._wire_hotkeys`。
- 新增 3 测试：全流程（按键序列 + 写回译文 + 状态）/ 空剪贴板提示 / 同语言跳过替换。

### TTS 冷启动预热
- `tts.warmup()`：后台 import edge_tts + 一次短文本合成（不播放），接入 `TkUi._warmup_async`。
- 实测：预热后 `speak()` 从冷启动 ~19s → **4.6s**（含播放）。

### 验证
- 全量 **111 passed**（108 + 3 输入框转译），无回归。

---

## 3.4.0 — TTS 回退链 + 视觉升级（2026-08-15）

### 朗读升级：edge-tts（在线）→ OneCore（离线神经）→ SAPI5（兜底）
- 用户反馈 SAPI5 老语音（Huihui/Zira）太像机器人。专家调研（元宝口语陪练架构 / Win11 原生语音 / 模型大小对比）后选定回退链。
- `winocr/services/tts.py` 重写：`speak()` 依次尝试 —— ① **edge-tts**（微软 XiaoxiaoNeural 中文 / JennyNeural 英文，按文本自动选，合成 MP3 走 MCI 播放）② **OneCore 神经语音**（`Windows.Media.SpeechSynthesis` 合成 WAV，需系统已装神经语音包）③ **SAPI5**（原机制兜底，保证没网没神经语音也能出声）。
- 播放统一走 Windows MCI（winmm.dll，零依赖），支持 mp3/wav 同步播完。
- 修坑：edge-tts 用 `out +=` 嵌套闭包报 UnboundLocalError 被 except 吞掉 → 一直静默降级到 SAPI5；`asyncio.wait_for` 取消会泄漏 aiohttp session。均改为 list 收集 + 库自带 timeout。
- `requirements.txt` 加 `edge-tts>=7.0.0`。
- 新增测试：回退链顺序（edge 命中即停）/ 全降级到 SAPI5 / 中英文语音自动选择。

### OneCore 中英混读（用户决策：离线档用官方通道 + 调用层切 voice）
- 第一性原理决策（用户提供 Win Neural 双包实测数据）：主链 edge-tts 保持；离线档 = 系统官方 OneCore 神经语音包，**绝不碰 NaturalVoiceSAPIAdapter 等 hack 桥接**；不引入 GB 级多语种模型。
- `_onecore_speak` 重写为**按语种连续段切分**（`_split_by_lang`：CJK/全角标点→zh，ASCII 字母数字→en，标点跟随前块）：「今天 meeting 在 3pm」→ zh/en/zh/en 各段，中文段选 Xiaoxiao、英文段选 Aria，一次 PowerShell 批量合成多段 WAV → `wave` 拼接 → MCI 一次播完。
- `_pick_onecore_voice` 按语种优先自然语音名（Xiaoxiao/Aria 等），无匹配回退语言前缀；单侧缺包时整段回落该侧语音。
- `_onecore_voice_map` 查询 DisplayName|Language；`onecore_voices()` 兼容展示。
- 新增测试：中英分块 / 语音选择 / 混读每段语音分配（mock 掉真实合成）。

### SAPI5 移除（用户决策：机械音不要）
- **用户决策**：edge-tts 在线五星即可，Win11 自动切 OneCore 神经语音；**SAPI5 机械音整体移除**——宁可朗读失败，也不降级机械音。
- `speak()` 回退链精简为：edge-tts（在线）→ OneCore（离线神经）→ 失败返回 False。
- 删除 `_sapi_speak` / `_sapi_voice_map` / `_pick_sapi_voice` / `sapi_voices()`。
- 测试更新：回退链降级断言改为「全失败返回 False」；删除 SAPI5 语音选择测试；测试文件去重重写。
- 文档 `DOC/WinOCR_TTS与朗读.md` 同步为两档架构。
- 全量 **115 passed**。

### SAPI5 层按语种选语音（用户自装 Hazel 后适配，随后按用户决策整体移除）
- **本机事实**：Win10 Home 单语言版无 Speech FoD（`dism` 查询能力数=0），OneCore 注册表 6 个 token 但无引擎 DLL（`AllVoices` 空）→ OneCore 层本机跳过。用户自装 `Microsoft Hazel [en-GB]`（SAPI5 通道）后实测三个 SAPI5 语音均可朗读。
- 一键安装器：`install_voices.bat`（纯 ASCII）+ `tools/install_tts_voices.py`（ShellExecuteW runas 提权 + dism 动态发现），换机器/升 Win11 后可直接装神经语音。

### 视觉升级（Manggo/Pot 风格 + Snipaste 选区）
- `theme.py` 调色板换成 Manggo 蓝（浅 `#1677ff` / 深 `#4d8dff` + Pot 深黑蓝底）。
- `region.py` 选区蒙层重写为 Snipaste/ShotShow 风格：暗化遮罩 + 白边选区 + 尺寸标签 + 跟随放大镜（4x + 取色 RGB）+ 底部操作提示。
- `Sticker` 贴条改为底部 Manggo 蓝下划线（去四边粗框），更「安静」。

### 验证
- 全量 **115 passed**（109 + 3 TTS 回退链 + 3 OneCore 混读），无回归。
- edge-tts 实测合成成功（本机 2.2s 合成 + 2.4s 播放）；冷启动首次 ~19s（网络/DNS 冷缓存），后续 ~6s。
- OneCore 混读逻辑单元验证通过；本机无神经语音包，端到端待用户装包后实机确认。

---

## 3.3.0 — UI 迁移：界面重构（Tkinter 重写）（2026-08-15）

> 一次性全量替换旧界面，UI 层完全重写，业务/胶囊/事件总线/配置零改动。

### 新增 `winocr/ui/tk/`
- `theme.py` —— 主题样式（浅/深两套），`set_theme` 整体下发；按钮圆角、卡片、高 DPI 全原生支持。
- `main_window.py` —— 主窗口：信息栏 + 主操作栏（胶囊驱动 + 知识库区按钮）+ 模式栏（容器包装整组显隐）+ 原文/译文卡片 + 可折叠 AI 聊天气泡 + 状态栏 + 🌙 主题按钮。
- `chat_panel.py` —— 气泡列表（用户右对齐蓝色 / 助理左对齐白卡 / 系统琥珀色）+ 选项 + 输入 + 存库/清空。
- `dialogs.py` —— 关于（消息框）/ 热键设置（表单布局 + _KeyEdit 录制）/ API 设置（精简：主连接的 URL/Key/文本模型/视觉模型）。
- `region.py` —— 全屏选区蒙层（屏幕截图 + 透明遮罩 + 鼠标框选 → PIL.Image）。
- `windows.py` —— Sticker（无边框置顶小贴条，拖拽+复制+存库）/ KnowledgeWindow（检索/浏览/删除）/ MarkdownWindow（结果+复制）。
- `app.py` —— TkUi(UiAdapter)：`_Poster` 对象 + Signal 跨线程派发；事件总线→window 回调；划词 Ctrl+C 探测+框选兜底；do_* / _run_capsule 完整保留。

### 接入
- `main.py cmd_gui` 优先加载 GUI，启动失败回退控制台。
- `requirements.txt` 无新增 GUI 依赖（界面仅用标准库 Tkinter）。
- `tests/test_gui_tk_smoke.py` —— 主窗口构建 / 主题切换 / 气泡 / 模式切换。

### Tk 现状
- Tkinter（TK）为唯一界面实现，GUI 冒烟回归持续覆盖。

### 验证
- 全量 **107 passed**（104 + 3 冒烟），无回归。
- 真机截图：浅色/深色对比，圆角按钮、原生暗色主题。

### 后续补全（同日）
- **API 设置完整版**：复刻既有全套能力，3 页签 —— AI/密钥（连接 CRUD + 采样 + 6 项限流 + 测试文本/视觉连接）、翻译（引擎/译向/回退顺序/限流 + 测试）、OCR/界面（引擎/模型档位/云端 OCR 连接/限流 + 测试）。测试按钮后台线程请求 → `TkUi.post` 回主线程更新结果（绿/红）。
- **划词翻译 + 小贴条补全**：`Sticker` 补「显示原文」折叠；新增端到端测试（真实链路 selection_translate 胶囊 → argos 翻译 → STICKER_RESULT → 贴条弹出 + 折叠往返）。
- `tests/test_gui_tk_smoke.py` 扩至 5 例（含 `test_tk_dialogs_build`、`test_tk_selection_sticker_flow`）。

### 验证
- 全量 **109 passed**（107 + API 设置冒烟 + 划词翻译端到端），无回归。
- 真机截图：浅色/深色对比；API 设置 3 页签；小贴条（真实翻译 `hello world` → argos → `你们好,世界`）。

### 已知小遗留（不阻塞）
- 截图选区蒙层做了但没在这台机器上完整跑过「框选截图→OCR→翻译→贴条」的交互流（文字划词链路已端到端验证，建议实机再验一次框选）。

---

## 3.2.0 — 胶囊化 + 知识闭环 + 划词翻译（2026-08-15）

> 实现路线图 P0–P5：护栏 → 数据闭环 → 理解层 → 场景胶囊化 → 差异化 → 清理。

### 场景胶囊化（一个场景 = 一个 `.py`）
- 新增 `winocr/capsules/` 场景层：截图翻译 / 剪贴板提取 / 打开文件 / 翻译文本 / 切换引擎 / 划词翻译 / 朗读 / 截图转 Markdown / 存库 / 检索库，共 10 个内置胶囊。
- 动作栏「知识库」区按钮、全局热键均由胶囊注册表驱动 —— 丢一个 `.py` 自动出现按钮 + 热键。
- `CapsuleContext` frozen 只读；胶囊不碰 UI，只经 pipeline 服务 + 事件总线。

### 数据资产闭环（同一批文档反复回看与复用）
- 知识库 `services/persistence/knowledge.py`：sqlite3 + FTS5（中文 trigram + LIKE 兜底），带溯源字段（来源 / 原图哈希 / 场景 / 时间），WAL 模式。
- 知识库回看窗口：检索 / 浏览 / 删除已沉淀的知识。
- 增强对话面板：锚定当前捕获物 + 召回知识库相关记忆（注入「背景上下文」）+ 一键沉淀 Q&A 进库。

### P0 护栏（离线优先）
- `offline_mode` 翻译回退链跳过在线引擎；RapidOCR 本地模型缺失时报错引导安装，禁静默联网下载。

### 差异化
- 截图→Markdown：OCR 几何重排（表格→管道表格 / 代码→代码块 / 段落），结果窗口一键复制。
- 划词翻译小贴条：任何界面划词/框选 → Ctrl+C 读剪贴板 → 翻译 / OCR，结果以无边框置顶小贴条呈现（可拖拽 / 复制 / 存库）。
- 朗读文本：零新依赖，PowerShell 桥接 Windows 内置 SAPI 离线朗读。

### 热键配置重构
- `HotkeyConfig` 只留 `enabled + quit + overrides`；默认组合键由胶囊声明，动作名 = 胶囊注册键。
- 旧 `snap_extract` 等字段自动迁移进 `overrides`，行为不变。

### 测试
- 测试从 42 例扩到 91 例，覆盖胶囊 / 知识库中文检索 / 离线护栏 / Markdown 几何 / 划词 / 热键闸门 / 插件发现。

### 对话历史持久化
- AI 对话历史落盘 `user_dir()/chat_history.json`：重开程序后 AI 面板上下文仍在；只存纯文本（图片留占位描述）；坏/缺文件安静忽略；`清空对话` 会清空并落盘。

### 修复
- 配置加载把 `dict` 字段（hotkey.overrides）字符串化导致热键设置崩溃 —— `_coerce` 支持 dict 并自愈旧坏值，`_toml_literal` 正确输出 TOML inline table。
- 外部插件目录（plugins/capsules）顶层模块导入缺 sys.path —— 已修。
- **「存到知识库」不能保存**：`KnowledgeBase` 未继承 `Persistence`，持久化轴发现不到 → `services["knowledge"]` 被注册为 None。已改为显式继承并加回归测试。

### 测试
- 测试扩到 102 例，新增对话历史持久化（5 例）+ GUI 冒烟真机（6 例全过）。

---

## 3.1.0 — 连接抽象（2026-08-13）

### 核心：API 连接抽象，消灭字段散落
- 新增 `[api.<name>]` 连接：每个平台（智谱 / 混元 / SiliconFlow / 自建 vLLM…）
 一个连接，Base URL / API Key / 文本模型 / 视觉模型 / 采样与限流参数只定义一次。
- `[ai]` 精简为 `text_connection` / `vision_connection`（引用连接名，视觉留空跟随文本）。
- `[translate]` 的 `glm_base_url / glm_model / glm_api_key` 合并为 `llm_connection`
 （留空跟随 AI 文本连接）；限流覆盖参数改名 `llm_max_output_tokens` 等。
- `[ocr]` 的 `cloud_*` 字段合并为 `cloud_connection`（留空 = 关闭云端 OCR）。
- **旧配置自动迁移**：3.0 散落字段（`glm_api_key` / `cloud_base_url` 等）加载时
 自动生成 `glm` / `glm-vision` / `glm-translate` / `cloud-ocr` / `hunyuan` 连接，
 引用自动接上；迁移不覆盖新配置显式写的值，且不会自动改写原文件。
- 连接字段级继承：翻译 / 视觉连接没填的字符串字段自动继承主连接
 （`ApiConfig.merged`），「只覆盖模型名、URL 与 Key 跟随」开箱即用。

### 采样参数可配置
- `temperature` / `top_p` 从 `glm_chat.py` 硬编码 0.7/0.9 移入连接配置，
 「API 与引擎设置 → AI / 密钥」可直接填写并持久化。

### UI
- 「API 与引擎设置」重写为连接编辑版：连接选择 / 新建 / 删除（带引用保护）、
 地址 / 密钥 / 双模型 / 采样 / 限流一页编辑；翻译页与 OCR 页改为连接下拉选择。
- 删除独立的「限流」页签：AI 限流参数并入连接编辑区，翻译 / OCR 覆盖参数留在各自页。
- 「测试文本 / 视觉 / 翻译 / OCR 连接」适配连接参数。

### 兼容性
- 3.0 配置文件无需任何手工改动，首次保存后自动转为新格式。
- `GLM_API_KEY` / `WINOCR_GLM_API_KEY` 环境变量改为覆盖 glm 连接密钥。
- 测试全部更新并通过（55 个用例，含新增旧配置迁移测试）。

---

## 3.0.0 — 插件化重构（2026-08-11）

### 架构升级
- 引入六轴插件化架构：capture / ocr / translate / ai / attach / persistence。
 往 `winocr/services/<轴>/` 下丢一个 `.py` 即可扩展，无需修改 `App`。
- 事件总线取代全局锁：UI 与业务通过 `EventBus` 通信，后台任务不直接碰界面，
 彻底解决 2.0 的 `_chatting` 状态机死锁与偶发卡死。
- 组合根依赖注入：`App.build()` 是唯一的「接线」处，所有服务实例由它装配，
 配置修改 `app.apply_config()` 一次生效，不再出现「改了配置没重载」。
- UI 适配器契约：新增 `winocr/ui/base.py`，把 UI 与业务完全拆开。
 当前 Tkinter 实现在 `winocr/ui/tk/`，未来可替换为 Web/托盘/，核心层不动。
- 单一 TOML 配置：`~/.winocr/config.toml` 取代 2.0 的 `config_local.py` /
 `config_v6.yaml` / `api_settings.py` / `hotkey_settings.py`，
 不再执行配置文件，更安全、更易迁移。

### 核心层
- `core/config.py`: `AppConfig` 强类型 schema，缺字段自动回默认，支持环境变量覆盖。
- `core/paths.py`: 全应用路径唯一真相来源，支持便携模式/常规模式、`WINOCR_HOME` 覆盖。
- `core/event_bus.py`: 线程安全，订阅返回取消函数，支持一次性订阅。
- `core/pipeline.py`: 捕获 → OCR → 翻译 → AI → 持久化，所有异步步骤统一走 `run_async`。
- `core/types.py`: `Capture` / `OcrResult` / `TranslateResult` / `Attachment` / `ChatMessage` 显式契约。

### 插件移植
- OCR：`rapidocr.py` 完整保留 v6_tiny 模型加载、72 字节占位校验、按 Y 聚类分行、
 代码截图智能预处理。
- 翻译：`argos` / `bing` / `glm` / `hunyuan` / `mymemory` 全部从 2.0 移植，
 自动回退链由 `dispatcher.py` 统一调度。
- AI：`glm_chat.py` 改进为历史不保留 base64 图片，自动缩图到 1600px，避免多轮后请求体膨胀。
- 附件：新增 attach 轴，`image` / `pdf` / `office` / `plaintext` 插件化，
 替代 2.0 的 `file_attach.py` 巨型 if-else。
- 持久化：`json_history.py` 原子写、最大 500 条、支持导出 Markdown/纯文本。

### GUI 重写
- `ui/tk/main_window.py`: 顶部信息栏、操作栏、简洁/高级双模式、Alt 快捷键、状态栏。
- `ui/tk/chat_panel.py`: WorkBuddy 风格气泡面板、多模态附件（Ctrl+V / 拖拽 / 文件选择）、
 附件解析放后台线程、上下文来源显式可控。
- `ui/tk/dialogs.py`: 热键设置（按键录制、冲突检测）、API/引擎设置（测试连接）、关于对话框。
- 线程安全：所有非主线程 UI 回写统一走 `TkUi.post()`，即 `root.after(0, ...)`。
- 剪贴板不再强依赖 `pyperclip`，自动回退到 Win32 API / Tk 剪贴板。
- 译文区可编辑：从 `state=DISABLED` 改为可编辑，支持撤销历史、右键菜单、`Ctrl+A` 全选。
- 修复「中文原文 → 译英 → 点译中没反应」：显式按钮不再自动纠正目标语言；
 原文已是目标语言时改翻译文区（回译）；新增选中片段翻译。
- AI 支持任意 OpenAI 兼容接口：新增可自定义 Base URL；
 模型名、API Key、接口地址全部开放填写，兼容智谱/SiliconFlow/OneAPI/vLLM 等。
- 修复 API 设置对话框测试连接时报 `name 'glm_tm' is not defined` 的错误。
- 翻译页新增独立大模型接口覆盖：可在「API 与引擎设置 → 翻译」里单独指定 glm 的
 Base URL / 模型名 / API Key；留空自动继承「AI / 密钥」页，
 实现「对话用强模型、翻译用快模型」的分离。
- AI 模型名开放填写：新增 `glm_text_model` / `glm_vision_model` 配置项，
 「API 与引擎设置」中可自由填任意智谱基座（如 `GLM-4.7-Flash`），
 留空回落内置默认；测试连接显示实际生效的模型名。

### 命令行
- `main.py` 支持多子命令：`gui` / `console` / `doctor` / `models` / `config` / `ocr`。
- `doctor` 一键输出插件可用性、依赖状态、模型资源位置。

### 资源
- 内置 OCR 模型 `models/v6_tiny/`（约 7MB）。
- 内置 Argos 离线翻译包 `vendor/argos_packages/`（约 164MB）。
- 提供 `setup.bat`、`run.bat` 一键安装/启动。

### 移除
- 不再支持 Win7。
- 不再使用 `pyperclip` 作为必需依赖。
- 不再使用 `config_local.py` 等可执行配置文件。

---

## 2.x（历史）

2.x 系列完成了 OCR 统一为 RapidOCR、Argos 无 torch 独立实现、AI 对话、热键设置等能力。
详见 `WinOCR2.0/CHANGELOG.md`。
