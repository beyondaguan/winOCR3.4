# WinOCR 3.4 项目一键 AI 从零复现指南

> 版本：v1.0 · 2026-08-28
> 对应代码版本：**3.4.19**（146 项测试全过）
> 项目路径：`D:\Documents\winOCR3.4-master`
> 定位：一份面向 AI 编程助手的「从空目录到可运行」全流程复现手册，覆盖架构理解、环境搭建、代码构建、模型获取、测试验证与打包分发。

---

## 目录

- [0. 项目是什么](#0-项目是什么)
- [1. 技术栈与依赖全景](#1-技术栈与依赖全景)
- [2. 架构深度解析](#2-架构深度解析)
- [3. 从零复现：环境搭建（第 1 步）](#3-从零复现环境搭建第-1-步)
- [4. 从零复现：代码构建（第 2 步）](#4-从零复现代码构建第-2-步)
- [5. 从零复现：模型与数据获取（第 3 步）](#5-从零复现模型与数据获取第-3-步)
- [6. 从零复现：配置与启动（第 4 步）](#6-从零复现配置与启动第-4-步)
- [7. 从零复现：测试验证（第 5 步）](#7-从零复现测试验证第-5-步)
- [8. 核心代码逐文件解读](#8-核心代码逐文件解读)
- [9. 配置系统详解](#9-配置系统详解)
- [10. 插件扩展指南](#10-插件扩展指南)
- [11. 打包分发](#11-打包分发)
- [12. 常见坑与避雷清单](#12-常见坑与避雷清单)
- [13. 关键决策速查](#13-关键决策速查)
- [14. AI 协作规范](#14-ai-协作规范)

---

## 0. 项目是什么

WinOCR 是一个 **Windows 桌面工具**，核心能力是：**截图识字（OCR）→ 翻译 → AI 解读**。纯 Python + Tkinter，无框架，离线优先。

一句话定位：专业文本工作流工具——不比广度比深度，押注「提取 → 几何重排 → 清洗 → 结构化 → TTS → 持久化 / 附件」这条脏活链路。

**30 秒架构速览**：`main.py` 只做参数解析 → `App().build()`（唯一接线处）→ `attach_ui(TkUi())` → `run()`。UI 与业务完全解耦：换界面只改 `attach_ui` 那一行。

---

## 1. 技术栈与依赖全景

### 1.1 运行环境

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 |
| Python | 3.10 ~ 3.14（推荐 3.10~3.13，3.13 已验证；本项目实测 3.12） |
| GUI | Tkinter（Python 标准库自带，零第三方 GUI 依赖） |
| 架构 | 六轴插件化 + 组合根依赖注入 + 事件总线 |
| 配置 | 单一 TOML 文件（`~/.winocr/config.toml` 或便携模式项目根 `config.toml`） |

### 1.2 依赖分层（requirements.txt）

依赖按「插件轴」分组，缺哪个只有那一轴不可用，程序照常启动：

```text
# 必需：截图 / 图像处理
Pillow>=9.0.0

# OCR 轴（rapidocr 插件）
numpy>=1.20.0
onnxruntime>=1.23.2          # PP-OCRv6 需要 ONNX IR version 10
rapidocr>=3.9.0

# 翻译轴 · argos 插件（完全离线，无 torch）
ctranslate2>=4.0
sentencepiece>=0.2.0

# 翻译轴 · 源语言检测
langid>=1.1.6

# 附件轴（AI 对话读文档）
PyMuPDF>=1.24.0              # PDF 全文提取
python-docx>=0.8.11          # Word
openpyxl>=3.0.0              # Excel

# 全局热键
keyboard>=0.13.5

# 划词取词：UIA TextPattern 直读
uiautomation>=2.0.0

# 朗读 TTS：edge-tts（在线；缺网自动降级系统语音）
edge-tts>=7.0.0

# 可选
tkinterdnd2>=0.4.0           # AI 对话面板拖放文件
pystray>=0.19.0              # 系统托盘（不装则静默跳过）
```

### 1.3 依赖不在 core 层

core 层（配置 / 事件总线 / 注册表 / 管线）只用标准库，零第三方依赖。所有第三方依赖只服务于某一个插件轴。验证方式：装完 core 后直接 `python main.py doctor`，缺什么一目了然。

---

## 2. 架构深度解析

### 2.1 六轴插件化架构

整个系统由六个独立的「插件轴」组成，每个轴有自己的基类契约和注册发现机制：

```text
┌─────────────────────────────────────────────────────────┐
│                    main.py（CLI 入口）                    │
│         参数解析 → App().build() → attach_ui → run()      │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│              App（组合根 / Composition Root）               │
│  唯一接线处：加载配置 → 发现插件 → 实例化注入 → 组装管线    │
│  ┌─────────────┐ ┌───────────┐ ┌───────────────────────┐ │
│  │ AppConfig   │ │ EventBus  │ │ PluginRegistry       │ │
│  │ (单一 TOML) │ │ (线程安全)│ │ (pkgutil 自动发现)    │ │
│  └─────────────┘ └───────────┘ └───────────────────────┘ │
└──────────────────────────┬──────────────────────────────┘
                           │
        ┌──────┬───────┬───┴───┬────────┬──────────┬────────┐
        ▼      ▼       ▼       ▼        ▼          ▼        ▼
    ┌──────┐┌──────┐┌──────┐┌──────┐┌──────────┐┌────────┐┌──────────┐
    │capture││ ocr  ││translate││  ai  ││  attach  ││persist-││  tts    │
    │  轴   ││  轴  ││   轴   ││  轴  ││   轴     ││ence 轴 ││ (独立)  │
    └──┬──┘└──┬──┘└──┬───┘└──┬──┘└────┬────┘└───┬──┘└────────┘
       │      │      │       │        │         │
       ▼      ▼      ▼       ▼        ▼         ▼
    screenshot rapidocr  argos    glm_chat   pdf       json_history
    clipboard  vision_ocr glm     openai_ch  docx      knowledge(sqlite3+FTS5)
    selection  structure  hunyuan  at         xlsx
    imagefile             mymemory            plaintext
```

六轴分别是：

1. **capture（捕获源）**：截图选区蒙层、剪贴板读取、图片文件加载、划词选区取词（UIA 直读 + 剪贴板兜底）。
2. **ocr（OCR 引擎）**：RapidOCR + PP-OCRv6 本地引擎（三档：tiny/small/medium）、结构化表格几何重建、云端视觉 OCR（OpenAI 兼容）。
3. **translate（翻译引擎）**：argos 离线神经翻译（无 torch）、glm 大模型翻译、hunyuan 翻译、mymemory 免费在线翻译，由调度器统一做回退链。
4. **ai（AI 对话）**：GLM Chat Provider、OpenAI 兼容 Provider，支持文本 + 视觉多模态。
5. **attach（附件解析）**：图片、PDF、Word、Excel、纯文本的提取与路由。
6. **persistence（持久化）**：JSON 历史记录（按项目隔离）、SQLite3 + FTS5 知识库（带全文检索）。

### 2.2 组合根（Composition Root）— `core/app.py`

`App.build()` 是全应用唯一的接线处，负责四件事：

1. 加载单一配置（`AppConfig.load()`）
2. 用注册表发现各轴插件（`PluginRegistry.discover()`）
3. 实例化并注入配置（依赖注入，全程没有模块级单例）
4. 组装 Pipeline / 事件总线 / UI 适配器

```python
# 核心装配流程（简化版）
def build(self) -> "App":
    # 1. 发现六轴插件
    self.discovered = {
        "ocr": self._discover("ocr", OcrEngine),
        "translate": self._discover("translate", TranslateEngine),
        "ai": self._discover("ai", AiProvider),
        "capture": self._discover("capture", CaptureSource),
        "attach": self._discover("attach", AttachParser),
        "persistence": self._discover("persistence", Persistence),
    }
    # 2. 实例化 + 注入配置
    translate_engines = {name: cls() for name, cls in ...}
    ocr_inst = self._make_ocr()
    ai_inst = self._make_ai()
    # 3. 组装 services 字典
    self.services = {"ocr": ocr_inst, "translate": dispatcher, ...}
    # 4. 组装 Pipeline + 项目管理器
    self.pipeline = Pipeline(self.bus, self.services, ...)
    self.projects = ProjectManager(self)
    return self
```

**设计要点**：此后任何扩展只需往 `services/<轴>/` 丢一个 `.py` 文件，`App.build()` 一行都不用改。运行期配置变更走 `app.apply_config()`，把配置重新注入所有已实例化服务并落盘——杜绝「改了配置没重载」。

### 2.3 事件总线 — `core/event_bus.py`

线程安全的发布/订阅模型，取代旧版的全局状态机（曾导致死锁）：

- 发布/订阅解耦：后台线程只管 `publish`，UI 只管 `subscribe`，双方互不持锁。
- 订阅者异常隔离：单个 handler 抛错不影响其他订阅者。
- 快照式派发：`publish` 时对订阅表取快照，允许 handler 内部再订阅/退订而不死锁。

预定义事件：`OCR_START/DONE`、`TRANSLATE_START/DONE`、`CHAT_START/DONE`、`ERROR`、`STATUS`、`CONFIG_CHANGED`、`WORKFLOW_DONE`、`KB_SAVE/RESULTS/SEARCH`、`MD_RESULT`、`STICKER_RESULT`。

### 2.4 管线 — `core/pipeline.py`

把所有步骤串成可组合、可单测的服务调用：

```text
Capture(截图/剪贴板/文件)
  → pipeline.ocr()        OcrEngine.recognize() → OcrResult
  → pipeline.translate()  TranslateDispatcher → TranslateResult
  → pipeline.chat()       AiProvider.chat() → str（可选，AI 解读）
  → pipeline.record()     json_history 持久化（原子写，上限 500 条）+ 知识库沉淀
```

异步铁律：长任务一律 `pipeline.run_async(fn, ..., on_done, on_error, cancel_event)` 丢后台线程；异常统一转事件（`Events.ERROR` + `Events.STATUS`），绝不静默吞掉；非主线程回写 UI 必须走 `TkUi.post()`。

### 2.5 插件注册表 — `core/registry.py`

通用插件注册表，泛型设计，任何基类都能用 `discover(base, package)`：

- 用 `pkgutil.iter_modules` 扫描（同时识别 `.py` 源码和 `.pyc` 打包形态）。
- 惰性导入重依赖：发现阶段只 `inspect` 类，不触发模块顶部的 `ctranslate2/rapidocr` 等 import。
- 多来源：支持内置包 + 外部插件目录（`plugins/`），未来可热插拔第三方插件。

### 2.6 跨线程 UI 队列泵 — `ui/tk/app.py`

**这是踩坑换来的核心机制，禁止撤销**：

- 后台线程只入队 `_ui_queue`，主线程泵 `_pump_ui` 在 `after(40)` 自续期排空队列。
- 泵一旦运行，后台线程绝不直接碰 Tk，从根本上消除跨线程 Tcl 竞争。
- 泵自愈心跳：`post()` 发现泵声称运行但 >1.5s 无执行 → 自动重新引导。

---

## 3. 从零复现：环境搭建（第 1 步）

### 3.1 准备 Python 环境

```cmd
:: 确认 Python 版本（需要 3.10~3.14，推荐 3.12）
python --version

:: 在项目根目录创建虚拟环境
cd D:\Documents\winOCR3.4-master
python -m venv .venv
```

### 3.2 安装依赖

```cmd
:: 激活虚拟环境并安装全部依赖
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> **依赖分层验证**：`Pillow` 是唯一必需项；`numpy/onnxruntime/rapidocr` 只服务 OCR 轴；`ctranslate2/sentencepiece` 只服务 argos 翻译轴。缺哪个只有那一轴不可用，程序照常启动。

### 3.3 一键安装（替代方案）

项目自带 `setup.bat`（GBK 编码 + CRLF），一键完成：

- 创建 `.venv`
- 安装依赖
- 自动下载 OCR 模型（tiny 与 medium 两档，约 140MB）
- 下载 Argos 中英互译离线包（约 140MB）
- 自检

```cmd
setup.bat
```

> `setup.bat` 检测到已有且校验通过的文件会自动跳过下载；个别大文件下载失败不中断，离线翻译自动降级可用。

---

## 4. 从零复现：代码构建（第 2 步）

### 4.1 项目目录结构（完整）

```text
winOCR3.4/
├── main.py                       # 多命令 CLI 入口（gui/console/doctor/models/config/ocr）
├── setup.bat                     # 一键安装（GBK+CRLF）
├── run.bat                       # 一键启动
├── requirements.txt              # 依赖清单
├── pyproject.toml                 # 打包/安装元信息
├── winocr/
│   ├── core/                     # 组合根层（零第三方依赖）
│   │   ├── app.py                #   App：唯一接线处（DI 容器 + apply_config 热重载）
│   │   ├── config.py             #   AppConfig：单一 TOML 配置模型（强 schema dataclass）
│   │   ├── event_bus.py          #   EventBus：线程安全发布/订阅 + 快照式派发
│   │   ├── pipeline.py            #   Pipeline：可组合管线编排 + run_async 异步包装
│   │   ├── registry.py            #   PluginRegistry：pkgutil 自动发现 + 惰性导入
│   │   ├── capsule.py             #   Capsule：场景胶囊发现器 + CapsuleContext
│   │   ├── projects.py            #   ProjectManager：项目注册表 + 跨服务重指
│   │   ├── types.py               #   共享数据类型（Capture/OcrResult/TranslateResult/...）
│   │   └── paths.py               #   路径解析（便携/常规双模式 + sys.frozen 分支）
│   ├── services/                 # 六轴插件实现
│   │   ├── capture/              #   捕获源：screenshot/clipboard/selection/imagefile
│   │   ├── ocr/                  #   OCR 引擎：rapidocr + structure + vision_ocr
│   │   ├── translate/            #   翻译引擎：argos/glm/hunyuan/mymemory + dispatcher
│   │   ├── ai/                   #   AI 对话：glm_chat + openai_chat
│   │   ├── openai_compatible/    #   统一 OpenAI 兼容 HTTP 客户端
│   │   ├── attach/               #   附件解析：pdf/office/image/plaintext
│   │   ├── persistence/          #   持久化：json_history + knowledge(sqlite3+FTS5)
│   │   ├── hotkey.py              #   全局热键服务（keyboard + Win32 RegisterHotKey 兜底）
│   │   ├── win32_hotkey.py        #   Win32 系统热键兜底
│   │   └── tts.py                 #   朗读 TTS（edge 在线 → SAPI5 离线，两级降级链）
│   ├── ui/                       # UI 适配器层
│   │   ├── base.py               #   UI 契约
│   │   ├── console.py            #   无界面模式
│   │   └── tk/                   #   Tkinter 单 UI（默认 GUI）
│   │       ├── app.py            #     UI 适配器实现（队列泵 + 热键接线 + 退出守卫）
│   │       ├── main_window.py    #     主窗口布局
│   │       ├── chat_panel.py     #     AI 对话面板
│   │       ├── dialogs.py         #     薄壳 re-export（已拆分 4 模块）
│   │       ├── dialogs_common.py #     工具函数 + 常量
│   │       ├── dialogs_settings.py #  热键 / API 设置 / 连接测试
│   │       ├── dialogs_data.py   #     知识库 / 历史 / 导入
│   │       ├── dialogs_misc.py   #     关于 / 插件 / 首次运行
│   │       ├── dialogs_hotkey.py #     热键设置
│   │       ├── dialogs_test.py   #     连接测试
│   │       ├── sticker.py        #     划词小贴条（方向选择 + 重译）
│   │       ├── project_bar.py    #     项目标签栏 + 合并窄带 + FlowFrame
│   │       ├── theme.py          #     多主题调色板
│   │       ├── style.py          #     ttk 样式配置
│   │       ├── color_picker.py   #     屏幕取色器
│   │       ├── tray.py           #     系统托盘（可选 pystray）
│   │       ├── exit_guard.py     #     进程退出守卫（venv shim 连根强杀）
│   │       ├── guards.py         #     @ui_thread 线程守卫
│   │       └── ui_inspector.py   #     界面区域定位器
│   └── version.py                # 版本号单一真相来源
├── models/                        # OCR 模型目录（GitHub 源码不含，需下载）
│   ├── v6_tiny/                   #   默认档位（约 7MB）
│   │   ├── PP-OCRv6_det_tiny.onnx
│   │   ├── PP-OCRv6_rec_tiny.onnx
│   │   └── ch_ppocr_mobile_v2.0_cls_mobile.onnx
│   └── v6_medium/                #   可选档位（约 133MB，智能升档用）
│       ├── PP-OCRv6_det_medium.onnx
│       ├── PP-OCRv6_rec_medium.onnx
│       └── ch_ppocr_mobile_v2.0_cls_mobile.onnx
├── vendor/argos_packages/        # 离线翻译包目录（GitHub 源码不含）
│   ├── translate-en_zh-1_9/
│   │   ├── metadata.json
│   │   ├── sentencepiece.model
│   │   └── model/model.bin
│   └── translate-zh_en-1_9/
│       └── (同上结构)
├── plugins/capsules/             # 第三方场景胶囊（自动发现）
├── tools/                         # 辅助工具脚本
│   ├── download_ocr_model.py      #   下载 OCR 模型（tiny/small/medium）
│   ├── download_argos.py          #   下载 Argos 翻译包
│   ├── list_winocr_proc.py        #   查看进程树 + PYID 固定身份
│   ├── kill_winocr.py             #   强杀 WinOCR 进程
│   ├── restart_winocr.py          #   重启
│   ├── fix_bat_encoding.py        #   修复 .bat 编码
│   ├── install_tts_voices.py      #   安装 TTS 语音
│   └── dump_ocr_items.py          #   转储 OCR 识别项
├── packaging/                     # 打包配置
│   ├── build.bat                 #   PyInstaller 构建脚本
│   ├── winocr.spec                #   PyInstaller spec
│   └── WinOCR.ico                #   应用图标
├── tests/                         # 测试（146 项全过）
├── DOC/                           # 文档
│   ├── WinOCR3.4文档总览.md       #   唯一技术文档
│   ├── AI辅助开发通用规范_V1.7.md
│   ├── 小白如何整体把握代码.md
│   ├── 踩坑.md
│   └── 团队规范-修复节奏.md
├── CHANGELOG.md
├── README.md
├── run.bat
├── setup.bat
├── stop_winocr.bat
├── stop_winocr.py
├── _exit_diag.py
├── install_voices.bat
└── .gitignore
```

### 4.2 入口文件 `main.py`

`main.py` 只做三件事，一行业务逻辑都不写：

1. 初始化控制台编码（`_init_console`：真控制台走 `WriteConsoleW` 天生 Unicode 安全只调 `errors`；管道才对齐代码页）
2. 解析命令行参数（`argparse`：gui / console / doctor / models / config / ocr）
3. 组装 App + 挂 UI（`App().build()` → `attach_ui(TkUi())` → `start()`）

关键设计：

- **单实例锁**：`CreateMutexW` 命名互斥量，已有实例在跑则 `FindWindowW` 找到主窗口拉到前台并退出当前进程，避免多个进程同时响应全局热键。
- **崩溃留痕**：启动崩溃时把 traceback 写进 `~/.winocr/crash.log`，方便 `pythonw` 无控制台场景排查。

### 4.3 从零构建代码（如果你要手搓）

如果你要从一个空目录完全手写这个项目，核心文件创建顺序：

1. `winocr/version.py` — 版本号单一真相来源
2. `winocr/core/types.py` — 共享数据类型（Capture / OcrResult / TranslateResult / Attachment / ChatMessage / KnowledgeRecord）
3. `winocr/core/paths.py` — 路径解析（便携/常规双模式 + `sys.frozen` 分支）
4. `winocr/core/config.py` — 配置模型（`ConnectableConfig` → `OcrConfig/TranslateConfig/AiConfig/HotkeyConfig/UiConfig/TtsConfig/PluginConfig/ProjectConfig` → `AppConfig`）
5. `winocr/core/event_bus.py` — 事件总线（线程安全发布/订阅 + 快照式派发）
6. `winocr/core/registry.py` — 插件注册表（`pkgutil.iter_modules` 自动发现 + 惰性导入）
7. `winocr/core/pipeline.py` — 管线编排（OCR → 翻译 → AI → 持久化 + `run_async` 异步包装）
8. `winocr/core/capsule.py` — 场景胶囊发现器
9. `winocr/core/projects.py` — 项目管理器（注册表 + 跨服务重指）
10. `winocr/core/app.py` — 组合根（唯一接线处）
11. 各轴基类：`services/ocr/base.py`、`services/translate/base.py`、`services/ai/base.py`、`services/capture/base.py`、`services/attach/base.py`、`services/persistence/base.py`
12. 各轴实现：`services/ocr/rapidocr.py`、`services/translate/argos.py`、`services/ai/glm_chat.py` 等
13. UI 适配器：`ui/base.py` → `ui/tk/app.py` → `ui/tk/main_window.py` 等
14. `main.py` — CLI 入口

---

## 5. 从零复现：模型与数据获取（第 3 步）

### 5.1 OCR 模型

`models/` 目录因体积原因不进 GitHub 仓库，需按需下载：

| 档位 | 体积 | 用途 | 获取方式 |
|---|---|---|---|
| `tiny` | 约 7MB | 默认档位 | `python tools/download_ocr_model.py tiny` |
| `small` | 随包自带 | 零下载 | `pip install rapidocr` 装完即有 |
| `medium` | 约 133MB | 智能升档 | `python tools/download_ocr_model.py medium` |

```cmd
:: 下载默认档位模型（ModelScope 官方源，带 SHA256 校验）
.venv\Scripts\python.exe tools\download_ocr_model.py tiny

:: 下载可选高档位模型
.venv\Scripts\python.exe tools\download_ocr_model.py medium
```

> 下载后模型落位于 `models/v6_tiny/` 和 `models/v6_medium/`，文件命名为 `PP-OCRv6_det_{tier}.onnx` / `PP-OCRv6_rec_{tier}.onnx` / `ch_ppocr_mobile_v2.0_cls_mobile.onnx`。
>
> `RapidOcrEngine._find_models()` 按三级优先级查找：项目目录新命名 → rapidocr 包内自带 → 项目目录旧命名兼容。
>
> 模型体积校验：低于 100KB 的 `.onnx` 视为占位文件（仓库曾出现 72 字节占位 .onnx）。

### 5.2 Argos 离线翻译包

```cmd
:: 下载中英互译离线包（约 140MB）
.venv\Scripts\python.exe tools\download_argos.py
```

或手动下载 Argos Translate 官方语言包，解压后放入 `vendor/argos_packages/`。

搜索目录顺序（先找到先用）：
1. 环境变量 `ARGOS_PACKAGES_DIR`
2. 用户目录 `~/.winocr/models/argos`
3. 项目内 `vendor/argos_packages`
4. argos 官方默认位置 `~/.local/share/argos-translate/packages`

### 5.3 零下载快速体验

把 `config.toml` 的 `ocr.model_type` 设为 `small` 即可——small 模型随 rapidocr pip 包自带，`pip install -r requirements.txt` 装完就能识别图片，不用额外下载任何文件。

---

## 6. 从零复现：配置与启动（第 4 步）

### 6.1 自检

```cmd
:: 自检：看六轴插件哪些可用、缺什么依赖
.venv\Scripts\python.exe main.py doctor
```

doctor 会输出：Python 版本、用户目录、配置文件、历史记录路径、插件目录、六轴插件装载情况（`[+]` 可用 / `[-]` 缺依赖 / ` . ` 未实例化）、关键依赖检查、模型资源、翻译引擎列表、场景胶囊。

### 6.2 生成配置

```cmd
:: 生成默认配置文件
.venv\Scripts\python.exe main.py config --init
```

配置文件写入路径（优先级）：
- 便携模式：项目根 `config.toml`（存在即触发）
- 常规模式：`~/.winocr/config.toml`

### 6.3 启动

```cmd
:: 一键启动（推荐）
run.bat

:: 或手动启动
.venv\Scripts\python.exe main.py

:: 无界面模式
.venv\Scripts\python.exe main.py console

:: 命令行识别一张图
.venv\Scripts\python.exe main.py ocr 图片.png -t zh-CN
```

### 6.4 默认热键

| 热键 | 功能 |
|------|------|
| `Ctrl+Shift+A` | 框选截图并识别 + 翻译 |
| `Ctrl+Shift+C` | 识别剪贴板中的图片/文字 |
| `Ctrl+Shift+D` | 划词翻译（选中文字后按此键） |
| `Ctrl+Shift+E` | 循环切换翻译引擎 |
| `Ctrl+Shift+R` | 朗读当前译文/原文 |
| `Ctrl+Shift+K` | 打开知识库（浏览/检索/导入/导出） |
| `Ctrl+Shift+I` | 导入知识库（弹引导窗） |
| `Ctrl+Shift+X` | 取消当前任务（OCR/翻译） |
| `Ctrl+Shift+Q` | 退出整个程序（全局闸门，永久存在） |

`Esc` / 关闭按钮仅隐藏窗口，程序后台常驻。

---

## 7. 从零复现：测试验证（第 5 步）

### 7.1 运行全量测试

```cmd
.venv\Scripts\python.exe -m pytest tests/ -v --tb=short
```

当前 **146 项测试全过**。

### 7.2 测试覆盖矩阵

| 测试文件 | 覆盖范围 |
|---|---|
| `test_smoke.py` | 插件发现、管线冒烟 |
| `test_openai_compatible.py` | OpenAI 兼容客户端、AI 注入、配置往返 |
| `test_translate_llm_config.py` | 连接语义、旧配置迁移、翻译连接继承 |
| `test_translate_direction.py` | 语言方向检测（含 explicit 语义、langid 回落） |
| `test_structure_rebuild.py` | 结构化 OCR 几何重建表格 |
| `test_group_by_lines.py` | 分行/断行合并算法 |
| `test_console_encoding.py` | 控制台编码不变量（936/65001 矩阵） |
| `test_hotkey_gate.py` / `test_hotkey_win32.py` / `test_hotkey_override.py` | 退出闸门 / Win32 热键兜底 / 热键录制覆盖 |
| `test_cancel.py` / `test_selection_translate_busy.py` | 任务取消、划词 busy 互斥 |
| `test_ai_base_url.py` | AI Base URL 注入 |
| `test_knowledge.py` | 知识库存/查/导出/导入 |
| `test_gui_smoke.py` | Tk 气泡/附件冒烟（无头 Tk，不需显示） |
| `test_sapi_host.py` / `test_tts_fallback.py` / `test_tts_split.py` | TTS 常驻宿主 / 降级链 / 句切分 |
| `test_ui_thread_guard.py` / `test_logging_hygiene.py` | UI 线程守卫 / 日志卫生 |
| `test_selection_capture.py` | 划词取词链路 |
| `test_project_bar.py` | 项目标签栏构建 |
| `test_tray_integration.py` | pystray 真集成测试 |

### 7.3 测试数据隔离

`tests/conftest.py` 提供 session 级 autouse fixture `_isolated_winocr_home`，把 `WINOCR_HOME` 重定向到 pytest 临时目录。测试不再读写真实 `~/.winocr`，结果可重复、不污染数据。

### 7.4 改代码后必跑

```cmd
:: 1. 语法检查（改了几个 .py 就查几个）
.venv\Scripts\python.exe -c "import ast; ast.parse(open('xxx.py').read())"

:: 2. 跑相关测试 → 跑全量测试
.venv\Scripts\python.exe -m pytest tests/test_xxx.py -v --tb=short
.venv\Scripts\python.exe -m pytest tests/ -v --tb=short

:: 3. 跑 doctor
.venv\Scripts\python.exe main.py doctor
```

---

## 8. 核心代码逐文件解读

### 8.1 `winocr/core/app.py` — 组合根（唯一接线处）

**已修根基，禁止动**：`App.build()` 的 DI 容器 + `apply_config()` 热重载。

`apply_config()` 是运行期配置变更的核心——把当前 `self.config` 重新注入所有已实例化服务并落盘。AI 提供方切换（glm ↔ openai_compat）需要重建实例，已在 `apply_config` 内处理；其余改动改完即生效。

关键方法：

- `build()` — 装配：发现六轴 → 实例化 → 注入配置 → 组装 Pipeline + ProjectManager
- `_discover(axis, base)` — 发现某轴插件，应用插件黑名单过滤
- `_translate_llm_kwargs()` — 大模型翻译引擎注入参数（来自翻译功能自己的连接参数）
- `_make_ocr()` / `_make_ai()` — 按配置选定 OCR/AI 引擎并注入
- `apply_config(save=True)` — 配置热重载（重新注入所有服务 + 落盘 + 发 CONFIG_CHANGED 事件）
- `describe()` — 自检输出：六轴插件可用性
- `attach_ui(ui_adapter)` — 挂载 UI 适配器
- `start()` — 启动 UI 主循环

### 8.2 `winocr/core/config.py` — 单一配置模型

取代旧版四套配置机制并存（`config_local.py` 可执行配置是安全隐患）。3.0 起一个强 schema 的 `AppConfig`，从单一 TOML 加载，全程类型化。

核心设计：

- `ConnectableConfig` 基类：一个 OpenAI 兼容接口的完整参数（`base_url/api_key/text_model/vision_model/temperature/top_p/限流`）。`OcrConfig` / `TranslateConfig` / `AiConfig` 各自继承它，直接在自己页里持有，不再有 `[api.*]` 连接表。
- 视觉采样参数分离：`vision_temperature/vision_top_p/vision_max_output_tokens`：`<0` = 不发送（推理模型安全），`0` = 跟随文本侧，`>0` = 视觉专属值。
- 旧配置迁移：`_apply_legacy_migration()` 把 3.0 散落字段（`glm_api_key/cloud_*` 等）直接落进各功能配置自己的字段。只补空缺不覆盖新值、不自动改写原文件。
- 环境变量覆盖：`GLM_API_KEY`（覆盖 AI 密钥，免落盘）、`WINOCR_MODEL_TYPE`（覆盖 OCR 档位）。
- TOML 读写：Python 3.11+ 用 `tomllib`，<3.11 回退内置最小 TOML 解析器。
- `HotkeyConfig`：只留 `enabled + quit + overrides` 字典，默认组合键由胶囊声明。
- `ProjectConfig`：真·项目栏的一个项目（id / name / open / translate_target）。

两个坑（勿踩）：

1. `from __future__ import annotations` 下 dataclass 的 `f.type` 是字符串而非类型对象，判断字段类型必须用 `_coerce()` 辅助函数。
2. 数字字段用 `None` 标记「旧配置没写」，否则默认值会覆盖 TOML 里的新值。

### 8.3 `winocr/core/pipeline.py` — 管线编排

每一步都是独立、可单测的服务调用；长任务统一走 `run_async` 丢到后台线程。

关键方法：

- `ocr(capture)` — 调 OCR 引擎识别，发 OCR_START/DONE 事件
- `translate(text, target, explicit, note)` — 调翻译调度器，发 TRANSLATE_START/DONE 事件
- `chat(msg)` — 调 AI 提供方，发 CHAT_START/DONE 事件
- `chat_with_context(msg, anchor, recall)` — 增强对话：锚定当前捕获物 + 召回知识库记忆
- `extract_and_translate(capture, target, auto_translate)` — 组合动作：OCR →（可选）翻译 → 记历史
- `record(ocr_text, translate_text, extra)` — 写入持久化
- `save_knowledge(record)` / `search_knowledge(query)` — 知识库编程接口
- `run_async(fn, *args, on_done, on_error, cancel_event, **kwargs)` — 异步包装：后台线程执行，异常统一转事件

`run_async` 的取消语义：任务完成但已被用户取消 → 丢弃结果，不回调 `on_done`（避免界面回填过期内容），发 STATUS「已取消」。

### 8.4 `winocr/core/registry.py` — 插件注册表

关键改进（对比旧版 `engines/__init__.py`）：

- 泛型：任何基类都能用 `discover(base, package)`。
- 惰性导入：发现阶段只 inspect 类，不触发模块顶部的重依赖 import。
- 用 `pkgutil.iter_modules` 而非 `Path.iterdir()` + 后缀过滤：PyInstaller 打包后模块落盘为 `.pyc`，按后缀过滤会漏掉 → 插件列表为空。
- 多来源：内置包 + 外部插件目录（`_PLUGIN_DIRS`）。

### 8.5 `winocr/core/types.py` — 共享数据类型

所有轴通过这些数据类通信，形成显式的管线契约：换掉任何一个实现，只要仍然收发这些类型，其余部分不需要知道它变了。

- `Capture` — 一次捕获的原始输入（image / text / empty）
- `OcrResult` — OCR 结果（text / lines / boxes / line_boxes / line_items / engine / confidence）
- `TranslateResult` — 翻译结果（text / source_lang / target_lang / engine / elapsed）
- `Attachment` — AI 对话附件（image / pdf / doc / xlsx / text）
- `ChatMessage` — 对话消息（role / text / images / attachments_text / context_blocks）
- `KnowledgeRecord` — 知识库记录（带溯源字段：source_type / image_hash / scene / created_at）

### 8.6 `winocr/core/paths.py` — 路径解析

全应用唯一的「文件在哪」真相来源。支持两种部署形态：

- 便携模式：项目根存在 `config.toml` → 配置与数据都放项目内（U 盘可带走）
- 常规模式：配置放 `~/.winocr/`（跨版本升级不丢设置）

打包形态（PyInstaller onedir）：`sys.frozen=True` 时项目根 = exe 所在目录，`models/` / `vendor/` / `plugins/` / `config.toml` 全部与 exe 同级。

### 8.7 `winocr/core/projects.py` — 项目管理器

负责「项目注册表 + 当前项目」的全部增删改查与跨服务重指：

- `add(name, translate_target)` — 新建项目，重指 AI 对话 / JsonHistory / 译向
- `switch(pid)` — 切换项目，重指服务
- `close_tab(pid)` — 关闭标签 = 仅 `open=False`（数据全留），**× 仅关标签、数据保留**
- `reopen(pid)` — 重新打开已关闭项目
- `rename(pid, name)` — 改名
- `set_target(pid, target)` — 设译向覆盖
- `delete(pid)` — 真删（移除注册 + 删知识 + 删两个历史文件，default 不可删）
- `migrate_legacy_history()` — 启动时把旧全局历史复制进 default 项目

### 8.8 `winocr/services/ocr/rapidocr.py` — 本地 OCR 引擎

四个关键点（都是踩坑换来的，别改坏）：

1. **模型加载**：`_find_models()` 按优先级查找（目录新命名 → rapidocr 包内自带 → 旧命名兼容）；档位缺失自动回退 tiny（离线护栏：绝不联网下载）。
2. **体积校验**：`_MIN_VALID_MODEL_SIZE = 100KB`——仓库曾出现 72 字节占位 .onnx。
3. **参数构造**：rapidocr 3.x 的 `EngineType/OCRVersion/ModelType` 必须传枚举，传字符串被拒；模型路径通过 `Det.model_path / Rec.model_path / Cls.model_path` 注入，实现零联网。
4. **返回兼容**：3.x 返回 `RapidOCROutput`（有 `.txts/.boxes/.scores`），1.x 返回 `(result, elapse)` 元组——按属性探测自适应。

智能档位（P1-2）：`recognize()` 在 `auto_upgrade=True` 且置信度 `< upgrade_threshold`（默认 0.5）时，自动调 `_next_available_tier()` 找更高档，用 `_run_with_tier()` 临时升档重试一次，取置信度更高的结果；用完还原档位与引擎缓存，不污染后续识别。

`_group_by_lines()` 增强：自适应阈值（行高中位数 × 0.5）、median 抗离群（行心取 y 中位数）、断行合并（高度重叠 + 小间隙的识别断框拼回一行）。

`_preprocess_image()`：小图放大（<600px 放大 1.5~2 倍），区分「代码截图」（只锐化，保留高亮色）与「普通照片」（灰度 + 对比度 1.3 + 锐化）——代码截图灰度化会毁掉符号。

### 8.9 `winocr/services/translate/dispatcher.py` — 翻译调度器

调度器不认识任何具体引擎，新增引擎 = 丢一个 `.py` + 在配置的 `fallback_order` 里写个名字。

回退链（默认）：`argos → glm → hunyuan → mymemory`。auto 模式逐个尝试、首个成功即返回；用户显式选了单引擎则失败就报错，不静默回退。

离线优先（`offline_mode=True`）：auto 回退链只走 `online=False` 的引擎，绝不偷偷回退到在线引擎击穿离线承诺。

语言方向检测（`detect` 方法）：

- `explicit=False`（一键翻译/热键）：空文本/纯符号 → `unknown` 跳过；langid 判定中文 → 中译外，其余 → 外译中；langid 不可用回落字符占比法。
- `explicit=True`（用户点了「译中」「译英」）：只推断 source，绝不动 target——早期无条件纠正导致「中文原文点译中被偷偷改成译英」的 bug，已修。

### 8.10 `winocr/ui/tk/app.py` — UI 适配器（唯一业务接缝）

**已修根基，禁止动**：`post()` 队列泵 + 自愈心跳（并发根因解）。

关键机制：

- `post(callback, *args)` — 线程安全队列 + 主线程泵：后台线程只入队，泵在 `after(40)` 自续期排空队列。泵自愈心跳：声称运行但 >1.5s 无执行 → 自动重新引导。
- `_on_hotkey_selection` — 划词取词在独立工作线程执行（按键瞬间目标软件仍持有焦点，UIA 直读能读到最新选中文本）。
- `quit_app()` — 退出调用链：`tts.stop → tray.stop → app.shutdown → root.quit/destroy → _force_exit_venv_tree`，全程幂等（`_exiting` 守卫）。
- `_force_exit_venv_tree()` — 进程退出守卫：查父进程，若父是 `.venv` shim 且当前进程命令行含 `main.py`，对父进程树执行 `taskkill /F /T` 连根强杀。

---

## 9. 配置系统详解

### 9.1 配置文件结构

```toml
# WinOCR 3.0 配置文件（纯数据，程序不会执行它）

current_project = "default"

projects = [{id = "default", name = "默认", open = true, translate_target = ""}]

[ocr]
engine = "rapidocr"
preprocess = true
model_type = "tiny"
structured = false
auto_upgrade = true
upgrade_threshold = 0.5
base_url = ""
api_key = ""
text_model = ""
vision_model = ""
max_output_tokens = 2048
# ... ConnectableConfig 字段

[translate]
engine = "auto"
target = "en"
auto_translate = true
offline_mode = false
fallback_order = ["argos", "glm", "hunyuan", "mymemory"]
base_url = ""
api_key = ""
# ... ConnectableConfig 字段

[ai]
provider = "glm"
base_url = ""
api_key = ""
text_model = ""
vision_model = ""
# ... ConnectableConfig 字段

[hotkey]
enabled = true
quit = "ctrl+shift+q"
overrides = {}

[ui]
mode = "simple"
theme = "light"
font_size = 11
window_size = "820x600"
theme_colors = {}

[tts]
engine = "auto"
voice = "zh-CN-XiaoxiaoNeural"
rate = 0
volume = 0
auto_read = false

[plugin]
blacklist = []
```

### 9.2 功能视角（无平台账号层）

每个功能（AI 对话 / 大模型翻译 / 云端视觉 OCR）直接在自己页里持有完整的一套连接参数（`base_url / api_key / text_model / vision_model / 采样 / 限流`），不再有「平台账号」中间层，互不串 Key。

任意 OpenAI 兼容接口都可用：智谱官方、SiliconFlow、OneAPI、自建 vLLM 等。

### 9.3 运行期生效

`app.apply_config()` 把配置重新注入所有已实例化服务并落盘。AI 提供方切换（glm ↔ openai_compat）需重建实例（已在 `apply_config` 内处理）；其余改动改完即生效；插件黑名单需重启程序彻底生效。

---

## 10. 插件扩展指南

### 10.1 新增 OCR 引擎

在 `winocr/services/ocr/` 放一个 `.py`，继承 `OcrEngine` 并给出 `name`：

```python
from winocr.services.ocr.base import OcrEngine
from winocr.core.types import OcrResult

class MyOcr(OcrEngine):
    name = "my_ocr"
    display_name = "我的 OCR"

    def recognize(self, image) -> OcrResult:
        # 重依赖必须在方法内惰性导入
        import some_lib
        ...
        return OcrResult(text=..., engine=self.name)

    def available(self) -> bool:
        try:
            import some_lib
            return True
        except ImportError:
            return False
```

下一次 `python main.py doctor` 就能看到新引擎。

### 10.2 新增翻译引擎

在 `winocr/services/translate/` 放一个 `.py`，继承 `TranslateEngine`：

```python
from winocr.services.translate.base import TranslateEngine

class MyTranslate(TranslateEngine):
    name = "my_translate"
    display_name = "我的翻译"
    online = True

    def translate(self, text, source, target) -> str:
        ...

    def available(self) -> bool:
        ...

    def set_config(self, **kwargs) -> None:
        # 接收注入的 API Key 等
        ...
```

在配置 `[translate].fallback_order` 里加上 `"my_translate"` 即可参与回退链。

### 10.3 新增场景胶囊

在 `plugins/capsules/` 放一个 `.py`，继承 `Capsule`：

```python
from winocr.core.capsule import Capsule, CapsuleContext

class SnapTranslate(Capsule):
    name = "snap_translate"
    display_name = "截图并翻译"
    hotkey = "ctrl+shift+a"
    enabled = True
    ui_section = "actions"

    def run(self, ctx: CapsuleContext, pipeline) -> None:
        # 所有结果通过 pipeline 服务或事件总线落地
        # 长任务走 pipeline.run_async
        # 结束发 WORKFLOW_DONE 释放忙标志
        ...
```

### 10.4 约定

- 重依赖必须在方法内惰性导入（不能放模块顶部），否则「列出引擎」会强制装齐所有引擎的依赖。
- 胶囊只读 `CapsuleContext`，不碰 UI（经事件总线回传），异常安全，长任务走 `pipeline.run_async`。
- 插件黑名单：在配置 `[plugin].blacklist` 里按 `.name` 禁用任一插件（需重启生效）。

---

## 11. 打包分发

### 11.1 工具与形态

PyInstaller onedir 目录包 + `models/vendor/plugins` 与 exe 同级（数据外置、热插拔）。

### 11.2 构建

```cmd
cd packaging
build.bat
```

`build.bat` 生成 GUI/CLI 双 exe + 复制 `models/`、`plugins/`、`vendor/`、`packaging/WinOCR.ico`。

### 11.3 包内结构

```text
WinOCR/
├── WinOCR.exe            # GUI 主程序（双击启动）
├── WinOCR-cli.exe        # 命令行版（doctor / ocr / config）
├── _internal/            # Python 运行时 + 依赖（PyInstaller 自动生成）
├── models/               # OCR 模型（v6_tiny + v6_medium + cls）
├── plugins/              # 插件目录（放入 .py 即自动发现）
├── vendor/               # 离线翻译包目录（放入 argos_packages 即生效）
└── config.toml           # 便携配置（用户数据写在 exe 旁，不进 AppData）
```

### 11.4 分发决策

- 基础包带 `v6_tiny`(6.6M)；`v6_medium`(133M) 按需放入。
- argos 164M 不进默认包（放入 `vendor/argos_packages` 即生效）。
- 绿色 zip 解压即用；不做代码签名 / 安装器 / 自动更新。
- 裁剪 OpenCV 视频 FFmpeg 后端（省 ~29MB，WinOCR 不用视频读写）。

### 11.5 复现/归档打包约定

分发或归档时务必排除：`.venv/`（约 476MB 虚拟环境）、`.git/`、`.workbuddy/`、所有 `__pycache__/`、`dist/`。

保留：`winocr/`（源码）、`models/`、`vendor/`、`plugins/`、`packaging/`、`tests/`、`tools/`、各入口 `.bat` 与 `.py`、`requirements.txt`、`pyproject.toml`、`README.md`、`CHANGELOG.md`、`DOC/`。

---

## 12. 常见坑与避雷清单

### 12.1 架构与并发

| 坑 | 正确做法 |
|---|---|
| 跨线程直接 `root.after(0, ...)` 在 Tk 下不可靠 | 用 `TkUi.post()` 队列泵：后台线程只入队，主线程泵在 `after(40)` 排空 |
| 全局状态机死锁（旧版 `_chatting` 全局锁） | 事件总线 + 异步回调：发布/订阅解耦，双方互不持锁 |
| 接线散落各处 →「改了配置没重载」 | 组合根 `App.build()` 唯一接线处 + `apply_config()` 热重载 |
| 划词取词焦点被抢 | 独立工作线程、按键瞬间执行取词（目标软件仍持有焦点） |

### 12.2 Tkinter / UI

| 坑 | 正确做法 |
|---|---|
| Tkinter 只读可复制 Text | 不要 `state=DISABLED`（连选择都禁）；绑 `<KeyPress>` 且 `event.state & 0x4`（Ctrl）放行、方向键放行、其它 `return "break"`。不要绑 `<Key>` |
| Text 无 wraplength | 用外层 Frame + `fill=X + expand=True` 自适应 |
| Tk 拖放文件 | 原生 WM_DROPFILES 不工作；必须 `tkinterdnd2` 且根窗口是 `TkinterDnD.Tk()` |
| `command=` 前向引用 | 一律用 `command=lambda: func()` 延迟查找，不直接传引用 |
| Label 内嵌子部件压字 | 用 Frame 做容器（`Label` 里 `pack(side=RIGHT)` 子部件会抢占文字渲染区） |
| `FlowFrame` 整组换行 | 窄窗按钮被推出可视区时用 FlowFrame 按组流式换行 |

### 12.3 Python / 依赖

| 坑 | 正确做法 |
|---|---|
| `from __future__ import annotations` 下 dataclass `f.type` 是字符串 | 判断字段类型用 `_coerce()` 辅助函数，不要用 `f.type is str` |
| rapidocr 参数 | `EngineType/OCRVersion/ModelType` 必须传枚举；模型 onnx 低于 100KB 视为占位文件 |
| langid 回落 | 必须处理 `langid` 未安装/异常 → 返回 None 让调度器用字符占比法，绝不能因缺依赖而崩 |
| openpyxl | 3.1.5 `cell.fill = None` 抛 TypeError；`PatternFill(patternType=..., fgColor=...)` 必须关键字 |
| langid 日文假名 | 平/片假名预检：出现即按非中文处理，防止 langid 只分中英时把假名误并进中文 |

### 12.4 Windows / 进程

| 坑 | 正确做法 |
|---|---|
| 全局热键需管理员权限 | `keyboard` 失败时用 `services/win32_hotkey.py` 的 `RegisterHotKey` 兜底 |
| venv shim 退出残留 | `_force_exit_venv_tree()`：查父进程是 `.venv` shim 且命令行含 `main.py` → `taskkill /F /T` 连根强杀 |
| pystray 线程引用丢失 | patch `_run_detached` 自建 `daemon=True` 线程并存引用 + `quit_app` 末尾 `os._exit(0)` 兜底 |
| PID 动态不可当身份 | 用 PYID（`sha1(真实解释器路径)[:10]`），不依赖 PID |
| `.bat` 中文乱码 | 批处理统一 GBK 编码 + CRLF + `chcp 936`；或跑 `python tools\fix_bat_encoding.py` |
| 控制台编码 | 真控制台走 `WriteConsoleW` 只调 `errors`；管道才对齐 `GetConsoleOutputCP()` |

### 12.5 测试

| 坑 | 正确做法 |
|---|---|
| 改了被 mock 替换的方法签名 | 必须同步检查测试中所有 mock 函数的参数列表 |
| 新增 `open_xxx` 对话框 | 必须加到 `test_gui_smoke.py::test_dialogs_build` 的遍历列表 |
| 测试依赖用户 `config.toml` | 凡读 `app.services` 的功能，测试中必须显式注入 mock |
| `taskkill /T` 误杀 pytest | 判定收紧为「父进程是 .venv shim 且当前进程命令行含 main.py」 |
| YAML 冒号+空格 | `name: Install (minimal: core)` 的 `minimal:` 被当 mapping → 加引号或改写 |

---

## 13. 关键决策速查

| 决策 | 结论 |
|---|---|
| 架构形态 | 维持 Python + Tkinter，不换 Tauri/Go/Electron |
| 战略定位 | 专业文本工作流工具，不比广度比深度 |
| 离线兜底 TTS | SAPI5 保留（2026-08-21 拍板，推翻 08-15 移除决策） |
| GUI 演进 | 暂缓；WebView2 失败、PySide6 未做好；若再试先设计后选型 |
| 引擎化 | 不把 winOCR 封装为通用引擎 |
| 分发形态 | PyInstaller onedir 双 exe + 数据外置 + 绿色 zip |
| 项目关闭语义 | × 仅关标签、数据保留；真删走「管理项目」 |
| 项目数据隔离 | 知识库 / 翻译历史 / AI 对话 / 译向全部按项目隔离 |
| 知识库导入 | 导入 JSON 时可选目标项目；全局热键 `Ctrl+Shift+I` |
| 老知识库兼容 | v1.0.0 → 3.4.18 升级时自动迁移 schema（scenario → scene） |
| 主基线 | winOCR 3.4 TK 版是唯一基础，继续演进 |

---

## 14. AI 协作规范

本项目遵循《AI 辅助开发通用规范 V1.7》的核心原则：

### 14.1 核心原则

- **上下文优先**：每次会话先给背景、环境、目标。
- **任务单一**：AI 每次响应只聚焦一个明确问题。
- **可运行交付**：代码要能直接跑，包含必要 import 和异常处理，严禁伪代码。
- **不臆造**：未知就明确说明，不编造 API/数据。
- **成本意识**：优先使用精准、信息量大的单次提问。
- **输出节制**：直接给出代码块/命令/结构化结论，严禁在代码前添加解释性废话。
- **无副作用**：严禁添加演示用 `print`、暂停语句、示例代码调用。
- **失败归因**：连续两次未解决问题时，输出《失败归因清单》（已尝试方案 / 已排除原因 / 卡住的技术点），明确请求人类介入。

### 14.2 双向约束（约束 AI 与用户）

- 版本只动 `version.py`。
- 禁止撤销 `post` 泵。
- 后台线程禁碰 Tk。
- 取词改动必查 `selection.log`。
- 不新写裸 `except`。
- `core/app.py` 的 DI 容器 + `apply_config` 热重载禁止动。
- `dispatcher.py` 翻译回退链禁止动。
- `ui/tk/app.py` 的 `post()` 队列泵 + 自愈心跳禁止动。
- `clipboard.py` 的 64 位句柄修正禁止动。

### 14.3 同步检查清单（每次改完代码必跑）

改功能代码后：

1. 语法检查（改了几个 .py 就 `ast.parse` 几个）
2. grep 交叉验证（新函数名/新参数名在 `winocr/` 和 `tests/` 中搜索，确认调用点都同步）
3. 签名比对（如果改了被 monkeypatch 替换的方法签名，逐个检查测试中的 mock）
4. 前向引用检查（Tk 的 `command=` 一律用 lambda 包装）
5. 新增公开函数 → grep `tests/` 确认有无覆盖 → 没有就补
6. 新增依赖 `app.services` 的功能 → 测试中注入 mock service

交付前：

1. `.venv\Scripts\python.exe -m pytest tests/ -v --tb=short` → 全绿
2. `python main.py doctor` → exit 0
3. 无临时调试代码残留
4. 改动文件清单与实际 git diff 一致

---

## 附录：版本历史脉络

| 版本 | 核心变化 |
|---|---|
| 2.x | OCR 统一 RapidOCR、Argos 无 torch 实现、AI 对话雏形、热键设置；全局状态机易死锁 |
| 3.0.0（2026-08-11） | 六轴插件化 + 组合根 + 事件总线：根治死锁；单一 TOML 配置；UI 契约化；内置离线模型 |
| 3.1.0（2026-08-13） | API 连接抽象 + 功能引用连接名 + 旧配置自动迁移 + Alt 快捷键移除 + 单实例锁 + tkinterdnd2 拖放 |
| 3.4.0–3.4.11 | 功能视角重构、UI 收敛 Tk、回退链 edge→OneCore→SAPI5；划词并发修复：独立工作线程、即时弹窗、`post()` 队列泵 |
| 3.4.12 | P1 架构加固：抽取词服务 + 单测 + CI + 热键降级 + UI 守卫 + 任务取消 + AI 历史落地 |
| 3.4.13 | P2 日志收口 + 消除 6 处静默吞异常 |
| 3.4.14 | P2-11 TTS 常驻 `_SapiHost`；SAPI5 分叉待裁决 |
| 3.4.15 | SAPI5 保留裁决落地 + 降级链回归测试 7 项 |
| 3.4.16 | P3-15 分包：PyInstaller onedir 双 exe + 数据外置；registry 改 pkgutil 兼容打包 |
| 3.4.17（2026-08-22） | TK 改进蓝图 P0~P2 全做：知识库/langid/插件黑名单/OCR 智能升档/向导/多提供方/系统托盘 |
| 3.4.18（2026-08-24） | 真·项目栏 + 数据隔离 + 知识库导入 + 完整退出修复 + PYID 对账工具 + 测试数据隔离 + dialogs.py 拆分 |
| **3.4.19（2026-08-25）** | 工程化改进归档：22 处裸 print 收敛 logging / app.py 拆出 exit_guard.py+style.py / dialogs 再拆 / 配置注入收敛 apply_config / 修复 AI 页签连接编辑器 / GitHub 源码缺模型修复 |

---

> 本文档基于 WinOCR 3.4.19 代码库生成，覆盖架构理解、环境搭建、代码构建、模型获取、配置启动、测试验证、核心代码解读、插件扩展、打包分发、常见坑与决策速查。全量 146 项测试通过，`main.py doctor` 正常。
