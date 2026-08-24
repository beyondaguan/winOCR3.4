# WinOCR 3.4

截图识字 / 离线翻译 / AI 解读 —— 插件化重构版（**当前版本：3.4.17**）。

纯 Python + Tkinter，无框架。离线 OCR 模型 + Argos 中英离线翻译已内置，断网也能完整运行。

> **文档入口**：技术架构、从零复现、配置系统、插件机制、打包分发、使用说明、测试与决策结论，全部在
> **[`DOC/WinOCR3.4文档总览.md`](DOC/WinOCR3.4文档总览.md)**。版本里程碑见 [`CHANGELOG.md`](CHANGELOG.md)。
> 新手入门可先读 [`DOC/小白如何整体把握代码.md`](DOC/小白如何整体把握代码.md)，AI 编程踩坑经验见 [`DOC/踩坑.md`](DOC/踩坑.md)，修复流程规范见 [`DOC/团队规范-修复节奏.md`](DOC/团队规范-修复节奏.md)。

## 快速开始

```cmd
setup.bat          :: 创建 .venv、装依赖、自检
run.bat            :: 启动图形界面
```

或手动：

```cmd
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

自检与启动：

```cmd
.venv\Scripts\python.exe main.py doctor      :: 看哪些插件可用、缺什么依赖
.venv\Scripts\python.exe main.py config --init
.venv\Scripts\python.exe main.py             :: 启动 GUI
```

模型文件已内置在仓库（`models/v6_tiny/` 约 7MB、`vendor/argos_packages/` 约 164MB），无需联网下载。

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
├── tests/                   # 119 用例全过
└── DOC/
    ├── WinOCR3.4文档总览.md  # 唯一技术文档
    ├── 小白如何整体把握代码.md # 新手代码导览
    ├── 踩坑.md               # AI 编程踩坑记录
    └── 团队规范-修复节奏.md   # 修复流程规范
```
