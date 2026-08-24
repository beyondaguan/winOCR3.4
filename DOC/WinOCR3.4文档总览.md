# WinOCR 3.4 技术文档总览（唯一文档）

> 版本：v2.0 · 2026-08-23
> 本文是 `D:\winOCR3.4` 代码库**唯一**的技术与使用文档，整合了原散落的设计、计划、手册、复现指南等全部内容。
> 所有技术细节、使用说明、复现步骤、决策结论均内联于此，不引用其他文档。
> 代码版本锚点：**3.4.17**（TK 改进蓝图 P0~P2 全落地，2026-08-22）。
> **本次会话补完（未升版本号，2026-08-23）**：① 划词小贴图新增「自动/译中/译英」方向选择；② 新增历史记录浏览面板（列表/检索/详情/复制/导出/清空）。两项均复用既有 `json_history` 持久化与 `dialogs.py` 框架，无新增依赖。
> **本次会话修复（未升版本号，2026-08-24）**：托盘退出进程残留修复——patch pystray `_run_detached` 捕获线程引用并设 `daemon=True`，`quit_app` 末尾 `os._exit(0)` 兜底，`Ctrl+Shift+Q` 全局闸门永久注册。详见 CHANGELOG「3.4.17 补完三」。

---

## 0. 作者路线定论（当前决策，最高优先级）

1. **主基线 = winOCR 3.4 TK 版**：它是目前「最好的」形态，作为唯一基础继续演进；其他 GUI 形态（Portable / WebView2 / PySide6）仅作历史参照。
2. **GUI 演进暂缓**：有空再考虑，不紧急。
3. **WebView2 已失败、PySide6 未做好**：两者均不作可行方向，相关探索已证伪。
4. **若未来再试 GUI，先后顺序铁律**：**先定界面 / 完成界面设计，再决定技术形态**（设计先行、技术后定）。
5. **不把 winOCR 封装为通用引擎**：保持「应用级一体化」，规避引擎化带来的体积与复杂度失控。

> 以下所有章节均以此定论为约束：架构不换、GUI 暂不动、不做引擎化封装。

---

## 1. 项目是什么

Windows 桌面工具：**截图识字（OCR）→ 翻译 → AI 解读**。纯 Python + Tkinter，无框架。

| 项 | 值 |
|---|---|
| 语言/UI | Python 3.10+ / Tkinter（`winocr/ui/tk/`，可整体替换） |
| 架构 | 六轴插件化 + 组合根依赖注入 + 事件总线 |
| 配置 | 单一 TOML（`~/.winocr/config.toml` 或便携模式项目根 `config.toml`） |
| 核心依赖 | Pillow / rapidocr / onnxruntime / ctranslate2 / sentencepiece / langid（其余可选） |
| 离线能力 | 内置 OCR 模型（`models/v6_tiny` 约 7MB + `models/v6_medium` 约 133MB）+ Argos 中英互译包（`vendor/argos_packages`，约 164MB） |
| 测试 | pytest，**119 用例全过** |
| 入口 | `main.py`（gui / console / doctor / models / config / ocr 六个子命令） |

**30 秒架构速览**：`main.py` 只做参数解析 → `App().build()`（唯一接线处）→ `attach_ui(TkUi())` → `run()`。UI 与业务完全解耦：换界面只改 `attach_ui` 那一行。

---

## 2. 从零复现（30 分钟内跑起来）

```cmd
:: 1. 准备环境（Windows 10/11，Python 3.10~3.14；推荐 3.10~3.13，3.13 已验证）
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

:: 2. 自检：看六轴插件哪些可用、缺什么依赖
.venv\Scripts\python.exe main.py doctor

:: 3. 生成配置 + 启动
.venv\Scripts\python.exe main.py config --init
run.bat                        :: 或 .venv\Scripts\python.exe main.py
```

**依赖分层**：`Pillow` 唯一必需；`numpy/onnxruntime/rapidocr` 只服务 OCR 轴；`ctranslate2/sentencepiece` 只服务 argos 轴；`langid` 只服务翻译源语言检测。缺哪个只有那一轴不可用，程序照常启动。`doctor` 子命令专为验证这一点设计。

**模型文件已内置在仓库**，不需要联网下载：

| 资源 | 位置 | 内容 |
|---|---|---|
| OCR 模型 | `models/v6_tiny/` | PP-OCRv6 tiny 三个 onnx（det 1.8MB / rec 4.5MB / cls 0.6MB） |
| OCR 模型（可选） | `models/v6_medium/` | PP-OCRv6 medium 三件套，智能升档用 |
| Argos 翻译包 | `vendor/argos_packages/` | `translate-zh_en-1_9/`、`translate-en_zh-1_9/`（双向中英） |

> OCR 的 `v6_small` 档由 rapidocr pip 包**自带**，零下载即可用；`v6_medium` 由 `tools/download_ocr_model.py medium` 获取（ModelScope 官方仓库，带 SHA256 校验）。
> PP-OCRv6 需要 ONNX IR version 10 → `onnxruntime>=1.23.2`（requirements.txt 已注明）。

---

## 3. 架构与关键设计决策

| 决策 | 动机 | 关键文件 |
|---|---|---|
| 组合根依赖注入 | 早期接线散落各处 →「改了配置没重载」常见 bug；收敛到 `App.build()` 一处 | `core/app.py` |
| 插件注册表 + 惰性导入 | 加引擎=丢一个 `.py`；发现阶段不触发重依赖导入；打包后 `.pyc` 形态也兼容（pkgutil） | `core/registry.py` |
| 事件总线 | 早期全局状态机易死锁 → 总线 + 异步回调根治 | `core/event_bus.py` |
| 单一 TOML 配置 | 不再执行 `config_local.py`（可执行配置是安全隐患） | `core/config.py` |
| 连接抽象（3.4） | 消灭「同名不同义/同义不同名/留空魔法」三乱；各功能直接持有自己的连接参数 | `core/config.py` |
| 单实例锁 | 多开进程同时响应全局热键 → 窗口堆叠；命名 Mutex 拉起已有窗口 | `main.py::cmd_gui` |
| Alt 快捷键整体移除 | Windows 粘滞键/输入法让裸按字母误带 Alt 位 → 误触；全改按钮 | `ui/tk/main_window.py` |
| 控制台编码双轨 | 真控制台走 `WriteConsoleW` 天生 Unicode 安全只调 `errors`；管道才对齐代码页；`.bat` 必须 GBK+CRLF | `main.py::_init_console` |
| 跨线程 UI 队列泵 | 后台线程只入队、主线程泵自续期；根治「异步结果不刷新/图贴卡住」 | `ui/tk/app.py::post/_pump_ui` |
| 忙标志 + 看门狗 | `_busy` 超时（30s）强制解锁 + 取消令牌，防界面假死 | `ui/tk/app.py` |
| 取词独立工作线程 | 按键瞬间在独立线程取词，避免 keyboard 钩子死锁 + 焦点时序错乱 | `ui/tk/app.py::_on_hotkey_selection` |

