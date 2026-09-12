# 完全国内非 GitHub 链路验证报告

- **验证日期**：2026-09-12
- **验证环境**：Windows + PowerShell 7、Python 3.14.7（venv：`.venv`）、Asia/Shanghai
- **验证结论**：✅ **通过**。全部外部依赖、模型、预编译库均可在**不访问 github.com、不使用代理**的情况下完成下载、安装、组装与实际推理；四个翻译引擎与本地 LLM 在新进程（不手动设置任何环境变量）中实测全部可用。

---

## 1. 验证范围与链路

| 资源 | 下载源（非 GitHub） | 域名归属 | 结果 |
|------|---------------------|----------|------|
| OCR 模型 PP-OCRv6 tiny/medium/cls（6 个 onnx） | ModelScope `RapidAI/RapidOCR` | 国内（魔搭，阿里云） | ✅ |
| Argos 离线翻译包 en_zh / zh_en | `argos-net.com` 官方包 CDN | 官方 CDN，非 GitHub，国内直连可达 | ✅ |
| LLM 模型 Qwen2.5-0.5B-Instruct Q4_K_M | ModelScope `Qwen/Qwen2.5-0.5B-Instruct-GGUF` | 国内（魔搭，阿里云） | ✅ |
| LLM 模型 Hunyuan-MT 1.8B Q4_K_M | ModelScope `Tencent-Hunyuan/HY-MT1.5-1.8B-GGUF` | 国内（魔搭，阿里云） | ✅ |
| llama-cpp-python 0.3.35 Python 绑定 | conda-forge 南大 / 华为云 / 中科大镜像 | 国内高校 / 云厂商 | ✅ |
| llama.cpp 10588 CPU+MKL 原生库 | 同上 | 国内高校 / 云厂商 | ✅ |
| MKL 2026.1.0（26 个 DLL） | 同上 | 国内高校 / 云厂商 | ✅ |
| TBB / OpenMP / Vulkan loader | 同上 | 国内高校 / 云厂商 | ✅ |
| 解包工具 zstandard（纯 Python wheel） | 阿里云 PyPI 镜像 `mirrors.aliyun.com/pypi` | 国内（阿里云） | ✅ |
| requirements.txt 其余 Python 依赖 | 阿里云 PyPI 镜像（清华镜像近期异常时的替代） | 国内（阿里云） | ✅ |

conda 镜像根地址（脚本按序自动回退，三个均实测可下载）：

```
https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/      （南京大学，本次主用）
https://mirrors.huaweicloud.com/anaconda/cloud/conda-forge/win-64/ （华为云）
https://mirrors.ustc.edu.cn/anaconda/cloud/conda-forge/win-64/     （中科大，跳转南大）
```

> 不可用记录：清华 TUNA / BFSU 对该路径返回 403；阿里云无 conda-forge 频道。
> 全程**未使用** `github.com`、ghproxy 代理或任何翻墙工具。GitHub CPU wheel 仅作为脚本内的最后兜底，本次验证未触发。

## 2. 下载产物清单

```
models\v6_tiny\   PP-OCRv6_det_tiny.onnx   (1.7 MB, SHA256 校验通过)
                  PP-OCRv6_rec_tiny.onnx   (4.3 MB, SHA256 校验通过)
                  ch_ppocr_mobile_v2.0_cls_mobile.onnx (0.6 MB)
models\v6_medium\ PP-OCRv6_det_medium.onnx (59.2 MB, SHA256 校验通过)
                  PP-OCRv6_rec_medium.onnx (73.1 MB, SHA256 校验通过)
                  ch_ppocr_mobile_v2.0_cls_mobile.onnx (0.6 MB)
vendor\argos_packages\translate-en_zh-1_9\model\model.bin (78.9 MB, zip 完整性+目录结构校验)
vendor\argos_packages\translate-zh_en-1_9\model\model.bin (78.9 MB)
models\llama\qwen2.5-0.5b-instruct-q4_k_m.gguf   (468.6 MB, GGUF 魔数校验)
models\llama\HY-MT1.5-1.8B-Q4_K_M.gguf           (1080.6 MB, GGUF 魔数校验)
.venv\Library\bin\*.dll                          (40 个：llama/ggml 7 + MKL 26 + TBB 4 + OpenMP 2 + vulkan 1)
.venv\Lib\site-packages\llama_cpp\               (0.3.35 绑定 + dist-info)
```

