# llama.cpp CPU 内核升级 — 官方多变体包替换说明

更新日期：2026-09-16
版本：3.4.31

## 一、背景：为什么内置本地大模型会慢

3.4.30 起，llama-cpp-python 的原生 DLL 从 conda-forge 国内镜像安装（
`llama.cpp-10588-cpu_mkl` 包）。该包按 conda-forge 的通用基线（x86-64 =
SSE2，2003 年指令集）编译，**不包含 AVX / AVX2 内核**——即使你的 CPU 明明
支持，也吃不到加速。

实测（0.5B Q4 模型、4 线程、i7-2600 Sandy Bridge）：

| 内核 | 速度 | 说明 |
|---|---|---|
| conda-forge 基线（SSE2） | **3.3 tok/s** | 一句话翻译要等 20 秒以上 |
| 官方多变体包（自动选中 sandybridge=AVX） | **18.1 tok/s** | 快 5.5 倍 |

整机越老收益越明显；AVX2 机器（Haswell 四代酷睿及以后）同样受益（选中
haswell 变体），AMD Zen4 机器选 zen4 变体，ARM64 笔记本选 armv8 变体。

## 二、原理：官方「多变体运行时分发」

上游 llama.cpp（b10588，与 conda-forge 包同一源码版本，C 接口符号完全
兼容）的官方 Windows 发布包采用 `GGML_BACKEND_DL` 构建模式：

- CPU 后端拆成 14 个按微架构命名的 DLL：`ggml-cpu-sandybridge.dll`、
  `ggml-cpu-haswell.dll`、`ggml-cpu-zen4.dll`、`ggml-cpu-sse42.dll` …
- 运行时由 `ggml_backend_load_all()` 按 CPU 实际指令集打分，自动挑选
  最优变体——和 Ollama 的 `ggml-cpu-sandybridge.dll` 是同一机制。

替换内容：`ggml.dll`、`ggml-base.dll`、`llama.dll`、`mtmd.dll`、
`libomp.dll` + 14 个 `ggml-cpu-*.dll` 变体；conda 的静态 `ggml-cpu.dll`
移入备份目录。Python 侧绑定（llama-cpp-python 0.3.35）零改动。

代码层配套（静态构建 / wheel 布局同样无害）：
`winocr/services/llama_backend.py` 的 `ensure_ggml_backends()` 在加载
模型前补调 `ggml_backend_load_all()`——多变体包必须显式枚举后端，
llama-cpp-python 按静态构建设计不会自己调。

## 三、影响面：谁需要，谁无关

| 场景 | 是否受益 |
|---|---|
| 用内置 llama_cpp 引擎跑本地大模型（翻译 / AI 对话） | ✅ 必要 |
| 日常翻译走 Ollama（OpenAI 兼容接口）或云端 API | 无关（不影响，llama_cpp 只是兜底链一环） |
| 纯 OCR / Argos 离线翻译 / 在线翻译 | 完全无关 |

## 四、使用方法

### 1) 自动（推荐）

`install_all.bat` 在第 4b 步自动执行（模型下载之后、自检之前）：

```
[4b/5] llama.cpp AVX speed-up package (official upstream, ~17MB)
       Auto-selects the native CPU kernel at runtime; non-fatal on failure.
```

- **下载源说明**：llama.cpp 官方发布包**没有第一方国内镜像站**
  （hf-mirror 只镜像 HuggingFace 模型，npmmirror / ModelScope 均不收
  GitHub release 二进制）。项目采用 ghproxy 家族国内代理链加速，
  2026-09 实测排序（快且稳者在前）：
  `gh-proxy.com → ghproxy.cn → ghfast.top → ghproxy.net → mirror.ghproxy.com → 直连`。
- **断点续传**：下载写入 `.part` 缓存文件，某一节点断流后，下一个节点
  从断点继续（HTTP Range 206），不再整包作废；下载完成后做 zip
  完整性校验（签名 + 中央目录 + CRC），损坏即弃并换节点重试。
- 缓存目录 `vendor/.cache/llama_cpp/`（重装免下载）。
- **失败不阻断安装**：仅打印警告，本地大模型退回慢速基线内核，
  OCR / 翻译 / UI 一切正常。