**已修根基（禁止动）**：`core/app.py` 的 DI 容器 + `apply_config` 热重载；`dispatcher.py` 翻译回退链；`ui/tk/app.py` 的 `post()` 队列泵 + 自愈心跳（并发根因解）；`clipboard.py` 的 64 位句柄修正。

**双向约束（约束 AI 与用户）**：版本只动 `version.py`；禁止撤销 `post` 泵；后台线程禁碰 Tk；取词改动必查 `selection.log`；不新写裸 `except`。

---

## 4. OCR 引擎

### 4.1 引擎契约（新增引擎看这里）

`winocr/services/ocr/base.py`：

```python
class OcrEngine(ABC):
    name: str = ""          # 插件注册键，配置 [ocr].engine 用它选引擎
    display_name: str = ""
    offline: bool = True
    def recognize(self, image) -> OcrResult: ...   # 输入 PIL.Image
    def available(self) -> bool: ...                # doctor 自检用，不抛异常
    def configure(self, **kwargs) -> None: ...      # 组合根注入配置
    def warmup(self) -> bool: ...                   # 可选预热
```

新增引擎 = 在 `winocr/services/ocr/` 放一个 `.py`（继承 `OcrEngine` + 给 `name`），`App.build()` 通过 `PluginRegistry` 自动发现，**核心代码零改动**。重依赖必须**方法内惰性导入**（不能放模块顶部），否则「列出引擎」会强制装齐所有引擎的依赖。

### 4.2 本地引擎 rapidocr：实现细节

`winocr/services/ocr/rapidocr.py`，四个关键点（都是踩坑换来的，别改坏）：

| 点 | 实现 |
|---|---|
| 模型加载 | `_local_models()` 按 `self.model_type`（tiny/small/medium）查 `models/v6_{tier}/`；来源优先级：目录新命名 `PP-OCRv6_det_small.onnx` → **rapidocr 包内自带**（small 零下载）→ 旧命名 `PP-OCRv6_tiny_det_infer.onnx`（兼容现有 v6_tiny）；档位缺失**自动回退 tiny**（离线护栏：绝不联网下载） |
| 体积校验 | `_MIN_VALID_MODEL_SIZE = 100KB`——仓库曾出现 72 字节占位 .onnx，不挡掉会报难懂的 protobuf 错误 |
| 参数构造 | rapidocr 3.x 的 `EngineType/OCRVersion/ModelType` **必须传枚举**，传字符串被拒；`ModelType` 用**实际生效档位**（`_effective_tier()`）构造，本地模型路径通过 `Det.model_path / Rec.model_path / Cls.model_path` 注入，实现零联网 |
| 返回兼容 | 3.x 返回 `RapidOCROutput`（有 `.txts/.boxes/.scores`），1.x 返回 `(result, elapse)` 元组——按属性探测自适应，不靠版本号判断 |

识别后处理：`_group_by_lines()` 增强版 —— **自适应阈值**（行高中位数 × 0.5，大/小字截图都稳）、**median 抗离群**（行心取 y 中位数）、**断行合并**（高度重叠 + 小间隙的识别断框拼回一行），行内按 X 排序；`_preprocess_image()` 小图放大（<600px 放大 1.5~2 倍），并区分「代码截图」（只锐化，保留高亮色）与「普通照片」（灰度 + 对比度 1.3 + 锐化）——代码截图灰度化会毁掉符号。

### 4.3 智能档位（3.4.17 P1-2）

`rapidocr.py` 的 `recognize()` 在 `auto_upgrade=True` 且结果置信度 `< upgrade_threshold`（默认 0.5）时，自动调 `_next_available_tier()` 找一个本地有模型的更高档，用 `_run_with_tier()` 临时升档重试一次，取置信度更高的结果；用完还原档位与引擎缓存，不污染后续识别。`model_availability()` 给 UI 标注各档是否齐备。配置项：`[ocr].auto_upgrade`、`[ocr].upgrade_threshold`。

### 4.4 云端 OCR 引擎 vision_ocr

`winocr/services/ocr/vision_ocr.py` —— 把图片发给视觉大模型（OpenAI 兼容接口）识别。参数**从配置直接读取**，与注入共用唯一真相来源（每功能独立持有连接参数，不再有 `cloud_connection` 引用名）。`[ocr].api_key` / `[ocr].base_url` / `[ocr].vision_model` 留空 = 关闭云端 OCR（默认关闭，本地引擎为主）。UI「测试云端 OCR」按钮必须走解析函数取参，不能传空串。

---

## 5. 翻译引擎

### 5.1 架构：调度器 + 引擎表

`winocr/services/translate/`：

| 文件 | 职责 |
|---|---|
| `base.py` | `TranslateEngine` 契约：`translate(text, source, target)` / `available()` / `warmup()` / `set_config(**kw)` |
| `dispatcher.py` | `TranslateDispatcher`：持有引擎实例表 + `fallback_order` 回退链，调度器**不认识任何具体引擎** |
| `argos.py` | 离线神经翻译（ctranslate2，无 torch） |
| `glm.py` / `hunyuan.py` | 大模型翻译（OpenAI 兼容，走 `services/openai_compatible/client.py`） |
| `mymemory.py` | 免费在线翻译，仅标准库 urllib，无额外依赖 |

**回退链**（配置 `[translate].fallback_order`，默认）：`argos → glm → hunyuan → mymemory`。auto 模式逐个尝试、首个成功即返回；**用户显式选了单引擎则失败就报错，不静默回退**。

