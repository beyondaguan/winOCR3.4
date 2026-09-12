# WinOCR 3.4

截图识字 / 离线翻译 / AI 解读 —— 插件化重构版（**当前版本：3.4.28**）。

纯 Python + Tkinter，无框架。离线 OCR 模型 + Argos 中英离线翻译，断网也能完整运行。

> **文档入口**：技术架构、快速开始、代码地图、架构铁律、从零复现、开发规范、踩坑精华，
> 全部合并在**唯一技术文档** [`DOC/WinOCR3.4文档总览.md`](DOC/WinOCR3.4文档总览.md)。
> 版本里程碑见 [`CHANGELOG.md`](CHANGELOG.md)。

## 快速开始

```cmd
install_all.bat    :: 一键全自动安装（依赖+OCR+翻译+LLM 模型，无需交互）
setup.bat          :: 同一套安装流程，下载前会询问是否下载 LLM 模型（可省 1.5GB）
run.bat            :: 启动图形界面
```

> **`install_all.bat`**（推荐）：全自动一键安装，无需任何交互。自动完成：
> - 创建虚拟环境 `.venv`
> - 安装所有 pip 依赖（阿里云镜像，失败自动换清华）
> - 从**国内 conda 镜像自动组装 `llama-cpp-python`**（南大/华为云/中科大，免编译器、不经 GitHub；失败才回退 GitHub 代理 wheel）
> - 调用统一下载器：OCR 模型（tiny + medium，约 140MB）、离线翻译包（中英，约 160MB）、LLM 模型（Qwen2.5-0.5B + Hunyuan-MT 1.8B，约 1.5GB）
> - 运行自检 `main.py doctor`
>
> `setup.bat` 与 `install_all.bat` 是同一套安装逻辑（前者仅多一个"是否下载 LLM 模型"的询问），可随时中断重跑，已就位文件自动跳过。

或手动：

