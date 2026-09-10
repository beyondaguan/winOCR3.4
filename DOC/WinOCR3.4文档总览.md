# WinOCR 3.4 文档总览

> **锚点版本：3.4.27（2026-09-11）**。本文件是项目**唯一技术文档**，由原 8 份文档
> （文档总览 / 从零复现 / 小白代码导览 / 踩坑 / 团队规范-修复节奏 / AI 辅助开发规范 /
> 项目栏蓝图 / 迭代计划）按当前代码状态合并重写而成，历史版本可在 git 历史中找回。
> 版本变更明细见 [`../CHANGELOG.md`](../CHANGELOG.md)。

---

## 1. 项目定位与核心参数

Windows 桌面工具：**截图 → OCR 识别 → 翻译 → AI 解读** 一键完成，专为阅读外文资料的普通用户设计。

| 维度 | 事实 |
|------|------|
| 版本 | 3.4.27（`winocr/version.py` 为单一真相源） |
| 技术栈 | 纯 Python 3 + Tkinter，无 GUI 框架 |
| OCR | RapidOCR（PP-OCRv6，tiny/small/medium 三档，本地离线）+ Windows 系统视觉接口 |
| 翻译 | llama.cpp 本地大模型（GGUF）、Argos 离线神经翻译、MyMemory、智谱 GLM、混元 |
| AI 对话 | OpenAI 兼容接口 / GLM / 混元 / llama.cpp 本地模型 |
| 离线能力 | OCR 模型与中英翻译包全部本地，断网完整可用（`[translate] offline_mode=True` 仅走离线引擎） |
| 测试 | 32 个测试文件、212 个用例（`tests/`） |
| 入口 | `main.py`（GUI / console / doctor / models / config / ocr 子命令） |
| 分发 | 便携模式（模型内置于 `models/`、`vendor/`）或常规模式（数据放 `~/.winocr`） |

## 2. 快速开始

```cmd
setup.bat          :: 建虚拟环境、装依赖、自动下载 OCR 模型(tiny+medium)+Argos 中英包、自检
run.bat            :: 启动图形界面
```

命令行等价与进阶：

```cmd
python -m venv .venv && .venv\Scripts\python.exe -m pip install -r requirements.txt
python main.py                    :: 启动 GUI（默认）
python main.py console            :: 无界面模式
python main.py doctor             :: 自检：插件可用性、模型、依赖
python main.py models             :: 查看 OCR / Argos 模型资源
python main.py config --init      :: 生成默认配置
python main.py ocr 图片.png -t zh-CN
python tools/download_ocr_model.py [tiny|medium]    :: 单独下载 OCR 模型（medium 约 133MB）
python tools/download_argos.py                       :: 单独下载离线翻译包（约 140MB）
```

零下载起步：`config.toml` 设 `ocr.model_type = "small"`——small 模型随 rapidocr pip 包自带，装完依赖即可识别。

## 3. 代码地图（四层结构）