**离线优先**（`[translate].offline_mode=True`，3.4.17 已在设置页暴露）：auto 回退链只走 `online=False` 的引擎（argos / mymemory 标准库通道仍可用），绝不偷偷回退到在线引擎击穿离线承诺。

### 5.2 语言方向检测（含 langid 增强，3.4.17 P0-3）

`dispatcher.detect()` 逻辑：

- `explicit=False`（一键翻译/热键）：空文本 / 纯符号数字（无翻译意义）→ 源语言 `unknown`，上层直接返回原文不翻译；否则用 `langid` 判定：**中文 → 中译外，其余语言一律按英文源（外译中）**，只做中英互译；langid 不可用则回落「中文占比 > 0.5」判定。
- `explicit=True`（用户点了「译中」「译英」）：**只推断 source，绝不动 target**——早期无条件纠正导致「中文原文点译中被偷偷改成译英、看起来像按钮没反应」，已修。

`langid` 接入细节（`dispatcher.py`）：懒加载 `langid` 并 `set_languages(["zh","en"])`，只在中英之间选择；日文假名（平/片假名）预检：出现即按非中文处理，防止 langid 只分中英时把假名误并进中文；`langid` 未安装 / 异常时返回 `None`，调用方自动回落字符占比法——**绝不因缺依赖而崩**。依赖：`langid>=1.1.6`（已列入 requirements.txt 必需项）。

### 5.3 离线引擎 argos：无 torch 独立实现

`argos.py` 直接驱动 **CTranslate2 + SentencePiece**，不依赖 `argostranslate/stanza/spacy`，体积从 ~1.2GB 降到 ~200MB，推理参数与 argostranslate 1.11 对齐（保证输出逐字一致）：

```text
beam_size=4, length_penalty=0.2, batch=32(tokens), compute_type=auto, intra_threads=min(cpu,4)
句切分：中文按 。！？； 拉丁按 .!?; 后跟空白
```

**模型包结构**（兼容 argos 标准）：

```
vendor/argos_packages/translate-zh_en-1_9/
├── metadata.json            # {"from_code":"zh","to_code":"en",...} ← 识别语言方向
├── sentencepiece.model      # 分词器
├── model/
│   ├── model.bin            # CTranslate2 模型（约 82MB）
│   ├── shared_vocabulary.json
│   └── config.json
└── README.md / stanza/      # stanza 目录可忽略（本实现不用）
```

**搜索目录顺序**（`core/paths.py::argos_search_dirs`）：环境变量 `ARGOS_PACKAGES_DIR` → 用户目录 `~/.winocr/models/argos` → 项目 `vendor/argos_packages` → argos 官方默认位置。先找到先用。

来源：Argos Translate 官方语言包（`argosopentech`），下载 `translate-zh_en`、`translate-en_zh` 的 `.argosmodel` 包解压后放入，或装 `argos-translate` 后 `argospm install translate-zh_en`。仓库内置为 1.9。

### 5.4 大模型翻译 glm/hunyuan：OpenAI 兼容客户端

`services/openai_compatible/client.py` 是统一 HTTP 客户端（urllib，无 SDK 依赖），glm / hunyuan / vision_ocr / glm_chat / openai_compat 全部复用它。注入参数由 `app._translate_llm_kwargs()` 解析：**大模型翻译直接使用 `[translate]` 自己页里填的地址 / 密钥 / 模型 / 限流，与 AI 对话完全解耦**；混元等其它翻译引擎复用同一套 `[translate]` 凭证。

---

## 6. 数据流与管线

`winocr/core/pipeline.py` —— 把所有步骤串成可组合、可单测的服务调用：

```text
Capture(截图/剪贴板/文件)
  → pipeline.ocr()        OcrEngine.recognize() → OcrResult
  → pipeline.translate()  TranslateDispatcher → TranslateResult
  → pipeline.chat()       AiProvider.chat() → str（可选，AI 解读）
  → pipeline.record()     json_history 持久化（原子写，上限 500 条）+ 知识库沉淀
UI 调用 pipeline.extract_and_translate(capture) 一条链完成「OCR → 翻译 → 记历史」
```

**异步铁律**：长任务一律 `pipeline.run_async(fn, ..., on_done, on_error, cancel_event)` 丢后台线程；异常统一转事件（`Events.ERROR` + `Events.STATUS`），**绝不静默吞掉**；非主线程回写 UI 必须走 `TkUi.post()`。`Ctrl+Shift+X` 在长任务期间可中断（`cancel_event` 置位，结果被丢弃，立即释放 busy 锁）。

事件总线（`core/event_bus.py`）：线程安全、订阅返回取消函数、支持一次性订阅。事件表：`OCR_START/DONE`、`TRANSLATE_START/DONE`、`CHAT_START/DONE`、`ERROR`、`STATUS`、`CONFIG_CHANGED`。

---

## 7. 配置系统（3.4 功能视角 + 3.4.17 新增）

`winocr/core/core_config.py`（实际文件名以仓库为准：`core/config.py`）：

| 概念 | 说明 |
|---|---|
| `AppConfig` | dataclass 强 schema，七个 section（ocr/translate/ai/hotkey/ui/tts/plugin）；缺字段自动回默认，解析失败不崩 |
| `ConnectableConfig` | 一个 OpenAI 兼容接口的完整参数（base_url/api_key/text_model/vision_model/temperature/top_p/限流）。`OcrConfig` / `TranslateConfig` / `AiConfig` 各自**继承它**，直接在自己页里持有，不再有 `[api.*]` 连接表 |
| 功能引用 | 各功能用自己的字段（`[ai].api_key/...`、`[translate].api_key/...`、`[ocr].api_key/...`）直接引用，不再按连接名间接引用 |
| 视觉采样分离 | 视觉侧 `vision_temperature` / `vision_top_p` / `vision_max_output_tokens`：`<0` = 不发送（推理模型安全），`0` = 跟随文本侧，`>0` = 视觉专属值 |
| 旧配置迁移 | 3.0 的 `glm_api_key`/`cloud_*` 等散落字段加载时自动转成各功能自己的字段；**只补空缺不覆盖新值、不自动改写原文件** |
| 环境变量覆盖 | `GLM_API_KEY`（覆盖 AI 密钥，免落盘）、`WINOCR_MODEL_TYPE` |
| **3.4.17 新增** | `[ocr].structured`（几何重建表格/版面，离线零额外模型）、`[ocr].auto_upgrade` + `[ocr].upgrade_threshold`（智能升档）、`[translate].offline_mode`（离线优先）、`[ai].provider`（glm / openai_compat 切换）、`[plugin].blacklist`（按 `.name` 禁用插件） |