```cmd
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

> **Windows PowerShell 用户**：若遇 `UnicodeDecodeError`，先执行 `$env:PYTHONUTF8=1` 再运行 pip。
>
> **llama.cpp 插件（本地 LLM）**：`requirements.txt` 中 `llama-cpp-python` 已注释——PyPI 只提供源码包，直接 pip 安装需要 C++ 编译器。
> 运行 `install_all.bat` / `setup.bat` 或 `python tools\download_all_models.py wheel` 会自动从**国内 conda 镜像**组装预编译版（无需 Miniconda、无需编译器、不经 GitHub），详见 [docs/llama-cpp-python-cn-mirror.md](docs/llama-cpp-python-cn-mirror.md)。
> 缺失时其他功能（OCR/翻译/截图）不受影响。

自检与启动：

```cmd
.venv\Scripts\python.exe main.py doctor      :: 看哪些插件可用、缺什么依赖
.venv\Scripts\python.exe main.py config --init
.venv\Scripts\python.exe main.py             :: 启动 GUI
```

> **模型获取**：`models/`（OCR 模型）与 `vendor/`（离线翻译包）因体积原因**不进 GitHub 仓库**，需按需下载。
>
> **想零下载直接用 OCR？** 把 `config.toml` 的 `ocr.model_type` 设为 `small` 即可——small 模型随 rapidocr pip 包自带，`pip install -r requirements.txt` 装完就能识别图片，不用额外下任何文件。
> 需数字/英文高精度或智能升档时再下载其他档位：
> - `tiny`（约 7MB，默认）与 `medium`（约 133MB，智能升档）从 ModelScope 官方仓库下载，带 SHA256 校验；
> - `small` 模型随 rapidocr pip 包自带，零下载；
> - 离线英汉/汉英互译包（`vendor/argos_packages/`，约 160MB）：两个安装 bat 均会自动从官方源下载，独立脚本为 `python tools/download_argos.py`。
> 下载脚本：`python tools/download_ocr_model.py [tiny|medium]`（默认档位 medium，两个安装 bat 均会自动下载 tiny 与 medium 两档）。
> 模型就绪后运行 `python main.py doctor` 自检确认可用。

## 一键统一下载（多线程，推荐）

所有外部资源可用统一脚本下载——8 线程分片、断点续传、SHA256/GGUF 校验，下载后自动归位（OCR→`models/`、翻译包→`vendor/argos_packages/`、LLM→`models/llama/`）。llama-cpp-python **优先走国内 conda 镜像（南大/华为/中科大，完全不经 GitHub、无需编译器）**，失败再回退 GitHub 代理 wheel：

```cmd
rem 全部下载（约 1.5GB）
.venv\Scripts\python tools\download_all_models.py
rem 只下载指定组
.venv\Scripts\python tools\download_all_models.py ocr llm
rem 列出资源清单 / 调整线程数 / 只下载不装 wheel
.venv\Scripts\python tools\download_all_models.py --list
.venv\Scripts\python tools\download_all_models.py --threads 16 --no-wheel-install
```

可选分组：`ocr` / `translate` / `llm` / `wheel`。已就位的文件自动跳过，中断后重跑即可续传。

## 模型下载直达地址

> 以下地址为浏览器/下载工具手动下载用；手动下载后请按"存放目录"剪切到对应位置（或直接用上面的统一下载脚本）。

### OCR 模型（ModelScope，带 SHA256 校验）

| 档位 | 文件 | 大小 | 直达地址 |
|------|------|------|----------|
| tiny | PP-OCRv6_det_tiny.onnx | ~7 MB | [下载](https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/det/PP-OCRv6_det_tiny.onnx) |
| tiny | PP-OCRv6_rec_tiny.onnx | ~7 MB | [下载](https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/rec/PP-OCRv6_rec_tiny.onnx) |
| medium | PP-OCRv6_det_medium.onnx | ~133 MB | [下载](https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/det/PP-OCRv6_det_medium.onnx) |
| medium | PP-OCRv6_rec_medium.onnx | ~133 MB | [下载](https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/rec/PP-OCRv6_rec_medium.onnx) |
| 全档位 | ch_ppocr_mobile_v2.0_cls_mobile.onnx | ~1 MB | [下载](https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx) |

**存放目录**：`models/v6_tiny/`、`models/v6_medium/`

### 离线翻译包（Argos 官方源）

| 语言包 | 大小 | 直达地址 |
|--------|------|----------|
| 英→中 (en→zh) | ~70 MB | [下载](https://argos-net.com/v1/translate-en_zh-1_9.argosmodel) |
| 中→英 (zh→en) | ~70 MB | [下载](https://argos-net.com/v1/translate-zh_en-1_9.argosmodel) |

**存放目录**：`vendor/argos_packages/translate-en_zh-1_9/`、`vendor/argos_packages/translate-zh_en-1_9/`

### LLM 模型（ModelScope，GGUF 量化）

| 模型 | 大小 | 直达地址 |
|------|------|----------|
| Qwen2.5-0.5B-Instruct-Q4_K_M | ~468 MB | [下载](https://www.modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/master/qwen2.5-0.5b-instruct-q4_k_m.gguf) |
| HY-MT1.5-1.8B-Q4_K_M（翻译专用） | ~1.08 GB | [下载](https://www.modelscope.cn/models/Tencent-Hunyuan/HY-MT1.5-1.8B-GGUF/resolve/master/HY-MT1.5-1.8B-Q4_K_M.gguf) |

**存放目录**：`models/llama/`

### llama-cpp-python（预编译版，无需 C++ 编译器）

**首选：国内 conda 镜像（非 GitHub，脚本自动完成）**

`download_all_models.py` 会从国内高校/云厂商镜像下载 conda-forge 预编译包（Python 绑定 + llama.cpp CPU/MKL 原生库 + MKL + Vulkan 加载器，共约 130MB），自动组装到 `.venv\Lib\site-packages\llama_cpp\` 与 `.venv\Library\bin\`，无需安装 Miniconda 或任何编译器：

```cmd
.venv\Scripts\python tools\download_all_models.py wheel
```

镜像根地址（脚本按序自动回退）：

- `https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/`（南京大学，实测可用）
- `https://mirrors.huaweicloud.com/anaconda/cloud/conda-forge/win-64/`（华为云）
- `https://mirrors.ustc.edu.cn/anaconda/cloud/conda-forge/win-64/`（中科大，跳转南大）