```
main.py                      # CLI 入口：解析子命令 → 组装 App → 启动
winocr/
├── core/                    # 核心层：组合根与全局机制
│   ├── app.py               #   App 组合根：发现插件→构造服务→依赖注入→事件总线
│   ├── config.py            #   单一 TOML 配置：schema 纠偏、旧字段迁移、projects 一致性
│   ├── projects.py          #   ProjectManager：项目生命周期与数据隔离
│   ├── event_bus.py         #   事件总线：订阅/派发（快照式，回调异常隔离）
│   ├── paths.py             #   路径决策：便携模式 vs ~/.winocr
│   ├── crash_handler.py     #   崩溃捕获：sys/threading/tk 三处接管 + faulthandler
│   └── guards.py / exit_guard.py  # 进程单例锁、退出兜底
├── services/                # 服务层：六轴 + 通用客户端
│   ├── capture/             #   捕获轴：截图、剪贴板（Win32 句柄备份/还原）
│   ├── ocr/                 #   OCR 轴：rapidocr（按模型路径签名缓存引擎）、系统视觉
│   ├── translate/           #   翻译轴：llama_cpp（实例锁串行）、argos、mymemory、glm、混元
│   ├── ai/                  #   AI 轴：llama_cpp_chat、glm_chat、openai 兼容对话
│   ├── attach/              #   附加轴：TTS（SAPI/MCI 双引擎降级）、hotkey、tray、win32_hotkey
│   ├── persistence/         #   持久化轴：json_history（原子写+.bak 备份）、knowledge（SQLite FTS5）
│   └── openai_compatible/   #   OpenAI 兼容客户端（重试/超时/流式）
├── ui/tk/                   # UI 层：唯一界面（Tkinter）
│   ├── app.py               #   TkUi：post 队列泵（跨线程唯一通道）、主题接线
│   ├── main_window.py       #   主窗口（全控件方法挂 @ui_thread 守卫）
│   ├── mask_window.py       #   蒙版翻译（抓屏投递回主线程）
│   ├── project_bar.py / dialogs_*.py / chat_panel.py / color_picker.py ...
├── capsules/                # 场景胶囊（自动发现）
└── version.py               # 版本单一真相源
tests/                       # 32 文件 212 用例
plugins/capsules/            # 第三方胶囊放置处
DOC/ui_demo/                 # UI 原型 HTML（非运行代码）
```

依赖方向铁律：`UI → core → services`，services 之间不互相 import，跨层通知走事件总线。

## 4. 架构关键决策（长期有效）

1. **组合根依赖注入**：`core/app.py` 发现并构造全部服务，UI 与服务只依赖抽象，不手工 new。
2. **六轴插件注册表**：`services/{capture,ocr,translate,ai,attach,persistence}/` 丢一个继承对应基类的 `.py` 即自动发现注册，核心零改动；`plugins/capsules/` 同理。
3. **单一 TOML 配置**：功能视角分节，每个功能页持有自己的连接参数；加载时 schema 强制纠偏 + 旧字段自动迁移 + projects 一致性保护。
4. **事件总线**：发布/订阅解耦；派发用订阅者快照，单个回调异常不影响他人。
5. **跨线程 UI 铁律**：非主线程**不得**碰任何 Tk 调用（`win.after` 也是 Tcl 调用）；一律经 `TkUi.post()` 队列泵回主线程。
6. **推理并发铁律**：llama.cpp context 非线程安全，本地模型推理必须持**实例级锁**全程串行（用 `RLock` 防持锁内再入死锁）；ctranslate2（Argos）本身支持并发，无需加锁。
7. **单实例 + 退出兜底**：进程锁防多开；托盘退出有 2 秒强杀兜底，防止残进程。
8. **原子写**：历史/配置等 JSON 一律「写临时文件 → os.replace」，进程被杀不出半截文件。
9. **离线优先**：`argos` 是断网最后保障；禁止静默联网下载 OCR 模型权重（下载必须走显式脚本）。

## 5. 功能与热键（当前全集）

| 热键 | 功能 |
|------|------|
| `Ctrl+Shift+A` | 框选截图 → 识别 + 翻译 |
| `Ctrl+Shift+C` | 识别剪贴板图片/文字 |
| `Ctrl+Shift+D` | 划词翻译（小贴条含「自动/译中/译英」三态方向按钮） |
| `Ctrl+Shift+E` | 循环切换翻译引擎 |
| `Ctrl+Shift+R` | 朗读当前译文/原文（TTS 双引擎降级） |
| `Ctrl+Shift+K` / `I` | 打开知识库 / 导入知识库（JSON·CSV，自动生成模板） |
| `Ctrl+Shift+X` | 取消当前任务 |
| `Ctrl+Shift+Q` | 退出程序（全局闸门，热键可录制覆盖，清空恢复默认） |