**运行期生效**：`app.apply_config()` 把配置重新注入所有已实例化服务并落盘——组合根是唯一接线处，杜绝「改了配置没重载」。AI 提供方切换（glm↔openai_compat）需**重建实例**，已在 `apply_config` 内处理；其余改动改完即生效，但插件黑名单需**重启程序**彻底生效。

**迁移实现的两个坑（勿踩）**：
- `from __future__ import annotations` 下 dataclass 的 `f.type` 是字符串 `'str'` 而非类型对象，`f.type is str` 恒 False → 判断字段类型必须用辅助函数 `_coerce()`。
- 数字字段用 `None` 标记「旧配置没写」，否则默认值会覆盖 TOML 里的新值。

---

## 8. 胶囊与插件机制

### 8.1 问题与目标

场景逻辑硬编码在 UI 层（`TkUi.do_snap` 等），加场景=改 3+ 文件、不可插拔/不可测。新增 Layer3 胶囊层，把场景从 UI 抽出。

### 8.2 场景胶囊（Capsule）

`Capsule` 子类 = 一个场景 = 一个 `.py`，声明 `name` / `display_name` / `hotkey` / `ui_actions` / `run`，丢进 `plugins/capsules/` 自动出现按钮 + 热键，`doctor` 可见。契约：
- `CapsuleContext` 只读 frozen；
- 不碰 UI（经事件总线回传）；
- 异常安全；
- 长任务走 `pipeline.run_async`；
- 结束发 `WORKFLOW_DONE` 释放忙标志。

### 8.3 引擎插件

`plugins/` 根目录丢 `.py` 定义对应基类子类即可（如 `OcrEngine`、`TranslateEngine`），复用同一 `PluginRegistry`。**扩展任意一轴：新建一个 `.py` 文件，继承对应基类，注册表自动发现，无需改 `App.build()`。**

示例（OCR 引擎）：
```python
from winocr.services.ocr.base import OcrEngine
from winocr.core.types import OcrResult

class MyOcr(OcrEngine):
    name = "my_ocr"
    display_name = "我的 OCR"
    def recognize(self, image): ...
    def available(self): ...
```
下一次 `python main.py doctor` 就能看到新引擎。

---

## 9. TTS 朗读与离线兜底

**回退链**：`speak → edge-tts(在线五星) → SAPI5(离线机械音兜底)`，统一走 Windows MCI 播放。

**SAPI5 裁决（★现行有效，2026-08-21 拍板）**：用户拍板**保留 SAPI5 为离线兜底**，推翻 08-15「移除」决策（其前提 Win11+OneCore 在本机 Win10 Home 不成立）。依据：离线唯一出声通道 + P2-11 常驻 `_SapiHost` 成本趋零 + UI/配置早已按保留实现。

**不做**：OneCore 探测插入、pywin32 直连、GB 级本地神经引擎。触发条件（升 Win11 / 换机装神经语音）满足后再升级为 edge→OneCore→sapi。

---

## 10. 知识库（数据资产闭环）

同一批文档反复回看与沉淀：OCR 原文、译文、AI 解读可存为一条**可溯源知识**（来源 / 原图哈希 / 场景 / 时间），本地 sqlite3 + FTS5 中文全文检索，完全离线。

- 面板（`ui/tk/dialogs.py::open_knowledge`）：浏览 / 全文检索（FTS5 trigram + LIKE 兜底）/ 详情 / 删除 / **导出 JSON·Markdown·CSV**（`services/persistence/knowledge.py::export`）/ **导入 JSON·CSV**（引导窗 `open_import_dialog`，可指定目标项目，空记录自动跳过）。
- **导入模板自动生成**：`ensure_import_templates()` 在 `~/.winocr/templates/` 生成 `知识库导入模板.csv` / `.json` / 字段说明 txt（幂等：已存在不覆盖）；CSV 用 utf-8-sig 编码，WPS/Excel 可直接打开编辑；`import_csv` 自动探测编码（utf-8-sig → utf-8 → gbk）。
- 主窗口「存入知识库」按钮打通写入（`main_window.py::save_to_knowledge`）。
- **按项目隔离（3.4.18）**：`knowledge` 表带 `project` 列，存 / 查 / 导出只看当前项目；「管理项目」里删除项目会连知识一并清掉；导入时可选择归入任意项目（默认当前）。
- **快捷键**：全局热键「打开知识库」默认 `Ctrl+Shift+K`（设置里可改录 / 清空）。
- AI 对话会自动召回知识库里相关的历史理解作为背景上下文（`pipeline.chat_with_context`）。
- 编程接口：`pipeline.save_knowledge(record)` / `pipeline.search_knowledge(query)`；批量导入 `KnowledgeBase.import_records(records, project=None)`。
- 数据文件：`~/.winocr/knowledge.db`（WAL 模式，写入不阻塞读取）。

---

## 11. 界面与 GUI 路线（已搁置）

**现状诊断**：按钮风格混搭、无圆角卡片、深色主题字段是摆设、状态栏无底色。原拟两方案：A 轻量（只改 `theme.py`+按钮，半天）/ B 全面（深色主题双配色 + 圆角 Canvas + 悬浮窗）。

**当前定论（见 §0）**：WebView2 已失败、PySide6 未做好，GUI 演进**暂缓**。若未来再试，**先定界面设计、后选技术形态**，且不把 winOCR 引擎化。本改造方案当前不实施，tkinter 仍是唯一 UI。

---

## 12. 打包分发（P3-15，已落地 3.4.16）

**工具**：PyInstaller（零改 `paths.py` 的 `sys.frozen` 分支）。**形态**：onedir 目录包 + `models/vendor/plugins` 与 exe 同级（数据外置、热插拔）。

