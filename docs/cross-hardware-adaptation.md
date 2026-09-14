# WinOCR 3.4 跨硬件 / 跨环境适配 — 更新说明

更新日期：2026-09-14
版本：3.4.29

## 一、自动硬件探测（代码层）

新增 `winocr/core/hardware.py`，三个核心引擎从"写死 R5 5500"改为"自动探测 + 环境变量覆盖"。

### 探测规则

| 参数 | 探测逻辑 | 不同机器的结果示例 |
|---|---|---|
| **CPU 线程数** | 物理核数 → 封顶 8（llama.cpp / ctranslate2 超过 8 核收益递减） | 双核轻薄本=2，R5 5500=8，32核台式=8 |
| **GPU layers** | nvidia-smi 查算力 → 算力 ≥ 7.0（Volta+）才走 GPU；老卡（Kepler/Pascal/Turing 部分）自动过滤，走 CPU 反而更快 | GT 710=0（过滤），RTX 4080=99（全层卸载） |
| **ctx 窗口** | 总内存 ≥ 16GB → 8192；≥ 8GB → 4096；否则 2048 | 4GB 机器=2048，16GB=8192 |

### 用户覆盖优先级

环境变量始终比探测结果优先：
```
WINOCR_LLAMA_THREADS=12    # 强制 12 线程（即使只有 6 核）
WINOCR_LLAMA_GPU_LAYERS=0  # 强制全 CPU（即使有 RTX 4080）
WINOCR_LLAMA_CTX=16384     # 强制 16K 上下文
```

### 为什么 GPU 老卡过滤？

| 架构 | 算力 | FP16 硬件加速 | llama.cpp 表现 |
|---|---|---|---|
| Kepler（GT 710 / GTX 750） | ≤ 5.x | ❌ | FP32 推理，比 CPU AVX2 慢 2~3 倍 |
| Pascal（GTX 10xx） | 6.x | ❌ | 同上 |
| Turing（RTX 20xx） | 7.5 | ✅ | 开始有 GPU 加速 |
| Ampere+（RTX 30xx/40xx） | ≥ 8.0 | ✅ | GPU 提速 3~10 倍 |

过滤逻辑在 `hardware.py` 的 `has_nvidia_gpu()` 函数里，用 `nvidia-smi --query-gpu=name,compute_cap` 查询。

## 二、安装脚本跨环境修复（bat 层）

### 问题：pip 镜像写死国内源

原代码：
```bat
%PY% -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
if errorlevel 1 (
    %PY% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
```
海外机器（美国/欧洲/东南亚）阿里云和清华镜像都拉不到 → 安装直接失败。

修复后的优先级：阿里云 → 清华 → **PyPI 官方**（`--default-timeout=60` 防 5 秒超时就放弃）。

### 问题：conda 镜像只有国内源（download_all_models.py）

原代码只列了南大/华为云/中科大三个镜像，全部国内。海外机器下载 llama-cpp-python 包失败。

修复：加上 `conda-forge` 官方仓库（`https://conda.anaconda.org/conda-forge/win-64/`）作为最后回退。

### 问题：Python 版本过宽

原 `install_all.bat` 探测 `py -3` 只看 tkinter 能不能 import，没检查版本。Python 3.9 装 `onnxruntime>=1.23.2` 会失败。

修复：加版本检查，< 3.10 直接报错退出。

## 三、requirements.txt 兼容性

| 依赖 | 最低版本 | Python 3.10 | Python 3.11 | Python 3.12 | Python 3.13 | Python 3.14 |
|---|---|---|---|---|---|---|
| Pillow | 9.0 | ✅ | ✅ | ✅ | ✅ | ✅ |
| numpy | 1.20 | ✅ | ✅ | ✅ | ✅ | ✅ |
| onnxruntime | 1.23.2 | ✅ | ✅ | ✅ | ✅ | ✅ |
| rapidocr | 3.9 | ✅ | ✅ | ✅ | ✅ | ✅ |
| ctranslate2 | 4.0 | ✅ | ✅ | ✅ | ✅ | ✅ |
| sentencepiece | 0.2 | ✅ | ✅ | ✅ | ✅ | ✅ |
| PyMuPDF | 1.24 | ✅ | ✅ | ✅ | ✅ | ✅ |
| keyboard | 0.13.5 | ✅ | ✅ | ✅ | ✅ | ✅ |
| uiautomation | 2.0 | ✅ | ✅ | ✅ | ✅ | ✅ |
| edge-tts | 7.0 | ✅ | ✅ | ✅ | ✅ | ✅ |
| tkinterdnd2 | 0.4 | ✅ | ✅ | ✅ | ✅ | ✅ |
| pystray | 0.19 | ✅ | ✅ | ✅ | ✅ | ✅ |

llama-cpp-python **不在 requirements.txt 里**——PyPI 只有源码包需要 C++ 编译器，默认从 conda-forge 预编译包安装（`download_all_models.py wheel` 步骤）。

## 四、download_all_models.py 跨平台支持

### wheel 版本

目前只内置了 `win_amd64`（CPU 版）。如果未来需要 GPU 版或 ARM 版：
- CUDA 版 wheel：`llama_cpp_python-0.3.35-cp311-cp311-win_amd64.whl`（需用户自行从 GitHub releases 下载，conda-forge 不提供 CUDA 版）
- ARM 版 wheel：PyPI 上暂无，需 llama-cpp-python 0.3.36+ 支持

### conda 包 build string

| Python | conda-forge build string |
|---|---|
| 3.10 | py310h699e580_0 |
| 3.11 | py311h5dfdfe8_0 |
| 3.12 | py312ha1a9051_0 |
| 3.13 | py313h927ade5_0 |
| 3.14 | py314hb98de8c_0 |

conda-forge 每次发版 build string 会变（取决于当时依赖的 hash），如果下载失败更新 `_CONDA_LCP_BUILDS` 字典即可。

## 五、已知不兼容场景

| 场景 | 现状 | 影响 | 规避方案 |
|---|---|---|---|
| Windows on ARM（Surface Pro X 等） | llama-cpp-python 无 win_arm64 预编译包 | OCR + 翻译（ctranslate2 有 ARM 版）可用，LLM 对话不可用 | 自行交叉编译或用 Docker x86 仿真 |
| 服务器级 CPU（Intel Xeon / AMD EPYC） | 代码正常跑，但建议线程数可能不是最优 | EPYC 有 64+ 物理核但很多是跨 NUMA 节点，8 线程可能不够 | 手动设 `WINOCR_LLAMA_THREADS=16~32` |
| 内存 < 4GB | ctx 2048 也可能吃紧 | 翻译长文时 swap 严重 | 用更小 ctx：`WINOCR_LLAMA_CTX=1024` |
| Linux / macOS | bat 脚本不跑，Tk 窗口 manager 差异 | GUI 可能错位，UIAutomation 不可用 | 项目当前只正式支持 Windows |
| Python 3.9 及以下 | rapidocr ≥3.9.0 需要 Python 3.10+ | pip 安装直接失败 | 强制升级 Python |

## 六、验证清单

在以下场景下测试完整安装 + 运行 + pytest 全量通过：

- [x] R5 5500 + GT 710（6核12线程，老卡过滤）
- [ ] 双核轻薄本（如 Intel N100）
- [ ] RTX 4080 + 32 核（GPU 全层卸载）
- [ ] Python 3.10 / 3.11 / 3.12 / 3.13 / 3.14 各一个
- [ ] 海外网络（pip 直连 PyPI 官方，不走国内镜像）
- [ ] conda-forge 官方镜像（国内镜像全挂时的回退）
