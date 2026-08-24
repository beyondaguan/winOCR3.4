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

import threading
from collections import Counter
from typing import Optional, Tuple

from .base import OcrEngine
from .structure import clean_text
from ...core.paths import ocr_model_dir
from ...core.types import OcrResult

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
        self._lock = threading.Lock()
        self.preprocess: bool = True
        self.model_type: str = "tiny"
        self.structured: bool = False      # 几何重建表格/版面（离线，零额外模型）
        self.auto_upgrade: bool = True     # 低置信度自动升档重试
        self.upgrade_threshold: float = 0.5
        self._models_cache: dict = {}      # {tier: (det,rec,cls)}；配置变更后失效

    def configure(self, **kwargs) -> None:
        """接收配置注入；档位变了模型路径缓存必须失效。"""
        super().configure(**kwargs)
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
            print(f"[RapidOCR] 模型档位 {self.model_type} 未安装，已回退 tiny")
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
            det, rec, _ = self._local_models()
            if not (det and rec):
                # 本地模型缺失：明确报错并引导安装，绝不静默联网下载权重
                raise RuntimeError(
                    "本地 OCR 模型缺失，已禁止自动联网下载。请运行 setup.bat 安装模型，"
                    "或检查 models/v6_tiny 目录是否包含 PP-OCRv6 的 det/rec onnx 文件。")
            try:
                self._engine = RapidOCR(params=self._build_params())
            except Exception as e:
                raise RuntimeError(f"RapidOCR 本地模型加载失败: {e}") from e
        return self._engine

    def warmup(self) -> bool:
        try:
            self._get_engine()
            return True
        except Exception as e:
            print(f"[RapidOCR 预热失败] {e}")
            return False

    # ------------------------------------------------------------------
    # 识别
    # ------------------------------------------------------------------
    def recognize(self, image) -> OcrResult:
        if image is None:
            return OcrResult(engine=self.name)
        if self.preprocess:
            image = self._preprocess_image(image)
        try:
            items = self._run(image)
        except Exception as e:
            print(f"[RapidOCR 识别失败] {e}")
            return OcrResult(engine=self.name)

        result = self._build_result(image, items)

        # ---- 智能档位（P1-2）：低置信度 → 升一档重试，取更优结果 ----
        # 触发条件：启用 + 当前档非最高 + 有可用更高档 + 置信度低于阈值
        if self.auto_upgrade and result.confidence < self.upgrade_threshold:
            higher = self._next_available_tier(self._effective_tier())
            if higher:
                try:
                    items2 = self._run_with_tier(image, higher)
                    result2 = self._build_result(image, items2)
                    if result2.confidence > result.confidence:
                        print(f"[RapidOCR] 低置信度 {result.confidence:.2f} → "
                              f"升档 {higher} 重试，置信度 {result2.confidence:.2f}")
                        result = result2
                except Exception as e:
                    print(f"[RapidOCR] 升档重试失败（忽略，用原结果）: {e}")
        return result

    def _run_with_tier(self, image, tier: str):
        """用指定档位临时识别：换档 → 跑 → 还原档位与引擎缓存。

        引擎实例按档位惰性创建（_get_engine），换档需要重建；
        结束后恢复原档位状态，避免污染后续识别。
        """
        saved_type, saved_engine, saved_cache = self.model_type, self._engine, self._models_cache
        try:
            self.model_type = tier
            self._engine = None
            self._models_cache = {}
            return self._run(image)
        finally:
            self.model_type = saved_type
            self._engine = saved_engine
            self._models_cache = saved_cache

    def _next_available_tier(self, tier: str) -> Optional[str]:
        """当前档之后的第一个「本地有模型」的更高档；没有则 None。"""
        try:
            idx = self._OCR_TIERS.index(tier)
        except ValueError:
            return None
        for t in self._OCR_TIERS[idx + 1:]:
            det, rec, _ = self._find_models(t)
            if det is not None and rec is not None:
                return t
        return None

    def model_availability(self) -> dict:
        """各档位本地模型是否齐备（UI 标注「该档未安装」用）。"""
        out = {}
        for t in self._OCR_TIERS:
            det, rec, _ = self._find_models(t)
            out[t] = det is not None and rec is not None
        return out

    def _build_result(self, image, items) -> OcrResult:
        lines, line_boxes, line_items = self._group_by_lines(items)
        scores = [s for _, _, s in items if s]
        text = "\n".join(lines)
        # 结构化输出：用原始包围框几何重建 Markdown 表格（HushSnap 式，离线）
        if self.structured and items:
            try:
                from .structure import rebuild_markdown_from_items
                img_w = getattr(image, "width", None)
                md = rebuild_markdown_from_items(items, img_w)
                if md:
                    text = md
            except Exception as e:
                print(f"[RapidOCR 结构化失败，回退纯文字] {e}")
        return OcrResult(
            text=text,
            lines=lines,
            boxes=[b for b, _, _ in items],
            line_boxes=line_boxes,
            line_items=line_items,
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
        thr = y_threshold if y_threshold else max(2.0, med_h * 0.5)
        # 换行判定的小间隙（仅用于回并「被 DB 切成上下两截」的同逻辑行）
        GAP = max(2.0, med_h * 0.25)

        # 1) 分行：按「top 排序 + 合并带封顶」成行。
        #    关键：合并带（row_floor）只下探约 1.5 个行高，且**只有单行短框**
        #    才把合并带向下推进；备注列那种「换行后被检测成的高框」即使 bottom
        #    伸到下一行，也绝不推进合并带 → 下一行的 top 不会被裹进当前行，
        #    从而彻底避免「三行药物揉成一行」。
        #    （早期用 max(band_bottom, bottom) 无限下探，正是 winOCR.txt 把
        #     秋水仙碱/NSAIDs/糖皮质激素 合并成 1 行根因。）
        parsed.sort(key=lambda x: (x["top"], x["cx"]))
        reach = med_h * 1.5              # 合并带相对行首最多下探距离
        rows = []
        row_top = None
        row_floor = None
        for item in parsed:
            if row_top is None:
                rows.append([item])
                row_top, row_floor = item["top"], item["top"] + reach
            elif item["top"] <= row_floor + GAP:
                rows[-1].append(item)
                # 仅单行短框推进合并带；高框（换行备注）原地归入本行但不延伸带
                if item["height"] <= med_h * 1.6:
                    row_top = min(row_top, item["top"])
                    row_floor = max(row_floor, item["top"] + reach)
            else:
                rows.append([item])
                row_top, row_floor = item["top"], item["top"] + reach

        # 2) 断行合并：仅回并「被 DB 切成上下两截的同一逻辑行」。
        #    判定：上一行末块与下一行首块「不重叠(overlap<=0) 且 垂直间隙很小
        #    (0<=gap<thr)」——即两截上下拼接、几乎相贴，这才是断框；
        #    相邻两行（即便被高框压得重叠）因 overlap>0 不会被误并。
        merged = [rows[0]]
        for row in rows[1:]:
            prev, cur = merged[-1][-1], row[0]
            overlap = min(prev["bottom"], cur["bottom"]) - max(prev["top"], cur["top"])
            gap = cur["top"] - prev["bottom"]
            if overlap <= 0 and 0 <= gap < thr:
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
        """小图放大 + 按图片类型选择增强策略。失败时原样返回，绝不阻断识别。"""
        try:
            from PIL import Image, ImageEnhance, ImageFilter

            w, h = image.size
            processed = image
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
            return processed
        except Exception:
            return image

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