**分发决策**：基础包带 `v6_tiny`(6.6M)；**argos 164M 不进默认包**（放入 `vendor/argos_packages` 即生效）；绿色 zip 解压即用；不做代码签名 / 安装器 / 自动更新。

**构建**：`packaging/build.bat` 生成 GUI/CLI 双 exe + 复制 `models/`、`plugins/`、`vendor/`、`packaging/WinOCR.ico`。数据外置：`models/`、`vendor/`、`plugins/` 与 exe 同级，`paths.py` 的 `sys.frozen` 分支专为此设计，**零代码改动**。

**验证**：`WinOCR.exe` 启 GUI 无黑窗；`WinOCR-cli.exe doctor` 各轴全绿；`ocr <图>` tiny 实测正确；全量 119 passed。

**包内结构**：
```
WinOCR/
├── WinOCR.exe            # GUI 主程序（双击启动）
├── WinOCR-cli.exe        # 命令行版（doctor / ocr / config）
├── _internal/            # Python 运行时 + 依赖（PyInstaller 自动生成）
├── models/               # OCR 模型（v6_tiny + v6_medium + cls）
├── plugins/              # 插件目录（放入 .py 即自动发现）
├── vendor/               # 离线翻译包目录（放入 argos_packages 即生效）
└── config.toml           # 便携配置（用户数据写在 exe 旁，不进 AppData）
```

**复现/归档打包约定**：分发或归档时**务必排除** `.venv/`（约 476MB 虚拟环境）、`.git/`、`.workbuddy/`、所有 `__pycache__/`、`dist/`。保留 `winocr/`（源码）、`models/`、`vendor/`、`plugins/`、`packaging/`、`tests/`、`tools/`、各入口 `.bat` 与 `.py`、`requirements.txt`、`pyproject.toml`、`README.md`、`CHANGELOG.md`、`DOC/`。

---

## 13. 3.4.17 TK 改进蓝图：已实现功能清单

执行 TK 改进蓝图，P0~P2 全部落地。逐项对应文件，便于验证或重建：

