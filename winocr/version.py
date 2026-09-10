# -*- coding: utf-8 -*-
"""版本信息（单一真相来源，pyproject 与 UI 都从这里读）。"""

__version__ = "3.4.27"
__version_name__ = "截图识字 · 离线翻译 · AI 解读"
__release_date__ = "2026-09-11"

# 相对 3.0 的架构变化摘要（UI「关于」对话框显示）
HIGHLIGHTS = [
    "3.4.22 日志与崩溃捕获系统：全局统一日志（RotatingFileHandler 5MB×3份）+ 环境变量/配置覆盖 + sys/threading/tk 三处未处理异常接管 + faulthandler 原生信号捕获 + crash-YYYYMMDD-HHMMSS.log 自动转储（含 traceback + 系统信息 + 线程列表），彻底解决偶发崩溃无迹可寻的问题",
    "3.4.21 TTS 朗读进度蒙版：朗读时在原文/译文区显示淡蓝透亮高亮蒙版，读到哪里蒙版跟到哪里；edge-tts 按句 chunk 粒度回调进度，SAPI 订阅 SpeakProgress 逐词事件回调；蒙版随朗读自动滚动定位，朗读结束/停止自动清除",
    "3.4.20 蒙版翻译字号自适应：逐行按「可用宽+译文字数」算字号，取『铺满宽度』与『OCR行高1:1上限』较小者——长译文铺满不空旷、短译文封顶不巨大；追加『选区均行高×1.3』约束防 OCR 检测框膨胀撑爆；FALLBACK 去掉选区高×0.06 巨型系数（根因）；位置仍逐行对齐 OCR 原行（原地覆盖）",
    "3.4.19 工程化改进归档：22 处裸 print 统一收敛 logging / app.py 拆出 exit_guard.py+style.py / dialogs_settings.py 再拆（dialogs_hotkey+dialogs_test）/ 配置注入收敛 apply_config（长参数列表→传配置对象）/ 修复 AI 页签连接编辑器从未显示",
    "3.4.18 工程化收尾：完整退出修复（venv shim 连根强杀，run.bat 不再杀进程）/ PYID 固定解释器身份对账工具 / 测试数据隔离（WINOCR_HOME）/ dialogs.py 按职责拆分 / 知识库跨项目检索修复",
    "3.4.17 TK 改进蓝图落地（P0~P2 全做）：P0-1 设置页点亮隐藏字段（离线模式/结构化 OCR/窗口尺寸/AI 提供方只读）/ P0-2 知识库浏览·检索·导出（JSON·MD·CSV）/ P0-3 源语言检测增强（langid 仅分中英，假名预检）/ P1 插件黑名单·OCR 智能升档·取色器克制折叠/ P2 首次运行向导·设置搜索·OpenAI 兼容提供方·系统托盘",
    "3.4.16 P3-15 分包/分发交付：PyInstaller onedir 双 exe（GUI WinOCR.exe + CLI WinOCR-cli.exe）+ models/vendor/plugins 外置便携 + 打包验证全绿（doctor 插件完整 / OCR 实测 / GUI 启动）；registry 插件发现改 pkgutil 兼容打包 .pyc 形态",
    "3.4.15 SAPI5 离线兜底裁决落地：补降级链回归测试 7 项（edge 命中即停 / edge 失败降级 sapi / edge 异常降级 / edge 模式不降级 / sapi 模式不走 edge / 全失败提示 / 取消跳过），锁定回退链语义",
    "3.4.14 P2-11 TTS 常驻进程：SAPI 离线兜底改常驻 PowerShell 宿主（行协议握手 + 取消即 terminate + 失败回落一次性 subprocess），免每次冷启动 + 免临时文件",
    "3.4.13 P2 日志卫生：_sel_log_static/SelectionCapturer 统一走 winocr.selection 日志（默认 WARNING 静默，WINOCR_DEBUG=1 落盘 selection.log）+ 5 模块接入 logging 消除 6 处静默吞异常",
    "3.4.12 P1 架构加固：抽取词服务（可单测/console 复用）+ 取词单测 + 最小 CI + 热键 Win32 兜底 + UI 线程守卫 + 任务取消 + AI 历史持久化落地",
    "3.4.12 取词逻辑从 Tk 适配器抽出独立服务 services/capture/selection.py（UIA 直读 + 注入兜底 + WM_COPY + 剪贴板轮询），TkUi 仅做薄壳调用",
    "3.4.12 新增 @ui_thread 守卫：后台线程误触 Tk 控件会被拦截并记录，防死锁（不再静默吞）",
    "3.4.12 新增任务取消：Ctrl+Shift+X 可在长 OCR/翻译期间中断，立即释放 busy 锁，不再苦等看门狗 30s",
    "3.4.12 全局热键新增 Win32 RegisterHotKey 兜底：非管理员下 keyboard 钩子被 UIPI 拦截时自动切换系统热键",
    "3.4.12 AI 对话历史持久化已落地（chat_history.json），重开程序保留上下文（旧注释『P1 待补』已清）",
    "3.4.11 划词取词兜底增强：注入 Ctrl+C 后轮询等待慢复制 + WM_COPY 兜底 + UIA 祖先查找",
    "3.4.10 划词图贴回填修复确认 + 泵自愈心跳：偶发「卡取词/翻译中」根治",
    "3.4.9 跨线程 UI 回写改「队列 + 主线程泵」：根治异步结果（图贴/状态）偶发不刷新",
    "3.4.8 划词「即时弹窗」：按 Ctrl+Shift+D 瞬间先弹图贴（识别中占位），取词+翻译异步回填",
    "3.4.7 划词取词改在【独立工作线程、按键瞬间】执行：杜绝 3.4.6 焦点被抢导致读数固定/不更新",
    "3.4.4 朗读加速：启动真合成预热（首声 ~19s→~3s）+ 长文本按句流式合成提前播放",
    "3.4.3 划词回归按键触发：选中文字按 Ctrl+Shift+D 弹贴图（UIA 直读 + 剪贴板兜底）",
    "版本号统一为 3.4.0（对齐目录 winOCR3.4），移除 bing 在线翻译",
    "划词翻译：小贴图 + UIA 直读，剪贴板无损备份/还原，绝不吞用户数据",
    "知识库：sqlite3 + FTS5 中文检索，同一批文档反复回看与沉淀",
    "单一 TOML 配置 + API 连接抽象（任意 OpenAI 兼容平台即插即用）",
]
