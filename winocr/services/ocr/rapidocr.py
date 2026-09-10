# -*- coding: utf-8 -*-
"""RapidOCR + PP-OCRv6 引擎（完整移植自 WinOCR2.0/ocr_engine.py，纯离线）。

保留 2.0 里所有踩过坑换来的逻辑，一条不少：
  - rapidocr 3.x 的 engine_type/ocr_version/model_type 必须传枚举，传字符串会被拒；
  - 仓库里出现过 72 字节的占位 .onnx，必须按体积挡掉，否则报难懂的 protobuf 错误；
  - 返回值有两种形态（3.x 的 RapidOCROutput 数据类 / 1.x 的 (result, elapse) 元组），
    按属性探测自适应，不依赖版本号判断；
  - 按 Y 坐标聚类分行、行内按 X 排序，还原真实排版；
  - 预处理区分「代码截图」与「普通照片」：代码截图只锐化以保留高亮色，
    灰度化会破坏符号识别。

相比 2.0 的改进：不再有模块级全局 ENGINE_NAME / ENABLE_PREPROCESSING，
状态收敛为实例属性，可同时存在多个不同配置的实例（测试友好）。
"""
from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Optional, Tuple

from .base import OcrEngine
from .structure import clean_text
from ...core.paths import ocr_model_dir
from ...core.types import OcrResult

logger = logging.getLogger(__name__)

# 低于此体积的 .onnx 视为占位符 / 下载残缺
_MIN_VALID_MODEL_SIZE = 100 * 1024

# rapidocr 包内模型路径缓存（_pkg_model 每次 import rapidocr/os/Path，重复调用浪费）
_pkg_models_cache: dict = {}