### 2) 手动

```bash
python tools/fix_llama_avx.py                 # 执行替换（默认动作）
python tools/fix_llama_avx.py --status        # 查看当前内核状态
python tools/fix_llama_avx.py --detect        # 检测 CPU 能力 + 实际选中的内核
python tools/fix_llama_avx.py --rollback      # 回滚到原始 conda 内核
python tools/fix_llama_avx.py --offline 包.zip  # 无网机器：用本地离线包
```

任意目录均可运行（脚本内部定位项目根）。重复执行幂等：已替换的机器
直接跳过。

`--detect` 输出示例（i7-2600）：

```
=== CPU detect ===
  machine       : AMD64
  OS support    : AVX=yes AVX2=no AVX512F=no
  expected pick : ggml-cpu-sandybridge
  registered    : 1 backend device(s)
    - CPU: Intel(R) Core(TM) i7-2600 CPU @ 3.40GHz
  installed     : 14 variant DLL(s) (official package applied)
```

「expected pick」按 OS 报告的指令集推导；「registered」是进程内真实
注册的设备（llama.cpp 真正用它算），两者一致即运行时选择正确。

### 3) 应用内一键切换（3.4.33+）

设置 → API 设置 →「大模型翻译」页 → 引擎选 llama_cpp 后，**本地模型**
下方出现「CPU 内核」下拉：**官方多变体 AVX（推荐）** / **MKL 基线（conda）**。

- 写入配置 `[translate].llama_kernel`（环境变量 `WINOCR_LLAMA_KERNEL=avx|mkl`
  优先），**下次启动本地模型时**由 `ensure_ggml_backends()` 在
  `import llama_cpp` 之前换装——DLL 入进程后文件被锁，不能热切换；
  应用运行中换装失败会静默保持现状。
- 双内核文件集在 `site-packages/llama_cpp/lib/kernel_sets/`（首跑从现场
  状态自举：avx 集来自激活态的变体目录，mkl 集优先取 `backup_baseline/`）。
  从未装过多变体包的机器 avx 集缺失，切换自动保持 MKL。
- 两内核共用 ggml.dll / llama.dll / mtmd.dll / libomp.dll（多变体包版本），
  仅 ggml-cpu.dll（+变体文件）随选择变化；MKL 模式会从 lib/ 移除全部
  `ggml-cpu-*.dll`，防止 `ggml_backend_load_all` 双注册 CPU 后端。

### 4) 不同情况的处理

| 机器情况 | 行为 |
|---|---|
| 从未替换（只有静态 ggml-cpu.dll） | 下载 → 备份 → 替换 → 冒烟检查 |
| 已替换过（存在 ggml-cpu-*.dll） | `[skip]` 秒退 |
| ARM64 Windows 笔记本 | 自动改用 `cpu-arm64.zip`（约 11MB） |
| llama-cpp-python 未安装 | `[skip] nothing to do`（不报错） |
| 代理全挂 / 无外网 | 提示改用 `--offline`；或保留慢速内核继续用 |
| 冒烟检查失败（backend 数 = 0） | **自动回滚**到备份，退出码 1 |

## 五、CPU 占用率硬限制（跑大模型不吃满 CPU）

### 应用内一键切换（3.4.33+）

设置 → API 设置 →「大模型翻译」页 → 引擎选 llama_cpp 后，**本地模型**
下方出现「CPU 内核」下拉：**官方多变体 AVX（推荐）** / **MKL 基线（conda）**。

- 写入配置 `[translate].llama_kernel`（环境变量 `WINOCR_LLAMA_KERNEL=avx|mkl`
  优先），**下次启动本地模型时**由 `ensure_ggml_backends()` 在
  `import llama_cpp` 之前换装——DLL 入进程后文件被锁，不能热切换；
  应用运行中换装失败会静默保持现状。
- 双内核文件集在 `site-packages/llama_cpp/lib/kernel_sets/`（首跑从现场
  状态自举：avx 集来自激活态的变体目录，mkl 集优先取 `backup_baseline/`）。
  从未装过多变体包的机器 avx 集缺失，切换自动保持 MKL。