各文件直达地址（南大镜像，Python 3.14 为例，其他版本换 build 串）：

| conda 包 | 内容 | 大小 |
|----------|------|------|
| [llama-cpp-python-0.3.35-py314hb98de8c_0.conda](https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/llama-cpp-python-0.3.35-py314hb98de8c_0.conda) | Python 绑定（按 py 版本选 build） | ~0.3 MB |
| [llama.cpp-10588-cpu_mkl_h012c08f_0.conda](https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/llama.cpp-10588-cpu_mkl_h012c08f_0.conda) | llama/ggml 原生 DLL（CPU+MKL） | ~18.6 MB |
| [mkl-2026.1.0-hac47afa_235.conda](https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/mkl-2026.1.0-hac47afa_235.conda) | MKL 全部 26 个 DLL（mkl_rt 运行时延迟加载 mkl_core/mkl_intel_thread/AVX/VML，必须整包提取） | ~109 MB |
| [llvm-openmp-23.1.1-h49e36cd_0.conda](https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/llvm-openmp-23.1.1-h49e36cd_0.conda) | libiomp5md.dll / libomp.dll | ~0.3 MB |
| [tbb-2023.1.0-hdcfe883_0.conda](https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/tbb-2023.1.0-hdcfe883_0.conda) | tbb12.dll 等 4 个（MKL TBB 线程层依赖，避开与 Argos 的 OpenMP 冲突） | ~0.2 MB |
| [libvulkan-loader-1.4.357.0-h477610d_2.conda](https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/libvulkan-loader-1.4.357.0-h477610d_2.conda) | vulkan-1.dll | ~0.3 MB |

Python 版本对应的绑定包 build 串：py310→`py310h699e580_0`、py311→`py311h5dfdfe8_0`、py312→`py312ha1a9051_0`、py313→`py313h927ade5_0`、py314→`py314hb98de8c_0`（支持 3.10–3.14）。

**备选 1：Miniconda + conda 安装**（同样走国内镜像）

```cmd
conda config --add channels https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/
conda create -n winocr python=3.14
conda activate winocr
conda install llama-cpp-python
```

**备选 2：GitHub Release CPU wheel（脚本自动回退，走 ghproxy 代理）**

| 文件 | 大小 | 直达地址 |
|------|------|----------|
| llama_cpp_python-0.3.35-py3-none-win_amd64.whl（**CPU 版**） | ~6.8 MB | [下载](https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.35/llama_cpp_python-0.3.35-py3-none-win_amd64.whl) |

**安装**：`.venv\Scripts\pip install llama_cpp_python-0.3.35-py3-none-win_amd64.whl`

> ⚠️ 必须用 `v0.3.35` 标签下的 CPU 版（6.8MB）；`/releases/latest/` 的同名文件是 AMD ROCm/HIP GPU 版（~406MB），非 AMD 显卡装上后 `import llama_cpp` 会报找不到 DLL。NVIDIA 加速请改用 `v0.3.35-cu124` 等标签的 wheel。
>
> 📌 **为什么 pip 直接装不行？** PyPI（含清华/阿里镜像）上 0.3.35 只有源码包 `tar.gz`（71MB，需 Visual Studio C++ 编译），Windows 预编译 wheel 仅发布在 GitHub Release，预编译 conda 包仅发布在 conda-forge（国内镜像可直连）。

## 默认热键