conda 原始包缓存于 `vendor\.cache\conda\`（6 个文件共约 129 MB），重装不重复下载。

## 3. 功能验证结果

### 3.1 原生库加载

```
.venv\Scripts\python -c "import llama_cpp; print(llama_cpp.__version__)"
-> 0.3.35
```

### 3.2 四个翻译引擎（经翻译调度器逐引擎实测，EN→ZH）

测试句：`Hello, how are you today?`

| 引擎 | 状态 | 实测译文 |
|------|------|----------|
| argos（本地 ctranslate2） | ✅ | 你好,你好吗? |
| llama_cpp（本地 Hunyuan-MT 1.8B） | ✅ | 你好，今天你怎么样？ |
| glm（SiliconFlow 云端 `tencent/Hunyuan-MT-7B`） | ✅ | 你好，你今天过得怎么样？ |
| mymemory（免 Key 兜底） | ✅ | 您好，您今天好吗？ |

反向 ZH→EN 及纯符号文本（跳过翻译）同样验证通过。

### 3.3 本地 LLM 推理

```
Llama(qwen2.5-0.5b-instruct-q4_k_m.gguf)("2+3=")  -> 5, 5   ✅
```

### 3.4 `python main.py doctor` 自检

OCR rapidocr、翻译引擎 argos / glm / hunyuan / llama_cpp / mymemory、
AI 提供方 glm / llama_cpp / openai_compat 以及全部输入源与依赖项均为 `[+]`。

## 4. 复现方式（干净机器，全程国内链路）

```powershell
# 1) Python 依赖（阿里云 PyPI 镜像；llama-cpp-python 不在 pip 安装，走第 2 步）
.venv\Scripts\python -m pip install -r requirements.txt `
    -i https://mirrors.aliyun.com/pypi/simple/

# 2) 一条命令：多线程下载全部模型 + 从国内 conda 镜像组装 llama-cpp-python
.venv\Scripts\python tools\download_all_models.py

# 3) 自检
.venv\Scripts\python main.py doctor
```

`download_all_models.py` 行为：8 线程 HTTP Range 分片 + 每块 `.partNN`
断点续传 → SHA256 / GGUF 魔数 / zip 结构校验 → 自动归位；conda 6 个包
从南大/华为云/中科大镜像下载后组装到 venv，并起子进程真实 `import llama_cpp`
验证。已存在且校验通过的文件自动跳过，可随时中断重跑。

## 5. 验证过程中修复的两个运行时问题

1. **MKL 必须整包提取 26 个 DLL**：`mkl_rt.3.dll` 在运行时延迟加载
   `mkl_core` / `mkl_intel_thread` / `mkl_avx*` / `mkl_vml_*`，只拷一个文件
   会报 `Cannot load mkl_intel_thread.3.dll`。
2. **MKL INTEL 线程层与 Argos(ctranslate2) 自带的 OpenMP 冲突**
   （`OMP: Error #15`，进程直接中止）：已在
   [winocr/services/translate/llama_cpp.py](file:///D:/Documents/getwinOCR/winOCR3.4-master/winocr/services/translate/llama_cpp.py)
   与 [winocr/services/ai/llama_cpp_chat.py](file:///D:/Documents/getwinOCR/winOCR3.4-master/winocr/services/ai/llama_cpp_chat.py)
   模块顶部内置 `os.environ.setdefault("MKL_THREADING_LAYER", "TBB")`，
   MKL 改走 TBB 线程层，冲突消除，无需用户设置环境变量。

详细安装方案与故障排查见
[docs/llama-cpp-python-cn-mirror.md](file:///D:/Documents/getwinOCR/winOCR3.4-master/docs/llama-cpp-python-cn-mirror.md)。

## 6. 结论

在不访问 GitHub、不使用代理的条件下，WinOCR 3.4 的依赖安装、全部模型下载、
llama-cpp-python 本地推理库组装、OCR / 四引擎翻译 / 本地对话功能均验证通过，
链路可复用于国内全新机器的离线化部署。