功能面：蒙版翻译（译文原地覆盖原文）、贴纸钉图对照、AI 对话面板（流式输出、附件、gen 代际守卫）、
多项目标签页（历史/知识库/AI 对话/译向按项目隔离，「管理项目」重开/改名/设译向/彻底删除需输入项目名确认，
默认项目不可删）、历史记录（检索/详情/复制/导出 MD·TXT/**清空前自动备份 history.json.bak**，
上限 500 条滚动）、知识库（SQLite FTS5 全文检索 + LIKE 兜底）、主题深浅色（含屏幕取色器）、
托盘常驻（Esc 仅隐藏）。主窗口「清场」按钮只清显示区，不碰持久化数据。

## 6. 测试

```cmd
.venv\Scripts\python.exe -m pytest tests/ -q      # 32 文件 212 用例
```

- 布局：`tests/` 按轴分文件（config / projects / json_history / knowledge / ocr / translate / chat…）
- 关键约定：**mock 签名必须与真实函数同步**（见 §12 P-01）；UI 逻辑测试不启动真实 Tk，
  用 post 队列的可注入替身；每修一个 bug 先补「冒烟测试」再改代码（见 §11）。
- 全量回归底线：`python -m compileall winocr` 必须零错误。

## 7. 从零复现指南（AI 一键重建路径）

按顺序执行，每步有明确验收点：

1. **骨架**：建 `winocr/` 包 + `main.py` 多命令入口（argparse 子命令：GUI 默认 / console / doctor / models / config / ocr）。
2. **配置系统**：`core/config.py` 定义 dataclass 体系 + TOML 读写；必须实现 schema 纠偏（类型错自动纠正）、旧字段迁移（保留向后兼容）、未知段忽略。
3. **组合根**：`core/app.py` 扫描 `services/*/` 与 `plugins/`，按基类自动发现插件，按依赖序构造并注入 `app.services` 字典。
4. **六轴服务**：按 §3 目录逐轴实现；每个引擎一个文件、一个基类契约（如 `OcrEngine.recognize(image) -> list[str]`）。
5. **事件总线 + 管线**：`pipeline.py` 串「捕获 → OCR → 翻译 → 记录」全流程，各步骤之间事件通知。
6. **UI 层**：`ui/tk/app.py` 实现 `TkUi.post()` 队列泵；**任何后台线程回写控件必须走 post**；主窗口、蒙版、托盘、对话框按需加。
7. **持久化**：json_history（原子写 + 500 上限 + 清空备份）与 knowledge（SQLite WAL + FTS5 虚表，删除同步索引）。
8. **崩溃捕获与自检**：`crash_handler` 接管三处异常 + faulthandler；`doctor` 子命令输出各插件可用性。

验收：`python main.py doctor` 全绿 → `run.bat` 起界面 → `Ctrl+Shift+A` 截图出译文 → `pytest tests/ -q` 全过。

模型资源：`models/v6_tiny`（默认，7MB）、medium（133MB，智能升档）从 ModelScope 下载（带 SHA256 校验）；
small 随 pip 包自带；Argos 中英包约 140MB 放 `vendor/argos_packages/`。**这些大文件不进 git 仓库**。

## 8. 开发规范

### 8.1 修复节奏（五步，必须遵守）

1. **定位根因**：先拿到证据（日志/复现/堆栈），不猜。
2. **先补冒烟测试**：在 tests/ 写一个能稳定复现该 bug 的用例（红）。
3. **最小修复**：只改必须改的；同时扫全局同类问题一并统一（如所有 `threading.Lock()` 伪锁）。
4. **红变绿**：新用例过、全量回归不破。
5. **更新文档**：CHANGELOG 记条目（根因/修复/影响/验证），新教训沉淀进 §12。

### 8.2 AI 辅助开发规范（要点）

- **上下文优先**：动代码前先读相关文件与本文档；禁止凭想象改接口。
- **任务单一**：一次对话聚焦一个可验证的交付物；大任务先拆步骤。
- **可运行交付**：交付 = 改完 + 编译过 + 相关测试过，三者缺一不算完成。
- **最小变更**：不顺手重构、不加没要求的功能、不为未来设计；删代码就删干净（不留 `// removed` 残迹）。
- **无幻觉**：不确定的 API/字段先查代码或文档再写；mock 与真实签名逐字一致。
- **调试铁律**：先写复现，再二分定位；禁止盲改重试碰运气。

## 9. 踩坑精华（历史实战教训，仍然全部适用）

