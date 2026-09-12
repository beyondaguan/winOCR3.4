# llama-cpp-python 国内非 GitHub 安装方案（已实测验证）

> 验证日期：2026-09-12
> 验证环境：Windows + Python 3.14（venv）+ llama-cpp-python 0.3.35
> 验证结果：从国内镜像下载、组装到 venv、加载 GGUF 模型实际推理，全链路通过
> （`2+3=` → `5, 5`）。全程不访问 GitHub、不需要 Visual Studio C++ 编译器、不依赖 conda 环境。

---

## 1. 为什么常规方式装不了

| 渠道 | Windows 预编译版 | 问题 |
|------|:---:|------|
| `pip install llama-cpp-python`（PyPI） | ❌ | 0.3.35 在 PyPI 上**只有源码包** `llama_cpp_python-0.3.35.tar.gz`（71.4 MB），安装时调 CMake + nmake 编译，没有 VS Build Tools 直接报错：`no such file or directory: 'nmake'` |
| 清华 / 阿里等 pip 国内镜像 | ❌ | 只同步 PyPI 源码包，同样没有 Windows wheel |
| GitHub Release wheel | ✅ | 文件只发布在 GitHub；且 `/releases/latest/download/...` 的同名文件实际是 **AMD ROCm/HIP GPU 版（~406 MB）**，非 AMD 显卡装上后 `import llama_cpp` 报 `Failed to load shared library 'llama.dll'`。真正的 CPU 版在 `v0.3.35` 标签下，仅 6.8 MB |
| **conda-forge 预编译包 + 国内镜像** | ✅ | **本方案**：国内高校/云厂商镜像可直连，包已编译好，脚本自动组装进普通 pip venv |

---

## 2. 已验证的国内镜像（2026-09-12 实测）

镜像根路径：`<镜像>/anaconda/cloud/conda-forge/win-64/`

| 镜像 | 域名 | 状态 |
|------|------|:---:|
| 南京大学 | `mirror.nju.edu.cn` | ✅ HTTP 206，可 Range，已实际下载全部包 |
| 华为云 | `mirrors.huaweicloud.com` | ✅ HTTP 200 |
| 中科大 | `mirrors.ustc.edu.cn` | ✅ HTTP 206（302 跳转南大 CDN） |
| 上海交大 | `mirror.sjtu.edu.cn` | ✅ HTTP 206（跳转 jcloud OSS） |
| 清华 TUNA | `mirrors.tuna.tsinghua.edu.cn` | ❌ 403 Forbidden（conda-forge 目录） |
| 北外 BFSU | `mirrors.bfsu.edu.cn` | ❌ 302 跳回清华后 403 |
| 阿里云 | `mirrors.aliyun.com` | ❌ 该路径 404（未镜像 conda-forge） |

> 脚本按 **南大 → 华为云 → 中科大** 顺序自动回退。

---

## 3. 需要下载的 6 个 conda 包（共约 129 MB）

conda-forge 把 llama-cpp-python 拆成「Python 绑定」和多个「原生 DLL 依赖包」，缺一不可。

| # | conda 包文件名 | 大小 | 提取的文件 |
|---|----------------|-----:|------------|
| 1 | `llama-cpp-python-0.3.35-py314hb98de8c_0.conda` | 0.3 MB | `llama_cpp/` Python 包 + dist-info（按 Python 版本换 build 串） |
| 2 | `llama.cpp-10588-cpu_mkl_h012c08f_0.conda` | 18.6 MB | `llama.dll` `mtmd.dll` `ggml.dll` `ggml-base.dll` `ggml-cpu.dll` `ggml-blas.dll` `ggml-vulkan.dll` |
| 3 | `mkl-2026.1.0-hac47afa_235.conda` | 109 MB | **全部 26 个 DLL**（见下方坑 1；不能只取 `mkl_rt.3.dll`） |
| 4 | `llvm-openmp-23.1.1-h49e36cd_0.conda` | 0.3 MB | `libiomp5md.dll` `libomp.dll`（TBB 线程层下不加载，保留即可） |
| 5 | `tbb-2023.1.0-hdcfe883_0.conda` | 0.2 MB | `tbb12.dll` `tbbmalloc.dll` `tbbmalloc_proxy.dll` `tbbbind_2_5.dll`（**坑 2 需要**） |
| 6 | `libvulkan-loader-1.4.357.0-h477610d_2.conda` | 0.3 MB | `vulkan-1.dll`（ggml-vulkan 依赖，无独显时由加载器兜底，不影响 CPU 运行） |