class RapidOcrEngine(OcrEngine):
    name = "rapidocr"
    display_name = "RapidOCR (PP-OCRv6, 本地内置模型)"
    offline = True

    def __init__(self) -> None:
        self._engine = None
        self._engine_sig = None  # 构建当前引擎时的 (det, rec, cls) 模型路径签名
        self._lock = threading.Lock()
        self.preprocess: bool = True
        self.model_type: str = "tiny"
        self.structured: bool = False      # 几何重建表格/版面（离线，零额外模型）
        self.paragraph_mode: str = "A+B"   # A=仅行分组 / B=仅几何段落 / A+B=两者结合
        self._models_cache: dict = {}      # {tier: (det,rec,cls)}；配置变更后失效

    def configure(self, **kwargs) -> None:
        """接收配置注入；档位变了模型路径缓存必须失效。"""
        super().configure(**kwargs)
        self._models_cache.clear()

    def apply_config(self, config) -> None:
        """从 OcrConfig 注入本地档位参数（云端参数由 VisionOcrEngine 处理）。"""
        self.preprocess = config.preprocess
        self.model_type = config.model_type
        self.structured = config.structured
        mode = getattr(config, "paragraph_mode", "A+B")
        self.paragraph_mode = mode if mode in ("A", "B", "A+B") else "A+B"
        self._models_cache.clear()

    # ------------------------------------------------------------------
    # 依赖 / 模型
    # ------------------------------------------------------------------
    def available(self) -> bool:
        try:
            import rapidocr  # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def _valid_model(path) -> bool:
        if path is None:
            return False
        try:
            return path.is_file() and path.stat().st_size >= _MIN_VALID_MODEL_SIZE
        except OSError:
            return False

    _OCR_TIERS = ("tiny", "small", "medium")

    def _effective_tier(self) -> str:
        """实际生效的模型档位：请求档位若本地缺模型，回落 tiny。

        扫描结果按档位缓存（configure 后失效），避免 warmup 时重复扫盘。
        """
        tier = self.model_type if self.model_type in self._OCR_TIERS else "tiny"
        if tier not in self._models_cache:
            self._models_cache[tier] = self._find_models(tier)
        return tier if self._models_cache[tier][0] is not None else "tiny"

    def _find_models(self, tier: str):
        """按优先级找 (det, rec, cls) 路径；找不到返回 (None, None, None)。

        来源顺序（从「用户显式放置」到「随包自带」）：
          1. models/v6_{tier}/ 下 RapidOCR 官方命名 PP-OCRv6_{part}_{tier}.onnx
             （download_ocr_model.py 的落位，也兼容用户手动放置）
          2. rapidocr 包内 models/ 下同名文件（pip 包自带 small，零下载即用）
          3. models/v6_{tier}/ 下 PaddleOCR 旧命名 PP-OCRv6_{tier}_{part}_infer.onnx
             （现有 v6_tiny 目录的文件，保持行为不变）
        cls 全档位通用（ch_ppocr_mobile_v2.0_cls_mobile.onnx），先查各位置再查包内。
        """
        d = ocr_model_dir(tier)
        candidates = {
            "det": [
                d / f"PP-OCRv6_det_{tier}.onnx",
                self._pkg_model(f"PP-OCRv6_det_{tier}.onnx"),
                d / f"PP-OCRv6_{tier}_det_infer.onnx",
            ],
            "rec": [
                d / f"PP-OCRv6_rec_{tier}.onnx",
                self._pkg_model(f"PP-OCRv6_rec_{tier}.onnx"),
                d / f"PP-OCRv6_{tier}_rec_infer.onnx",
            ],
            "cls": [
                d / "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
                self._pkg_model("ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
                d / f"PP-OCRv6_{tier}_cls_infer.onnx",
            ],
        }
        det = next((p for p in candidates["det"] if self._valid_model(p)), None)
        rec = next((p for p in candidates["rec"] if self._valid_model(p)), None)
        cls = next((p for p in candidates["cls"] if self._valid_model(p)), None)
        if det is not None and rec is not None:
            return (str(det), str(rec), (str(cls) if cls else None))
        return None, None, None

    @staticmethod
    def _pkg_model(name: str):
        """rapidocr pip 包内自带的模型文件（如 PP-OCRv6_det_small.onnx）。

        结果按文件名缓存：本函数在 _find_models 的候选构造里会被多次调用，
        每次都 import rapidocr/os/Path 是纯浪费（import 有 sys.modules 缓存但
        os/Path 构建与目录拼接仍是多余的）。
        """
        if name in _pkg_models_cache:
            return _pkg_models_cache[name]
        p = None
        try:
            import rapidocr
            import os
            from pathlib import Path as _P
            p = _P(os.path.join(os.path.dirname(rapidocr.__file__), "models", name))
        except Exception:
            p = None
        _pkg_models_cache[name] = p
        return p

    def _local_models(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """返回本地 (det, rec, cls) 路径；det/rec 缺一即视为不可用（cls 可选）。

        档位按 self.model_type 找；该档位缺模型时**自动回退 tiny**
        （行为可预测 + 离线护栏：绝不联网下载，宁可降档也不崩）。
        结果按档位缓存（configure 后失效）。
        """
        tier = self._effective_tier()
        if tier != self.model_type and self.model_type in self._OCR_TIERS:
            logger.warning("[RapidOCR] 模型档位 %s 未安装，已回退 tiny", self.model_type)
        if tier not in self._models_cache:
            self._models_cache[tier] = self._find_models(tier)
        return self._models_cache[tier]

    def _build_params(self) -> dict:
        from rapidocr import EngineType, ModelType, OCRVersion

        # 用「实际生效档位」构造枚举：请求档位缺模型时会回落 tiny，
        # 引擎参数必须与本地实际模型一致，否则 rapidocr 按错档位预处理。
        try:
            model_type = ModelType(self._effective_tier())
        except ValueError:
            model_type = ModelType.TINY

        params = {
            "Global.log_level": "error",              # 屏蔽加载期 info 刷屏
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.ocr_version": OCRVersion.PPOCRV6,
            "Det.model_type": model_type,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.ocr_version": OCRVersion.PPOCRV6,
            "Rec.model_type": model_type,
        }
        det, rec, cls = self._local_models()
        if det and rec:                                # 本地模型优先，彻底零联网
            params["Det.model_path"] = det
            params["Rec.model_path"] = rec
            if cls:
                params["Cls.model_path"] = cls
        return params

    def _get_engine(self):
        # 引擎与「实际解析出的模型路径」绑定：apply_config/configure 改档位后
        # _models_cache 已失效，此处签名若变化必须重建，否则设置里切了模型档位
        # 仍静默沿用旧引擎（「配置改了引擎没重载」的经典 bug）。
        det, rec, cls = self._local_models()
        sig = (det, rec, cls)
        if self._engine is not None and self._engine_sig != sig:
            self._engine = None
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            try:
                from rapidocr import RapidOCR
            except ImportError as e:
                raise ImportError(
                    "未安装 rapidocr。请运行 setup.bat 或 "
                    "`pip install rapidocr onnxruntime`。"
                ) from e
            if not (det and rec):
                # 本地模型缺失：明确报错并引导安装，绝不静默联网下载权重
                raise RuntimeError(
                    "本地 OCR 模型缺失，已禁止自动联网下载。请运行 setup.bat 安装模型，"
                    "或检查 models/v6_tiny 目录是否包含 PP-OCRv6 的 det/rec onnx 文件。")
            try:
                self._engine = RapidOCR(params=self._build_params())
            except Exception as e:
                raise RuntimeError(f"RapidOCR 本地模型加载失败: {e}") from e
            self._engine_sig = sig
        return self._engine

    def warmup(self) -> bool:
        try:
            self._get_engine()
            return True
        except Exception as e:
            logger.warning("[RapidOCR 预热失败] %s", e)
            return False

    # ------------------------------------------------------------------
    # 识别
    # ------------------------------------------------------------------
    # 低置信离群项的硬阈值：低于即视为「鬼字/丢段」，触发升档 / 直接丢弃
    # 经验值：tiny 在中文新闻 OCR 上对正常行的最低也 >0.9，鬼字/错段 <0.6
    _OUTLIER_SCORE = 0.60

    def recognize(self, image) -> OcrResult:
        if image is None:
            return OcrResult(engine=self.name)
        orig_image = image
        scale = 1.0
        if self.preprocess:
            image, scale = self._preprocess_image(image)
        try:
            items = self._run(image)
        except Exception as e:
            logger.warning("[RapidOCR 识别失败] %s", e)
            return OcrResult(engine=self.name)

        # ---- 坐标系还原 ----
        # 预处理若放大过图片（小图 1.5×/2×），RapidOCR 返回的框是「放大图」坐标；
        # 必须缩回原图坐标系——否则所有下游（蒙版原位覆盖 / 行重排 / 段落判定 /
        # 结构化表格）都按放大坐标渲染，表现为行距过宽、内容溢出选区（3.4.20 实证）。
        items = self._rescale_items(items, scale)

        # ---- 离群鬼字拦截 ----
        # tiny 档对中文竖排/低对比度小图的「鬼字」（如「阳美重工…」）
        # 自信度往往 ~0.55，远低于正常行的 0.95+；按相对阈值过滤掉这种废项。
        # 注意：这里只做过滤，【不】再据此升档重试——升档要换档重建引擎
        # （模型重新加载），实测会让一次识别从 ~0.9s 涨到 ~3.8s，且置信度
        # 已经 0.99 时也会白跑。档位改由用户在设置里自己选。
        items, dropped = self._drop_outlier_items(items)
        if dropped:
            logger.info("[RapidOCR] 丢弃 %d 个低分鬼字项 (score<%.2f)",
                        dropped, self._OUTLIER_SCORE)

        result = self._build_result(orig_image, items)
        return result

    @staticmethod
    def _rescale_items(items, scale):
        """把识别项四点框坐标从放大图坐标系缩回原图坐标系（scale=1 时原样返回）。"""
        if not items or scale == 1.0:
            return items
        out = []
        for box, text, score in items:
            if box is not None:
                box = [[x / scale, y / scale] for x, y in box]
            out.append((box, text, score))
        return out

    def _drop_outlier_items(self, items):
        """过滤掉 score 明显低于正常水平的「鬼字」识别项。

        tiny 在中文新闻OCR上对正常行的 score 一般 ≥0.90，鬼字往往 ≤0.60。
        取正常中位数（去掉可能存在的低分离群后），差值超过 0.30 即判离群。
        返回 (filtered_items, dropped_count)。"""
        if not items:
            return items, 0
        scores = sorted(float(s) for _, _, s in items if s)
        if len(scores) < 3:                       # 项太少不传谣于离群判断
            return items, 0
        # 用上四分位（Q3）作「正常水平」，避免被少量低分离群拉偏
        q3 = scores[int(len(scores) * 0.75)]
        threshold = min(self._OUTLIER_SCORE, q3 - 0.30)
        if threshold <= 0:
            return items, 0
        kept = [it for it in items if float(it[2]) >= threshold]
        return kept, len(items) - len(kept)

    def model_availability(self) -> dict:
        """各档位本地模型是否齐备（UI 标注「该档未安装」用）。"""
        out = {}
        for t in self._OCR_TIERS:
            det, rec, _ = self._find_models(t)
            out[t] = det is not None and rec is not None
        return out

    @staticmethod
    def _line_geom(line_boxes, lines):
        """从每行的多个四点框还原整行几何量（x0/x1/y0/y1）。"""
        geom = []
        for boxes, text in zip(line_boxes, lines):
            if not boxes:
                geom.append({"x0": 0, "x1": 0, "y0": 0, "y1": 0, "text": text})
                continue
            xs = [p[0] for b in boxes for p in b]
            ys = [p[1] for b in boxes for p in b]
            geom.append({"x0": min(xs), "x1": max(xs),
                         "y0": min(ys), "y1": max(ys), "text": text})
        return geom

    @staticmethod
    def _detect_paragraphs(lines_geom, img_w, img_h):
        """段落层（B 法）：在 A 的行分组之上判定自然段落。

        判定（满足任一即新段）：
          1) 与上一行垂直间隙 ≥ 行距众数 × 1.8（段距明显大于行距）；
          2) 本行 left 相对段落左边界明显缩进（中文首行缩进 2 字符，段间无垂直间隙）。
        行距众数用相邻行间隙的「中位数」近似（双峰分布里中位数落在小峰=行距），
        比取最小值更抗离群（菜单密集处不会把阈值带偏）。
        """
        n = len(lines_geom)
        if n == 0:
            return []

        gaps = [lines_geom[i]["y0"] - lines_geom[i - 1]["y1"] for i in range(1, n)]
        pos = sorted(g for g in gaps if g > 0)
        if len(pos) >= 5:
            # 间隙双峰（行距小峰 + 段距大峰）：取下半部的中位数当行距，
            # 比全局中位数更稳——样本多时不会误取段落大间隙。
            lower = pos[: len(pos) // 2]
            modal = lower[len(lower) // 2]
        else:
            # 样本少（≤4 行）：直接取最小正间隙当行距（最紧凑即行距）。
            modal = pos[0] if pos else max(6.0, img_h * 0.02)
        para_gap = max(modal * 1.8, 6.0)          # 段距 = 行距的 1.8 倍以上
        indent_thr = max(8.0, img_w * 0.025)      # 首行缩进阈值（约 2 字符宽）

        para_ids = [0] * n
        para_left = lines_geom[0]["x0"]
        pid = 0
        for i in range(n):
            cur = lines_geom[i]
            if i > 0:
                prev = lines_geom[i - 1]
                gap = cur["y0"] - prev["y1"]
                new_para = (gap >= para_gap
                            or cur["x0"] - para_left >= indent_thr)
                # 跟随当前段左边界（回到段落左缘时更新参考）
                if not new_para and abs(cur["x0"] - para_left) <= indent_thr * 0.5:
                    para_left = cur["x0"]
                if new_para:
                    pid += 1
                    para_left = cur["x0"]
            para_ids[i] = pid
        return para_ids

    def _build_result(self, image, items) -> OcrResult:
        mode = self.paragraph_mode
        # 行分组层（A 法）：
        #   A / A+B → 数据驱动自适应阈值（稳健，推荐）
        #   B      → 退回旧固定阈值(12px)，以此隔离 A 的效果，便于对比
        if mode == "B":
            lines, line_boxes, line_items = self._group_by_lines(items, y_threshold=12)
        else:
            lines, line_boxes, line_items = self._group_by_lines(items)
        scores = [s for _, _, s in items if s]

        # ---- 段落层（B 法）：四角几何（行距众数 ×1.8 + 首行缩进）把行聚成段落 ----
        #   A     模式：不跑段落判定，text 为平铺 lines（仅修阅读顺序，无分段）
        #   B/A+B 模式：在行之上判定自然段落，text 用 \n\n 分段
        para_ids: list = []
        paragraphs: list = []
        if mode != "A" and line_boxes:
            img_w = getattr(image, "width", 0) or 0
            img_h = getattr(image, "height", 0) or 0
            line_geom = self._line_geom(line_boxes, lines)
            para_ids = self._detect_paragraphs(line_geom, img_w, img_h)
            # 按 para_id 聚合成段落（段内行以 \n 连接）
            buf, cur = [], -2
            for ln, pid in zip(lines, para_ids):
                if pid != cur:
                    if buf:
                        paragraphs.append("\n".join(buf))
                    buf, cur = [], pid
                buf.append(ln)
            if buf:
                paragraphs.append("\n".join(buf))

        text = "\n\n".join(paragraphs) if paragraphs else "\n".join(lines)
        # 结构化输出：用原始包围框几何重建 Markdown 表格（HushSnap 式，离线）
        if self.structured and items:
            try:
                from .structure import rebuild_markdown_from_items
                img_w = getattr(image, "width", None)
                md = rebuild_markdown_from_items(items, img_w)
                if md:
                    text = md
            except Exception as e:
                logger.warning("[RapidOCR 结构化失败，回退纯文字] %s", e)
        return OcrResult(
            text=text,
            lines=lines,
            boxes=[b for b, _, _ in items],
            line_boxes=line_boxes,
            line_items=line_items,
            paragraphs=paragraphs,
            para_ids=para_ids,
            engine=self.name,
            confidence=(sum(scores) / len(scores)) if scores else 0.0,
        )

    def _run(self, image):
        """调用引擎并把两种返回形态统一成 [(box, text, score), ...]。"""
        import numpy as np

        engine = self._get_engine()
        if image.mode != "RGB":                 # RapidOCR 的加载器按 3 通道处理
            image = image.convert("RGB")
        output = engine(np.array(image))

        if hasattr(output, "txts"):             # rapidocr 3.x
            txts = output.txts
            if not txts:
                return []
            boxes, scores = output.boxes, output.scores
            items = []
            for i, text in enumerate(txts):
                box = boxes[i].tolist() if boxes is not None else None
                score = float(scores[i]) if scores is not None else 0.0
                items.append((box, text, score))
            return items

        result, _ = output                      # rapidocr-onnxruntime 1.x
        return result or []

    @staticmethod
    def _group_by_lines(items, y_threshold: int = 0):
        """按几何聚类分行，行内按 X 排序，还原阅读顺序。

        相比早期固定阈值版（y_threshold=12）的增强：
          1. 自适应阈值 —— 按行高中位数比例（0.5×）算，大字体/小字体截图都稳，
             不再一刀切（截图小字时固定 12px 会把多行并成一行）。
          2. median 抗离群 —— 行心取 y 中位数而非均值，DB 框抖动/单个错框不拉偏行心。
          3. 断行合并 —— 垂直高度重叠（同逻辑行被 DB 切成上下两截）且间距小的
             相邻块并入同一行，缓解「识别断框」导致的拆行。

        返回 (lines, line_boxes, line_items)：
          lines      每行合并后的文本
          line_boxes 每行内各识别项的四点框（供几何重排）
          line_items 每行内各识别项文本（分列 / 转 Markdown 表格用）
        """
        parsed = []
        for box, text, _score in items:
            if box is None or text is None:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            ys_sorted = sorted(ys)
            # 表格截图里 RapidOCR 常把单元格边框读成 `|`/`Ⅰ`/`1`/`一` 等伪影
            # （苯溴马隆|25-50、|标准剂量、Img，每日1-3次）。统一用与结构化路径
            # 相同的清理逻辑剥掉行首伪影、把中段列分隔换成空格。
            clean = clean_text(text)
            parsed.append({
                "text": clean,
                "box": box,
                "cx": sum(xs) / len(xs),
                "cy": ys_sorted[len(ys_sorted) // 2],   # median，抗离群
                "top": min(ys), "bottom": max(ys),
                "height": max(ys) - min(ys),
            })
        if not parsed:
            return [], [], []

        # 自适应阈值：行高中位数 × 0.5，至少 2px；显式传入则优先（测试/兼容）
        heights = sorted(p["height"] for p in parsed if p["height"] > 0)
        med_h = heights[len(heights) // 2] if heights else 12.0

        # ===== 数据驱动的行间典型间距 =====
        #
        # 旧版用 `med_h * 0.5` 当分行阈值，对英文正文这种「行高 ≈ reach」
        # 的常见字号（med_h 65px、reach 97.8px、行距 92px）会把多行揉成一行
        # （连同 Phase 2 的「回并」一起触发，输出 1 个 row）。
        #
        # 同时它对混合字号/混合行距/用户缩放也很脆——`med_h` 是全局单一阈值，
        # 一张大图里既有标题（行高大）又有正文/脚注（行高小），任何全局策略都会
        # 在某一段上失配。
        #
        # 改为「看数据本身」：对所有按 top 排序后的相邻 top 求差，丢掉明显是
        # 「同行多盒」造成的 0/g 极小值（< 0.3 × min_h）后取最小者作为
        # 「本图最紧凑的真实行间 gap」。阈值取它的一半——既能稳定把紧排版
        # 分开，也容忍宽排版（gap 比最小者还大的相邻行当然也分开），对
        # 缩放/混合字号天然鲁棒（一切按比例缩放）。
        sorted_by_top = sorted(parsed, key=lambda x: (x["top"], x["cx"]))
        top_gaps = [sorted_by_top[i]["top"] - sorted_by_top[i - 1]["top"]
                    for i in range(1, len(sorted_by_top))]
        min_h = min((p["height"] for p in parsed if p["height"] > 0), default=12.0)
        nontrivial_thr = max(1.0, min_h * 0.3)        # 「远大于盒内字符间距」
        nontrivial_gaps = sorted(g for g in top_gaps if g >= nontrivial_thr)

        if y_threshold:
            thr = y_threshold                          # 测试/兼容优先
        elif nontrivial_gaps:
            min_gap = nontrivial_gaps[0]               # 本图最紧凑的真实行距
            thr = max(2.0, min_gap * 0.5)
        else:
            thr = max(2.0, med_h * 0.5)                # 单行兜底

        # 「被 DB 切成上下两截的同逻辑行」回并的间隙——DB 切片间隙通常
        # 只有 0-2px，远小于正常行间 gap。固定小阈值，跟 med_h 解耦：
        # med_h 大（如标题/正文混排）下 med_h*0.20 偏大，会把紧排正文
        # 的相邻行误并；DB 真实切片几乎都是 0-2px 的接触，2.5 够用。
        SPLIT_THR = 2.5

        # 1) 分行：相邻块 top 差 ≤ thr 即同一行。
        #    用「首块 top」作行的锚点，不再用累加式的 row_floor——
        #    旧版 `row_floor = max(row_floor, top + reach)` 会在连续
        #    短框累加下无限下推，把整段英文压成一行。这里一行锚定首块 top，
        #    后续块靠 top 差是否在阈值内判断是否同行，不再下推 line boundary。
        #    「高框」（备注列换行导致 bottom 探到下一行）top 仍在本行内，
        #    自然归入正确行（见 test_tall_remark_cell_does_not_merge_next_row）。
        rows = []
        for item in sorted_by_top:
            if not rows or item["top"] - rows[-1][0]["top"] > thr:
                rows.append([item])
            else:
                rows[-1].append(item)

        # 2) 断行合并：仅回并「被 DB 切成上下两截的同一逻辑行」。
        #    判定：上一行末块与下一行首块「不重叠(overlap<=0) 且 垂直间隙很小
        #    (0<=gap<SPLIT_THR)」——两截上下拼接、几乎相贴，这才是断框。
        #    用 SPLIT_THR（< 普通最小行距）防止 phase 1 在紧排版下漏分的两行
        #    被这里错误地并回去。
        if not rows:
            return [], [], []
        merged = [rows[0]]
        for row in rows[1:]:
            prev, cur = merged[-1][-1], row[0]
            overlap = min(prev["bottom"], cur["bottom"]) - max(prev["top"], cur["top"])
            gap = cur["top"] - prev["bottom"]
            if overlap <= 0 and 0 <= gap < SPLIT_THR:
                merged[-1].extend(row)              # 同逻辑行被切成两截，拼回
            else:
                merged.append(row)

        # 3) 行内按 X 排序输出
        out, out_boxes, out_items = [], [], []
        for row in merged:
            row.sort(key=lambda x: x["cx"])
            out.append(" ".join(x["text"] for x in row))
            out_boxes.append([x["box"] for x in row])
            out_items.append([x["text"] for x in row])
        return out, out_boxes, out_items

    # ------------------------------------------------------------------
    # 预处理
    # ------------------------------------------------------------------
    def _preprocess_image(self, image):
        """小图放大 + 按图片类型选择增强策略。失败时原样返回，绝不阻断识别。

        返回 (processed, scale)：scale 是几何放大倍数，调用方用于把识别框坐标
        缩回原图坐标系（识别框必须与原图 1:1，见 recognize 的坐标系还原）。
        """
        try:
            from PIL import Image, ImageEnhance, ImageFilter

            w, h = image.size
            processed = image
            scale = 1.0
            if w < 600 or h < 600:
                scale = 2 if min(w, h) < 300 else 1.5
                processed = processed.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

            if self._is_code_screenshot(processed):
                # 代码截图：只锐化。灰度+强对比会毁掉语法高亮里的符号
                if processed.mode == "RGBA":
                    processed = processed.convert("RGB")
                processed = processed.filter(ImageFilter.SHARPEN)
            else:
                if processed.mode != "L":
                    processed = processed.convert("L")
                processed = ImageEnhance.Contrast(processed).enhance(1.3)
                processed = processed.filter(ImageFilter.SHARPEN)
            return processed, scale
        except Exception:
            return image, 1.0

    @staticmethod
    def _is_code_screenshot(image) -> bool:
        """量化采样判断背景色占比：>40% 认为是代码/文档截图。"""
        try:
            small = image.resize((64, 64))
            pixels = list(small.getdata())
            if small.mode in ("RGB", "RGBA"):
                colors = [p[:3] for p in pixels]
            elif small.mode == "L":
                colors = [(p, p, p) for p in pixels]
            else:
                return False
            quantized = [(r // 32, g // 32, b // 32) for r, g, b in colors]
            most_common = Counter(quantized).most_common(1)[0][1]
            return (most_common / len(quantized)) > 0.4
        except Exception:
            return False