| 编号 | 坑 | 预防规则 |
|------|----|----------|
| P-01 | mock 函数签名与真实函数不同步，测试全绿但线上崩 | 改签名必须全局搜 mock/替身同步；测试断言参数而非仅返回值 |
| P-02 | Tkinter `command=self.method(...)` 误加括号——启动即调用而非回调 | 回调一律传引用或 `lambda:`；code review 专查 `command=` |
| P-03 | 后台线程直接碰 Tk 控件/`after` → 偶发卡死、崩溃 | 跨线程一律 `TkUi.post()`；`after` 也是 Tcl 调用，同样只能在主线程调 |
| P-04 | llama.cpp context 并发调用 → 原生 access violation，进程秒死，Python 拦不住 | 本地推理全程持实例锁；锁要可重入（RLock）；锁内不得回调会再次取锁的代码 |
| P-05 | 配置缓存只清一半（清了路径缓存没清已构建引擎）→ 切档位静默不生效 | 缓存失效要成对设计：键（签名）+ 值（实例）一起换；配置变更后做「重建冒烟」 |
| P-06 | `ctypes` 子模块隐式依赖（用了 `ctypes.wintypes` 只 import ctypes） | 用到子模块就显式 import；依赖别的库恰好导过 ≠ 依赖成立 |
| P-07 | 破坏性操作无确认无备份（清空历史直接覆写） | 删除/清空类操作：确认框 `default="no"` + 自动备份（.bak）双保险 |
| P-08 | `overrideredirect(True)` 后设 `-fullscreen` 在 Windows 必抛 TclError | 无边框全屏用 `geometry("WxH+0+0")`，不要混用两套机制 |
| P-09 | crash 日志文件名精度到秒，同秒多线程崩溃互相覆盖 | 冲突自动追加序号；转储内容含 traceback + 线程列表 |
| P-10 | 划词注入 Ctrl+C 时热键修饰键（Ctrl/Shift）还物理按着 → 组合成 Ctrl+Shift+C，目标软件不复制（Chrome 弹 DevTools） | 注入前 `GetAsyncKeyState` 检测并 KEYUP 释放修饰键 |
| P-11 | 剪贴板轮询取词误读旧内容（假成功/取到旧词） | 备份后先 EmptyClipboard 清空，只认本次新写入；清空失败退化旧行为 |
| P-12 | keyboard 库对 OS 长按自动重复再次触发回调 → 双贴条 + 双 worker 互抢剪贴板 | 热键回调加去抖窗口（划词 800ms） |
| P-13 | ctypes 未开 `use_last_error`，LastError 被内部调用覆盖 → 单实例互斥漏判程序双开 | WinAPI 轮询错误一律 `WinDLL(..., use_last_error=True)` + `get_last_error()`；句柄 restype 显式声明 |

> 新踩的坑按「编号 / 现象 / 根因 / 预防规则」格式追加到此表，不另开文件。

## 10. 历史决策存档（已实现，留结论）

- **项目栏与数据隔离**（原蓝图文档）：关闭标签仅隐藏（数据保留）、彻底删除需输入项目名强确认、
  历史/知识库/AI 对话/译向全部按项目隔离、知识库导入可选目标项目——已全部落地于
  `core/projects.py` + `ui/tk/project_bar.py`。
- **迭代计划 P0-P2**（原迭代计划文档）：P0 可用内核、P1 热键+托盘+导出、P2 项目隔离+知识库——均已交付，
  后续规划不再以文档维护，见 CHANGELOG 演进。
- **GUI 技术路线**（作者定论）：以 3.4 TK 为唯一基线；WebView2 已试验失败、PySide6 未完成迁移；
  不把 winOCR 引擎化。

## 11. 目录速查与文档入口

- 使用说明 / 截图 / 热键表 → [`../README.md`](../README.md)
- 版本变更明细 → [`../CHANGELOG.md`](../CHANGELOG.md)
- UI 原型 → `DOC/ui_demo/*.html`
- 本文件为唯一技术文档；新增技术内容直接扩充对应章节，不再拆分新文件。