系统已自带、无需下载的运行库：`vcruntime140.dll`、`vcruntime140_1.dll`、`msvcp140.dll`、`vcomp140.dll`（VC++ 2015-2022 Redistributable，Windows 10/11 一般已具备）。

### 两个必须处理的运行时坑（均已在脚本/代码中修复，手动安装必看）

**坑 1：MKL 不能只提取 `mkl_rt.3.dll`——它运行时延迟加载一整组 DLL**

PE 静态导入表里 `mkl_rt.3.dll` 只显示依赖 kernel32，看起来一个文件就够了；
实际运行时它会动态加载 `mkl_core.3.dll`、`mkl_intel_thread.3.dll`、
`mkl_def/mc3/avx2/avx512/avx10.3.dll`、`mkl_vml_*.3.dll` 等 26 个文件，
只拷 `mkl_rt.3.dll` 会报：

```
INTEL oneMKL ERROR: 找不到指定的模块。 mkl_intel_thread.3.dll.
Intel oneMKL FATAL ERROR: Cannot load mkl_intel_thread.3.dll.
```

处理：mkl 包 `Library/bin/` 下的**全部 .dll** 原样提取（约 26 个，
解压后约 400MB，.conda 下载仍为 109MB）。

**坑 2：MKL 默认 INTEL 线程层与 ctranslate2（Argos）的 OpenMP 冲突**

Argos 引擎的 ctranslate2 wheel 自带一份 `libiomp5md.dll`；MKL 默认的
INTEL 线程层（`mkl_intel_thread.3.dll`）也要加载 OpenMP 运行时
（conda 包内的 `libiomp5md.dll` + `libomp.dll`）。同一进程先后加载两份
OpenMP 会直接中止进程：

```
OMP: Error #15: Initializing libomp.dll, but found libiomp5md.dll already initialized.
```