| 项 | 功能 | 关键文件 / 符号 |
|---|---|---|
| P0-1 | 设置页点亮隐藏字段：离线优先、结构化 OCR、窗口尺寸+「记住当前」、AI 提供方只读 | `ui/tk/dialogs.py`（设置各 tab）、`core/config.py` |
| P0-2 | 知识库面板：浏览/检索/详情/删除/**导出 JSON·MD·CSV**；主窗口「存入知识库」打通写入 | `ui/tk/dialogs.py::open_knowledge`、`services/persistence/knowledge.py::export`、`ui/tk/main_window.py::save_to_knowledge` |
| P0-3 | 源语言检测增强：langid 仅分中英 + 日文假名预检 + unknown 跳过 | `services/translate/dispatcher.py`（`_langid_engine` / `_langid_detect`） |
| P1-1 | 插件黑名单：按 `.name` 禁用任一轴插件 | `core/config.PluginConfig`、`core/app.py::_discover`、`ui/tk/dialogs.py::open_plugins` |
| P1-2 | OCR 智能升档：低置信度自动升档重试 | `services/ocr/rapidocr.py`（`recognize` / `_run_with_tier` / `_next_available_tier`） |
| P1-3 | 取色器克制：21 个配色角色折叠进「自定义配色（高级）」 | `ui/tk/dialogs.py` 外观页、`ui/tk/color_picker.py` |
| P2-1 | 首次运行向导：未配置 Key 时弹一次引导 | `ui/tk/dialogs.py::open_first_run`、`ui/tk/app.py::_should_show_first_run` |
| P2-2 | 设置搜索 + 多提供方：五 tab 关键词搜索；AI 提供方 glm/openai_compat 切换 | `ui/tk/dialogs.py::_do_search`、`services/ai/openai_chat.py` |
| P2-3 | 系统托盘（可选 pystray）：装了才有，没装静默跳过 | `ui/tk/tray.py`、`ui/tk/app.py` 启动/退出接线 |
| P3-1 | **划词小贴图方向可选**：贴图底部「自动 / 译中 / 译英」三态按钮，点选后按选中方向重译；划词翻译自动记入历史 | `ui/tk/sticker.py`（方向行 + `_retranslate`）、`ui/tk/app.py::translate_sticker` / `_translate_to_sticker(target=)` |
| P3-2 | **历史记录浏览面板**：主窗口信息栏「历史记录」按钮 → 列表（时间/原文/方向）/ 关键词检索 / 详情（原文+译文+场景）/ 复制原文·译文 / 导出 Markdown·TXT / 清空；数据来自 `json_history` 落盘的 `history.json` | `ui/tk/dialogs.py::open_history`、`ui/tk/main_window.py`（信息栏按钮）、`services/persistence/json_history.py` |

**新增依赖**：`langid>=1.1.6`（必需，翻译源语言检测）、`pystray>=0.19.0`（可选，托盘）。二者已写入 `requirements.txt` 与 `pyproject.toml`。P3-1 / P3-2 为本次会话补完，**不新增任何依赖**，复用既有 `json_history` 与 `dialogs.py` 框架。

---

## 13.1 3.4.18 项目栏 + 数据隔离 + 知识库导入（2026-08-24，未发版）

按 `DOC/WinOCR_项目栏与数据隔离_蓝图.md` 落地，不新增依赖。核心符号速查：

| 项 | 功能 | 关键文件 / 符号 |
|---|---|---|
| 项目注册表 | `ProjectConfig` + `AppConfig.projects/current_project`；TOML 里 `projects` 段**必须排首位**（否则被 `[section]` 头吸收）；default 项目强制存在 | `core/config.py`（`_coerce_projects` / `_asdict_deep` / `save`） |
| 项目路径 | 每项目独立 `history/{id}.json`、`chat_history/{id}.json` | `core/paths.py::project_history_path / project_chat_history_path` |
| 项目管理器 | add / switch / close_tab（仅 `open=False`，数据保留）/ reopen / rename / set_target / delete（真删）/ migrate_legacy_history / 切换重指 AI 对话·JsonHistory·译向 | `core/projects.py::ProjectManager` |
| 知识库隔离 | `knowledge` 表加 `project` 列（幂等 ALTER）；存/查/删按项目过滤；FTS 用 `JOIN knowledge_fts AND k.project=?` | `services/persistence/knowledge.py` |
| 知识库导入 | `import_records(records, project=None) -> (ok, skipped)`：字段白名单、空记录跳过、单事务主表+FTS、id 重建；探测 schema 动态构造 INSERT 列，**兼容老 schema（scenario/tags/note）**；`import_csv(path)` 自动探测编码 | `services/persistence/knowledge.py::import_records` / `import_csv` |
| 导入引导窗 | `open_import_dialog(window, status_callback?)`：目标项目下拉 + 模板目录展示 + 打开模板文件夹 + 选 JSON/CSV；`ensure_import_templates()` 幂等生成 CSV/JSON/说明模板；面板「导入」与全局热键「导入知识库」共用 | `ui/tk/dialogs.py::open_import_dialog` / `_run_import` / `ensure_import_templates`、`core/paths.py::import_templates_dir` |
| 老 schema 迁移 | 启动 `_init_db` 时幂等探测列；缺 `scene` 但有 `scenario` → `ALTER ADD scene` + `UPDATE 复制`；`tags`/`note` 缺则补；解决 v1.0.0 升级用户的 `table knowledge has no column named scene` | `services/persistence/knowledge.py::_migrate_legacy_columns` |
| 项目标签栏 | 顶部标签（默认/…/+），点标签切换、× 仅关标签（default 无 ×）；**Frame 容器**（Label 内嵌子部件会压字） | `ui/tk/project_bar.py::ProjectTabBar` |
| 合并窄带 | 替代 info+action 两栏：输入 / 系统 / 信息三组，**`FlowFrame` 整组换行**根治窄窗被遮盖 | `ui/tk/project_bar.py::FlowFrame`、`ui/tk/main_window.py::_build_merged_band` |
| 管理项目 | 列全部（含已关）：重开 / 改名 / 设译向 / 真删（default 不可删） | `ui/tk/project_bar.py::open_manage_projects` |
| 热键修复 | 录制统一写 `cfg.overrides[action]`（原误写 `setattr` 临时属性→不生效不落盘）；任意新键覆盖旧键、重启保留 | `ui/tk/dialogs.py::open_hotkey_settings`、`services/hotkey.py` |
| 热键新增 | 动作「打开知识库」`open_knowledge`（`Ctrl+Shift+K`）+ 动作「**导入知识库**」`import_knowledge`（`Ctrl+Shift+I`） | `services/hotkey.py::ACTIONS`、`ui/tk/app.py::_wire_hotkeys` / `_open_knowledge` / `_import_knowledge` |

---

## 14. 用户使用要点

### 14.1 安装与启动

```cmd
setup.bat          :: 创建 .venv、装依赖、自检
run.bat            :: 启动图形界面
```
或手动 `python -m venv .venv` → `pip install -r requirements.txt` → `main.py`。

### 14.2 默认热键

| 热键 | 功能 |
|------|------|
| `Ctrl+Shift+A` | 框选截图并识别 + 翻译 |
| `Ctrl+Shift+C` | 识别剪贴板中的图片/文字 |
| `Ctrl+Shift+T` | 翻译原文框当前内容 |
| `Ctrl+Shift+E` | 循环切换翻译引擎 |
| `Ctrl+Shift+D` | 划词翻译（选中文字后按此键 → UIA/剪贴板取词 → 小贴图） |
| `Ctrl+Shift+R` | 朗读当前译文/原文 |
| `Ctrl+Shift+K` | 打开知识库（3.4.18 新增；浏览 / 检索 / 导入 / 导出） |
| `Ctrl+Shift+I` | 导入知识库（3.4.18 追加；弹引导窗：选目标项目 + 选 JSON/CSV，比打开面板更直接） |
| `Ctrl+Shift+X` | 取消当前任务（OCR / 翻译） |
| `Ctrl+Shift+Q` | 退出整个程序（**全局闸门，永久存在**） |

> 全部热键（除 quit 闸门）在「热键设置」里可重新录制，**新录即覆盖旧键、重启保留**；清空某项恢复胶囊默认。

- **划词翻译**：任意软件里**选中文字 → 按 `Ctrl+Shift+D`**，翻译结果以小贴图弹出。取词按优先级：主窗口内选中 → UIA 直读系统选中（WPS / 浏览器兼容，不污染剪贴板）→ 剪贴板兜底（模拟 `Ctrl+C`，带完整备份/还原）→ 主窗口原文/译文。已取消「自动划词 / Alt+右键」钩子监听，只有这一个显式按键入口，不误弹、不抢右键。贴图底部带「**自动 / 译中 / 译英**」方向按钮，点选即按该方向重译（默认自动 = 跟随配置 target）；每次划词翻译自动写入历史记录。
- **`Esc` / 关闭按钮**：只隐藏当前窗口，程序继续后台常驻（托盘态），下次识别自动弹出。截图框选途中按 `Esc` 还会直接取消本次框选。
- **`Ctrl+Shift+Q`**：退出最后逃生口，是「全局闸门、永久存在」——即便关掉全局热键（`[hotkey] enabled = false`），它仍注册并退出整个程序；即便缺少 `keyboard` 库，只要主窗口有焦点，应用内兜底绑定也能触发退出。

### 14.3 核心功能

截图翻译、剪贴板提取、翻译切换、划词翻译、朗读、截图转 Markdown、知识库、AI 对话、**历史记录**、**项目栏**。主窗口信息栏「历史记录」按钮打开浏览面板：按时间倒序列出所有 OCR/划词记录（原文 + 方向），支持关键词检索、查看详情（原文/译文/场景/时间）、一键复制原文或译文、导出 Markdown·TXT、清空全部。**项目栏**（3.4.18）：顶部标签页新建 / 切换 / 关闭项目，知识库、翻译历史、AI 对话、译向按项目隔离；「管理项目」里重开 / 改名 / 设译向 / 彻底删除（默认项目不可删不可关，× 仅关标签、数据保留）。展开「AI 对话」面板后可直接对识别结果提问，或粘贴/拖拽图片、PDF、Word、Excel 让 AI 解读；有图走视觉模型、纯文本走文本模型，勾选「带上识别原文」可把最近一次 OCR 结果作为上下文。

### 14.4 设置

按**功能视角**组织：每个功能页里直接填**自己的一套**连接参数（Base URL / API Key / 文本模型 / 视觉模型 / 采样 / 限流），不再有「平台账号」中间层，互不串 Key、互不串地址。任意 OpenAI 兼容接口都可用（智谱官方、SiliconFlow、OneAPI、自建 vLLM 等）。可设置离线优先、结构化 OCR、配色主题、热键等。

### 14.5 FAQ

- **热键无效？** `keyboard` 全局热键需管理员权限；失败时窗口内按钮仍可直接操作。Alt+字母快捷键已整体移除（防误触）。
- **首次识别慢？** 后台自动预热 ONNX 模型，通常无感知；仍慢可换 `model_type = "tiny"`。
- **完全离线能用吗？** 能。OCR 模型已内置，翻译用 `argos` 引擎，无需网络。
- **API Key 安全吗？** 存本地 `config.toml` 明文；环境变量 `GLM_API_KEY` 可临时覆盖，免落盘。
- **中文原文先「译英」再点「译中」没反应？** 已修：用户显式点击时目标语言不会被偷偷改向；若原文已是目标语种，系统自动改翻译文区（回译）。
- **译文区为什么可编辑？** 让人当场改错字/机翻生硬处再复制，比「只读→粘到别处改」顺手；支持 `Ctrl+A`、右键、撤销。
- **双击 `.bat` 中文乱码？** 批处理统一 **GBK 编码 + CRLF + `chcp 936`**；编辑过请用编辑器另存 ANSI/GBK，或跑 `python tools\fix_bat_encoding.py` 一键修正。

### 14.6 命令行入口

```cmd
python main.py                  # 启动图形界面（默认）
python main.py console          # 无界面模式
python main.py doctor           # 自检：哪些插件可用、缺什么依赖
python main.py models           # 查看 OCR / Argos 模型资源
python main.py config --init    # 生成默认配置
python main.py ocr 图片.png -t zh-CN   # 命令行识别并翻译
```

---

## 15. 测试体系

```cmd
.venv\Scripts\python.exe -m pytest tests/ -q        # 119 用例全过
```

| 测试文件 | 覆盖 |
|---|---|
| `test_smoke.py` | 插件发现、管线冒烟 |
| `test_openai_compatible.py` | OpenAI 兼容客户端、AI 注入、配置往返 |
| `test_translate_llm_config.py` | 连接语义、旧配置迁移、翻译连接继承 |
| `test_translate_direction.py` | 语言方向检测（含 explicit 语义、langid 回落） |
| `test_structure_rebuild.py` | 结构化 OCR 几何重建表格 |
| `test_group_by_lines.py` | 分行/断行合并算法 |
| `test_console_encoding.py` | 控制台编码不变量（936/65001 矩阵） |
| `test_hotkey_gate.py` / `test_hotkey_win32.py` | 退出/Win32 热键兜底 |
| `test_cancel.py` / `test_selection_translate_busy.py` | 任务取消、划词 busy 互斥 |
| `test_ai_base_url.py` | AI Base URL 注入 |
| `test_knowledge.py` | 知识库存/查/导出 |
| `test_gui_smoke.py` | Tk 气泡/附件冒烟 |
| `test_sapi_host.py` / `test_tts_fallback.py` / `test_tts_split.py` | TTS 常驻宿主 / 降级链 / 句切分 |
| `test_ui_thread_guard.py` / `test_logging_hygiene.py` | UI 线程守卫 / 日志卫生 |
| `test_selection_capture.py` | 划词取词链路 |

**改代码后必跑**：相关测试 + `python main.py doctor`。测试里要显式 `os.environ.pop("GLM_API_KEY")` 防环境变量污染断言。GUI 改动额外跑 `test_gui_smoke.py`（无头 Tk 冒烟，不需显示）。

---

## 16. 常见坑速查（给 AI 编程的避雷清单）

| 坑 | 正确做法 |
|---|---|
| Tkinter 只读可复制 Text | 不要 `state=DISABLED`（连选择都禁）；绑 `<KeyPress>` 且 `event.state & 0x4`（Ctrl）放行、方向键放行、其它 `return "break"`。**不要绑 `<Key>`**（不区分 modifier，会拦 Ctrl+C） |
| Text 无 wraplength | 用外层 Frame + `fill=X + expand=True` 自适应；**不要固定 width + pack_propagate(False)** |
| Tk 拖放文件 | 原生 WM_DROPFILES 不工作；必须 `tkinterdnd2` 且根窗口是 `TkinterDnD.Tk()`（`ui/tk/app.py::run` 已兼容两者） |
| dataclass f.type | `from __future__ import annotations` 下是字符串，判断类型用 `_coerce()` |
| rapidocr 参数 | `EngineType/OCRVersion/ModelType` 必须传枚举；模型 onnx 低于 100KB 视为占位文件 |
| langid 回落 | 必须处理 `langid` 未安装/异常 → 返回 None 让调度器用字符占比法，绝不能因缺依赖而崩 |
| 硅基流动免费档 | POST 冷启动 60~97s 偶发（非配置错）；诊断三步：GET /v1/models → 直接 POST → `App().build()` 内部 `ai.chat()`；连接超时建议 120s |
| openpyxl | 3.1.5 `cell.fill = None` 抛 TypeError；`PatternFill(patternType=..., fgColor=...)` 必须关键字 |
| 全局热键 | `keyboard` 需管理员权限；失败时窗口内按钮仍可用；非管理员下 `services/win32_hotkey.py` 用 `RegisterHotKey` 兜底 |
| 托盘可选 | pystray 未装时 `tray.py` 静默跳过，程序照常隐藏窗口 + 全局热键常驻，不要因此报错 |
| 插件黑名单 | 改动需重启程序才完全生效（装配发生在 `App.build()`） |

---

## 17. 版本历史脉络

| 版本 | 核心变化 |
|---|---|
| 2.x | OCR 统一 RapidOCR、Argos 无 torch 实现、AI 对话雏形、热键设置；全局状态机易死锁 |
| 3.0.0（2026-08-11） | **六轴插件化 + 组合根 + 事件总线**：根治死锁；单一 TOML 配置；UI 契约化；内置离线模型；移除 Win7/pyperclip/config_local.py |
| 3.1.0（2026-08-13） | **API 连接抽象**：`[api.*]` 连接表 + 功能引用连接名 + 旧配置自动迁移 + 字段级继承；设置界面重写；Alt 快捷键移除；单实例锁；tkinterdnd2 拖放 |
| 3.4.0–3.4.11 | 功能视角重构（各功能自持连接参数）、UI 收敛 Tk、回退链 edge→OneCore→SAPI5；划词并发修复：独立工作线程、即时弹窗、`post()` 队列泵根治刷新、贴图回填、兜底取词 |
| 3.4.12 | P1 七项：取词服务抽取+单测/CI/热键降级/UI 守卫/任务取消/AI 历史落地 |
| 3.4.13 | P2 日志收口 + 消除 6 处静默吞异常 |
| 3.4.14 | P2-11 TTS 常驻 `_SapiHost`；SAPI5 分叉待裁决 |
| 3.4.15 | SAPI5 保留裁决落地 + 降级链回归测试 7 项 |
| 3.4.16 | P3-15 分包：PyInstaller onedir 双 exe + 数据外置；registry 改 pkgutil 兼容打包 |
| **3.4.17（2026-08-22）** | **TK 改进蓝图 P0~P2 全做**：点亮隐藏配置字段、知识库浏览/检索/导出、langid 源语言增强、插件黑名单、OCR 智能升档、取色器克制、首次运行向导、设置搜索、OpenAI 兼容提供方、系统托盘；文档体系整合为单一文档 |
| **3.4.18（2026-08-24，未发版）** | **真·项目栏 + 数据隔离**：ProjectConfig 注册表 + ProjectManager（增/切/关标签/重开/改名/设译向/真删）；知识库/历史/对话按项目隔离；合并窄带 + FlowFrame 整组换行根治窄窗按钮被遮；知识库 JSON 导入（可选目标项目）；**导入快捷键**`Ctrl+Shift+I`；**老 schema 兼容**（WinOCR-Portable-v1.0.0 → scenario/tags/note 自动迁移到 scene）；热键录制修复（写 overrides）+ 新增「打开知识库」`Ctrl+Shift+K` |

---

## 18. 关键决策速查

| 决策 | 结论 |
|---|---|
| 架构形态 | **维持 Python + Tkinter**，不换 Tauri/Go/Electron（OCR 速度换架构零收益；Argos 离线翻译是换架构死结） |
| 战略定位 | **专业文本工作流工具**，不比广度比深度（押注「提取→几何重排→清洗→结构化→TTS→持久化/附件」脏活） |
| 离线兜底 TTS | **SAPI5 保留**（2026-08-21 拍板，推翻 08-15 移除） |
| GUI 演进 | **暂缓**；WebView2 失败、PySide6 未做好；若再试先设计后选型 |
| 引擎化 | **不**把 winOCR 封装为通用引擎 |
| 分发形态 | PyInstaller onedir 双 exe + 数据外置 + 绿色 zip |
| 3.4 TK 改进 | P0–P2 已全部落地（知识库/langid/插件/OCR 升档/向导/多提供方/托盘） |
| 项目关闭语义 | **× 仅关标签、数据保留**；真删走「管理项目」（2026-08-24 拍板） |
| 项目数据隔离 | 知识库 / 翻译历史 / AI 对话 / 译向全部按项目隔离（MVP 范围扩大，2026-08-24 拍板） |
| 知识库导入 | 导入 JSON 时**可选目标项目**（默认当前）；配全局热键 `Ctrl+Shift+I`（比「打开知识库」更直接，2026-08-24 拍板） |
| 老知识库兼容 | v1.0.0 → 3.4.18 升级时自动迁移：缺 `scene` → `ALTER ADD` 并把 `scenario` 数据拷过去；`tags`/`note` 列兼容补齐；用户重启程序即生效（2026-08-24 修复） |

---

## 19. 项目结构

```
WinOCR3.4/
├── main.py                       # 多命令 CLI 入口
├── setup.bat                     # 一键安装（GBK+CRLF）
├── run.bat                       # 一键启动
├── requirements.txt              # 依赖
├── pyproject.toml                # 打包/安装元信息
├── winocr/
│   ├── core/                     # 组合根、事件总线、配置、管线、类型、路径、胶囊发现器
│   ├── services/                 # 六轴插件
│   │   ├── capture/
│   │   ├── ocr/                  # RapidOCR + 结构化表格重建 + 云端视觉 OCR
│   │   ├── translate/            # argos / glm / hunyuan / mymemory + 回退链调度
│   │   ├── ai/                   # OpenAI 兼容对话
│   │   ├── openai_compatible/    # 统一 OpenAI 兼容客户端（鉴权/重试/多模态）
│   │   ├── attach/
│   │   ├── persistence/          # JSON 历史 + sqlite3/FTS5 知识库
│   │   └── tts.py                # 朗读（edge 在线 → SAPI5 离线）
│   ├── ui/                       # UI 适配器
│   │   ├── base.py
│   │   ├── console.py            # 无界面演示
│   │   └── tk/                   # Tkinter 单 UI（默认 GUI）
│   │       ├── app.py            # UI 适配器实现（唯一业务接缝）
│   │       ├── main_window.py    # 主窗口
│   │       ├── chat_panel.py     # AI 对话面板
│   │       ├── dialogs.py        # 设置对话框（热键/API/关于/知识库/插件/向导/历史记录）
│   │       ├── sticker.py        # 划词小贴条（Ctrl+Shift+D 触发，含自动/译中/译英方向选择）
│   │       ├── region.py         # 截图选区蒙层
│   │       ├── theme.py          # 多主题调色板 + 字号 + 自定义覆盖
│   │       ├── ui_inspector.py   # 界面区域定位器
│   │       ├── color_picker.py   # 屏幕取色器
│   │       └── tray.py           # 系统托盘（可选 pystray）
│   └── version.py                # 版本号单一真相来源
├── models/v6_tiny/               # 内置 OCR 模型
├── models/v6_medium/             # 可选 OCR 模型（智能升档用）
├── vendor/argos_packages/        # 内置离线翻译包
├── plugins/capsules/             # 第三方场景胶囊（自动发现）
└── tests/                        # 冒烟测试（119 用例）
```