- 两内核共用 ggml.dll / llama.dll / mtmd.dll / libomp.dll（多变体包版本），
  仅 ggml-cpu.dll（+变体文件）随选择变化；MKL 模式会从 lib/ 移除全部
  `ggml-cpu-*.dll`，防止 `ggml_backend_load_all` 双注册 CPU 后端。

### CPU 占用率硬限制

本地推理默认会用满所有核（任务管理器 100%），拖累系统其它操作。
3.4.32 起支持 Windows Job Object 硬性配额（500ms 调度窗口内强制生效，
不是降优先级的软手段）：

```toml
# config.toml
[translate]
cpu_limit = 70        # 进程 CPU ≤ 70%（0 = 不限制）

[ai]
cpu_limit = 70        # 本地 llama_cpp 对话同理
```

或用环境变量（优先级更高）：

```cmd
set WINOCR_CPU_LIMIT=70
```

- 取值范围自动钳制到 10~95；Ollama 是独立进程不受影响。
- 生效范围是本进程全部线程（llama.cpp 推理线程、ctranslate2 都算）。
- 实测（i7-2600，限 40%）：推理期间进程占用峰值 294%（4C8T 满载 = 800%
  刻度），从未越过 320% 理论上限；代价是翻译 1.2s/句 → 2.1s/句。
- 引擎加载模型时自动应用（translate / ai 的 llama_cpp 引擎均接入），
  配置读取优先级：环境变量 > config。
- Win7 等无 CpuRateControl 的系统自动静默降级（不限制，不影响运行）。

实现：`winocr/core/cpu_limit.py`（单测 `tests/test_cpu_limit.py` 用
QueryInformationJobObject 从内核读回配额验证）。

## 六、回滚

```bash
python tools/fix_llama_avx.py --rollback
```

恢复首次备份（conda 原始 DLL）并删除 14 个变体文件。备份目录：
`.venv/Library/bin_backup_baseline/`（仅在首次替换时创建，不会被后续
运行覆盖，保证任何时刻都能退回）。

手工方式：把该目录内所有文件复制回 `.venv/Library/bin/` 即可。

## 七、技术细节（踩坑记录）

1. **必须显式枚举后端**：`GGML_BACKEND_DL` 包不再静态内置 CPU 后端，
   llama-cpp-python 直接加载模型会报
   `no backends are loaded. hint: use ggml_backend_load()`。
   → `ensure_ggml_backends()` 补调。
2. **变体扫描路径只看「主程序目录 + 当前工作目录」**（见上游
   `ggml/src/ggml-backend-reg.cpp` 的 `ggml_backend_load_best`）。
   Python 进程的主程序目录是 `Scripts\`，永远扫不到
   `.venv/Library/bin/` 里的变体。
   → 调用 `load_all()` 前临时 `chdir` 到 DLL 目录，调用完恢复。
3. **DLL 来源必须同一上游 tag**：官方 b10588 与 conda-forge 10588 同源，
   llama-cpp-python 0.3.35 的 ctypes 绑定按符号名加载，跨版本混装才
   会有缺符号风险；本方案整包同版本替换，无此问题。
4. **不能借 Ollama 的变体 DLL**：Ollama 用自己 fork 的 llama.cpp，内部
   结构体与导出符号不保证兼容，混用轻则加载失败重则段错误——所以必须
   下载官方包而不是复制 `ollama` 安装目录里的现成文件。

## 八、与 Ollama 方案的关系

两者互补：

- **Ollama**（若已安装）：独立进程 + OpenAI 兼容接口，5 分钟空闲自动
  卸载模型释放内存；`config.toml` 里 `[translate]` / `[ai]` 指向
  `http://localhost:11434/v1/chat/completions` 即可使用。
- **内置 llama_cpp 引擎**：无外部依赖、开箱即用；本升级让它从
  SSE2 基线提速 5.5 倍，作为回退链（Ollama 挂了 / 没装 Ollama 的机器）
  也足够流畅。

回退链顺序见 `config.toml` 的 `fallback_order`，例：
`glm(Ollama) → argos(离线秒回) → llama_cpp(本地兜底) → mymemory(在线兜底)`。