处理：让 MKL 改用 TBB 线程层——在 llama_cpp 原生库加载**之前**设置
环境变量 `MKL_THREADING_LAYER=TBB`，此时 MKL 改加载
`mkl_tbb_thread.3.dll` + `tbb12.dll`（第 5 个包就是为此准备的），
进程中不再出现第二份 OpenMP。项目代码已在
[winocr/services/translate/llama_cpp.py](file:///D:/Documents/getwinOCR/winOCR3.4-master/winocr/services/translate/llama_cpp.py)
和 [winocr/services/ai/llama_cpp_chat.py](file:///D:/Documents/getwinOCR/winOCR3.4-master/winocr/services/ai/llama_cpp_chat.py)
模块顶部 `os.environ.setdefault("MKL_THREADING_LAYER", "TBB")`。
手动安装/自行写启动脚本时，在 Python 进程最早期加：

```python
import os
os.environ.setdefault("MKL_THREADING_LAYER", "TBB")
```

> 不建议用官方提示的 `KMP_DUPLICATE_LIB_OK=TRUE` 压制冲突——那是
> "不受支持的不安全绕过"，两份 OpenMP 并行可能导致结果错误或崩溃。

### Python 版本与绑定包 build 串对照

| Python | 绑定包 build 串 |
|:------:|-----------------|
| 3.10 | `py310h699e580_0` |
| 3.11 | `py311h5dfdfe8_0` |
| 3.12 | `py312ha1a9051_0` |
| 3.13 | `py313h927ade5_0` |
| 3.14 | `py314hb98de8c_0` |

> 例：Python 3.11 时第 1 个文件改为
> `llama-cpp-python-0.3.35-py311h5dfdfe8_0.conda`。

### 直达地址（南大镜像，Python 3.14）

```
https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/llama-cpp-python-0.3.35-py314hb98de8c_0.conda
https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/llama.cpp-10588-cpu_mkl_h012c08f_0.conda
https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/mkl-2026.1.0-hac47afa_235.conda
https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/llvm-openmp-23.1.1-h49e36cd_0.conda
https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/tbb-2023.1.0-hdcfe883_0.conda
https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/libvulkan-loader-1.4.357.0-h477610d_2.conda
```

其他版本/平台的完整文件清单可查 anaconda API：
`https://api.anaconda.org/package/conda-forge/llama-cpp-python/files`

---

## 4. 一键安装（推荐）

项目已内置全自动化脚本，多线程下载、镜像回退、解包组装、导入校验一条龙：

```cmd
.venv\Scripts\python tools\download_all_models.py wheel
```

下载全部资源（OCR 模型 + 翻译包 + LLM 模型 + llama-cpp-python）：

```cmd
.venv\Scripts\python tools\download_all_models.py
```

脚本行为：

1. 先检测当前 Python 是否已经能 `import llama_cpp`，已可用直接跳过；
2. 优先从南大/华为云/中科大 conda 镜像下载上表 6 个包（缓存在 `vendor\.cache\conda\`，不重复下载）；
3. 自动安装纯 Python 小包 `zstandard`（从阿里 PyPI 镜像）用于解包 `.conda`；
4. 按下表组装；
5. 起子进程实际 `import llama_cpp` 验证；
6. conda 镜像全部失败时，自动回退 GitHub CPU wheel（经 ghproxy 代理）。

---

## 5. 组装后的目录布局

```
.venv\
├── Library\
│   └── bin\                         ← 全部原生 DLL（40 个）
│       ├── llama.dll
│       ├── mtmd.dll
│       ├── ggml.dll / ggml-base.dll / ggml-cpu.dll / ggml-blas.dll / ggml-vulkan.dll
│       ├── mkl_*.dll（26 个：mkl_rt/mkl_core/mkl_intel_thread/mkl_tbb_thread/
│       │              mkl_avx*/mkl_vml_*/libimalloc 等，必须整包提取）
│       ├── libiomp5md.dll / libomp.dll
│       ├── tbb12.dll / tbbmalloc.dll / tbbmalloc_proxy.dll / tbbbind_2_5.dll
│       └── vulkan-1.dll
└── Lib\
    └── site-packages\
        ├── llama_cpp\               ← 第 1 个包里 Lib/site-packages/ 的全部内容
        └── llama_cpp_python-0.3.35.dist-info\
```

**关键细节（踩坑记录）**：conda-forge 版的 `llama_cpp/llama_cpp.py` 带了一个补丁，
在 Windows 上不再从 `llama_cpp/lib/` 加载 DLL，而是从 **Python 前缀的上溯三级 +
`Library\bin`** 加载。在 venv 里解析出的路径正好是

```
.venv\Library\bin\
```

所以 DLL 必须放到该目录，放到 `site-packages\llama_cpp\lib\` 无效
（会报 `WinError 3 系统找不到指定的路径: ...\Library\bin`）。

也可以用环境变量临时覆盖加载目录：

```cmd
set LLAMA_CPP_LIB_PATH=D:\path\to\bin
```

---

## 6. 手动安装步骤（不依赖项目脚本时）

需要 Python ≥ 3.8（用 zipfile + zstandard）、7z 或 Python 均可解包。
`.conda` 本质是 zip，内含 `pkg-*.tar.zst`（文件主体）和 `info-*.tar.zst`（元数据）。

### 6.1 创建并激活 venv

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install zstandard -i https://mirrors.aliyun.com/pypi/simple/
pip install numpy diskcache jinja2 typing-extensions pyyaml
```

### 6.2 下载 6 个包

```cmd
mkdir vendor\.cache\conda
cd vendor\.cache\conda
curl -LO https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/llama-cpp-python-0.3.35-py314hb98de8c_0.conda
curl -LO https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/llama.cpp-10588-cpu_mkl_h012c08f_0.conda
curl -LO https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/mkl-2026.1.0-hac47afa_235.conda
curl -LO https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/llvm-openmp-23.1.1-h49e36cd_0.conda
curl -LO https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/tbb-2023.1.0-hdcfe883_0.conda
curl -LO https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/libvulkan-loader-1.4.357.0-h477610d_2.conda
cd ..\..\..
```

### 6.3 解包组装（Python 脚本）

```python
import io, os, tarfile, zipfile, zstandard
from pathlib import Path

# 必须在 import llama_cpp / 加载 MKL 之前设置（避开与 ctranslate2 的 OpenMP 冲突，坑 2）
os.environ.setdefault("MKL_THREADING_LAYER", "TBB")

PREFIX = Path(".venv")
CACHE  = Path("vendor/.cache/conda")
SITE   = PREFIX / "Lib/site-packages"
BIN    = PREFIX / "Library/bin"
SITE.mkdir(parents=True, exist_ok=True)
BIN.mkdir(parents=True, exist_ok=True)

# wanted=None 表示提取该包 Library/bin 下全部 .dll（mkl 必须整包，见坑 1）
DLLS = {
    "llama.cpp-10588-cpu_mkl_h012c08f_0.conda": {
        "llama.dll", "mtmd.dll", "ggml.dll", "ggml-base.dll",
        "ggml-cpu.dll", "ggml-blas.dll", "ggml-vulkan.dll"},
    "mkl-2026.1.0-hac47afa_235.conda": None,
    "llvm-openmp-23.1.1-h49e36cd_0.conda": {"libiomp5md.dll", "libomp.dll"},
    "tbb-2023.1.0-hdcfe883_0.conda": {
        "tbb12.dll", "tbbmalloc.dll", "tbbmalloc_proxy.dll", "tbbbind_2_5.dll"},
    "libvulkan-loader-1.4.357.0-h477610d_2.conda": {"vulkan-1.dll"},
}

def open_pkg(conda_file):
    z = zipfile.ZipFile(conda_file)
    name = next(n for n in z.namelist() if n.startswith("pkg-"))
    raw = zstandard.ZstdDecompressor().decompress(z.read(name))
    return tarfile.open(fileobj=io.BytesIO(raw))

# 1) Python 绑定
tf = open_pkg(CACHE / "llama-cpp-python-0.3.35-py314hb98de8c_0.conda")
for m in tf.getmembers():
    if m.isfile() and "Lib/site-packages/" in m.name:
        rel = m.name.split("Lib/site-packages/", 1)[1]
        dst = SITE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(tf.extractfile(m).read())

# 2) 原生 DLL（只取每个包 Library/bin/ 下的 .dll）
for pkg, wanted in DLLS.items():
    tf = open_pkg(CACHE / pkg)
    for m in tf.getmembers():
        if not m.isfile() or "/bin/" not in m.name:
            continue
        base = m.name.split("/")[-1]
        if wanted is None or base in wanted:
            (BIN / base).write_bytes(tf.extractfile(m).read())
```

> 若不通过项目代码启动（自己写的脚本/Notebook），别忘了在进程最早期
> 设置 `MKL_THREADING_LAYER=TBB`，否则同进程加载 Argos(ctranslate2) 后
> 首次调用 llama 推理会触发 OMP Error #15。

### 6.4 验证

```cmd
.venv\Scripts\python -c "import llama_cpp; print(llama_cpp.__version__)"
```

预期输出 `0.3.35`，无异常即成功。

真实推理验证（需要一个 GGUF 模型）：

```cmd
.venv\Scripts\python -c "from llama_cpp import Llama; llm=Llama(model_path=r'models\llama\qwen2.5-0.5b-instruct-q4_k_m.gguf', n_ctx=256, verbose=False, n_threads=4); print(llm('2+3=', max_tokens=4, temperature=0)['choices'][0]['text'])"
```

预期输出类似 `5, 5`。

项目自检：

```cmd
.venv\Scripts\python main.py doctor
```

`AI 提供方` 与 `翻译引擎` 下的 `llama_cpp` 均应显示 `[+]`。

---

## 7. 故障排查

| 现象 | 原因与处理 |
|------|-----------|
| `WinError 3 ... \Library\bin` | DLL 放错位置。必须放在 `.venv\Library\bin\`，不是 `site-packages\llama_cpp\lib\` |
| `Failed to load shared library 'llama.dll'` | DLL 依赖不全。对照第 3 节确认 40 个 DLL 都在；曾误用 GitHub `/latest/` 的 HIP 版也会报此错 |
| `INTEL oneMKL ERROR ... mkl_intel_thread.3.dll` | 只提取了 `mkl_rt.3.dll`。mkl 必须整包提取 `Library/bin` 全部 26 个 DLL（坑 1） |
| `OMP: Error #15 ... libomp.dll/libiomp5md.dll already initialized` | MKL 的 INTEL 线程层与 ctranslate2 的 OpenMP 冲突。加载 llama 前设 `MKL_THREADING_LAYER=TBB`，并确保 tbb 包 4 个 DLL 已就位（坑 2）；不要用 `KMP_DUPLICATE_LIB_OK=TRUE` 压制 |
| 下载 403（清华） | 换南大/华为云/中科大镜像；脚本已自动处理 |
| `import zstandard` 失败 | `pip install zstandard -i https://mirrors.aliyun.com/pypi/simple/` |
| Python 3.9 或更旧 | 该 conda 包只提供 py310-py314；升级 Python，或改用 GitHub CPU wheel |
| 想要 NVIDIA GPU 加速 | conda 包另有 `llama.cpp-10588-cuda129_*` / `cuda130_*`；GitHub 有 `v0.3.35-cu124` 等 wheel |
| mkl 包 109MB 太大想省流量 | 可改用 GitHub CPU 版 wheel（6.8MB，自带静态依赖），脚本的 conda 路由失败后会自动回退 |

---

## 8. 备选：真正的 conda 环境（不用 venv 组装）

已安装 Miniconda/Anaconda 时更简单，DLL 依赖由 conda 自动解决，channel 同样指向国内镜像：

```cmd
conda config --add channels https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/
conda config --set show_channel_urls yes
conda create -n winocr python=3.14
conda activate winocr
conda install llama-cpp-python
```