| 热键 | 功能 |
|------|------|
| `Ctrl+Shift+A` | 框选截图并识别 + 翻译 |
| `Ctrl+Shift+C` | 识别剪贴板中的图片/文字 |
| `Ctrl+Shift+D` | 划词翻译（选中文字后按此键） |
| `Ctrl+Shift+E` | 循环切换翻译引擎 |
| `Ctrl+Shift+R` | 朗读当前译文/原文 |
| `Ctrl+Shift+K` | 打开知识库（浏览 / 检索 / 导入 / 导出） |
| `Ctrl+Shift+I` | 导入知识库（弹引导窗：选目标项目 + 选 JSON/CSV 文件） |
| `Ctrl+Shift+X` | 取消当前任务（OCR / 翻译） |
| `Ctrl+Shift+Q` | 退出整个程序（全局闸门，永久存在） |

`Esc` / 关闭按钮仅隐藏窗口，程序后台常驻。

## 命令行入口

```cmd
python main.py                  # 启动图形界面（默认）
python main.py console          # 无界面模式
python main.py doctor           # 自检：插件可用性与依赖
python main.py models           # 查看 OCR / Argos 模型资源
python main.py config --init    # 生成默认配置
python main.py ocr 图片.png -t zh-CN   # 命令行识别并翻译
```

## 关键事实（详情见 DOC 总览）

- **架构**：六轴插件化（capture/ocr/translate/ai/attach/persistence）+ 组合根依赖注入 + 事件总线。
- **配置**：单一 TOML，功能视角——每个功能页直接持有自己的连接参数，互不串 Key。
- **扩展**：在 `winocr/services/*/` 或 `plugins/` 丢一个 `.py` 继承对应基类，注册表自动发现，核心代码零改动。
- **离线优先**：`argos` 本地神经翻译是断网最后保障；`[translate].offline_mode=True` 仅走离线引擎。
- **划词翻译方向可选**：小贴条底部「自动 / 译中 / 译英」三态按钮，点选即按该方向重译；每次划词自动写入历史记录。
- **历史记录面板**：主窗口信息栏「历史记录」按钮 → 列表浏览 / 关键词检索 / 详情查看 / 复制原文·译文 / 导出 Markdown·TXT / 清空。
- **项目栏（按项目隔离）**：顶部标签页栏新建 / 切换 / 关闭（× 仅关标签、**数据保留**）项目；知识库、翻译历史、AI 对话、译向全部按项目隔离；「管理项目」里做重开 / 改名 / 设译向 / **彻底删除**（默认项目不可删不可关）。
- **知识库导入**：`Ctrl+Shift+I` 直达导入引导窗——选目标项目 + 选 **JSON 或 CSV**；系统在 `~/.winocr/templates/` 自动生成导入模板（`知识库导入模板.csv` / `.json` + 字段说明 txt，用 WPS/Excel 编辑 CSV 即可批量灌数据）；配合导出 JSON/MD/CSV 形成备份恢复闭环；空记录自动跳过。
- **热键录制**：设置里录任意组合键立即覆盖旧键生效并重启保留（清空则恢复默认）。
- **当前路线（作者定论）**：以 3.4 TK 为唯一基线；GUI 演进暂缓（WebView2 已失败、PySide6 未做好）；不把 winOCR 引擎化。

## 项目结构

```
WinOCR3.4/
├── main.py                  # 多命令 CLI 入口
├── winocr/                  # 源码（core 组合根 / services 六轴 / ui/tk 单 UI）
├── models/v6_tiny/          # 内置 OCR 模型
├── vendor/argos_packages/   # 内置离线翻译包
├── plugins/capsules/        # 第三方场景胶囊（自动发现）
├── tests/                   # 32 文件 212 用例全过
└── DOC/
    ├── WinOCR3.4文档总览.md  # 唯一技术文档（架构/复现/规范/踩坑已合并）
    └── ui_demo/              # UI 原型 HTML
```
