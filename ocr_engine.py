"""
OCR 识别引擎模块

双引擎架构:
    - 主引擎: EasyOCR (中文识别率高，GPU 加速，安装简便)
    - 备引擎: PaddleOCR (需额外安装 paddlepaddle，准确率更高但依赖更重)

图像预处理流水线（可配置开关）:
    灰度化 → CLAHE 对比度增强 → 自适应阈值二值化 → 降噪 → 锐化 → 放大

异常恢复:
    - 首次识别置信度低 → 关闭二值化重试
    - EasyOCR 不可用 → 自动切换到 PaddleOCR
    - GPU 不可用 → 自动切换到 CPU
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image

from .config import AppConfig, OCRConfig, PreprocessConfig, default_config

logger = logging.getLogger(__name__)


# ─── OCR 结果数据结构 ────────────────────────────────────────


@dataclass
class OCRTextBlock:
    """单个识别的文本块"""
    text: str                          # 识别出的文字
    confidence: float                  # 置信度 0~1
    bbox: tuple[int, int, int, int]    # 边界框 (x1, y1, x2, y2)，相对于截图
    line_index: int = 0                # 行序号

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= 0.7

    @property
    def is_medium_confidence(self) -> bool:
        return 0.5 <= self.confidence < 0.7

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < 0.5


@dataclass
class OCRResult:
    """单张图像的完整 OCR 结果"""
    region_name: str
    text_blocks: list[OCRTextBlock]
    full_text: str                      # 合并所有文本块
    avg_confidence: float
    engine_used: str                    # "easyocr" | "paddleocr"
    preprocess_steps: list[str] = field(default_factory=list)

    @property
    def text_lines(self) -> list[str]:
        """按行返回文本"""
        return [b.text for b in sorted(self.text_blocks, key=lambda b: b.line_index)]

    def filter_by_confidence(self, threshold: float) -> list[OCRTextBlock]:
        """按置信度过滤文本块"""
        return [b for b in self.text_blocks if b.confidence >= threshold]


# ─── 图像预处理 ─────────────────────────────────────────────


class ImagePreprocessor:
    """图像预处理流水线 - 各步骤可独立开关"""

    def __init__(self, config: PreprocessConfig | None = None):
        self._cfg = config or default_config.preprocess

    def process(self, image: Image.Image, steps: list[str] | None = None) -> np.ndarray:
        """
        执行预处理流水线。

        若传入 steps 列表则仅运行指定步骤（用于回退重试时调整参数）。
        默认按配置运行所有已启用的步骤。
        """
        img_array = np.array(image)
        applied: list[str] = []

        step_map = {
            "grayscale":           self._grayscale,
            "clahe":               self._clahe,
            "adaptive_threshold":  self._adaptive_threshold,
            "denoise":             self._denoise,
            "sharpen":             self._sharpen,
            "scale":               self._scale,
        }

        # 若未指定步骤列表，按顺序执行所有已启用的步骤
        if steps is None:
            steps = [
                "grayscale", "clahe", "adaptive_threshold",
                "denoise", "sharpen", "scale",
            ]

        for step_name in steps:
            if step_name not in step_map:
                continue
            # 检查该步骤是否在配置中启用
            if not getattr(self._cfg, step_name, True):
                continue
            try:
                img_array = step_map[step_name](img_array)
                applied.append(step_name)
            except Exception:
                logger.warning("预处理步骤 %s 执行失败，跳过", step_name)

        return img_array, applied

    def _grayscale(self, img: np.ndarray) -> np.ndarray:
        """转灰度"""
        if len(img.shape) == 3 and img.shape[2] == 3:
            import cv2
            return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        if len(img.shape) == 3 and img.shape[2] == 4:
            import cv2
            return cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
        return img

    def _clahe(self, img: np.ndarray) -> np.ndarray:
        """CLAHE 自适应直方图均衡化 - 增强对比度"""
        import cv2
        gray = img if len(img.shape) == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        clahe = cv2.createCLAHE(
            clipLimit=self._cfg.clip_limit,
            tileGridSize=self._cfg.tile_grid_size,
        )
        return clahe.apply(gray)

    def _adaptive_threshold(self, img: np.ndarray) -> np.ndarray:
        """自适应阈值二值化 - 将文字与背景分离"""
        import cv2
        gray = img if len(img.shape) == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        return cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=11,
            C=2,
        )

    def _denoise(self, img: np.ndarray) -> np.ndarray:
        """非局部均值降噪"""
        import cv2
        gray = img if len(img.shape) == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        return cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)

    def _sharpen(self, img: np.ndarray) -> np.ndarray:
        """拉普拉斯锐化"""
        import cv2
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        return cv2.filter2D(img, -1, kernel)

    def _scale(self, img: np.ndarray) -> np.ndarray:
        """按比例放大图像（提升小字识别率）"""
        import cv2
        sf = self._cfg.scale_factor
        if sf <= 1.0:
            return img
        h, w = img.shape[:2]
        return cv2.resize(img, (int(w * sf), int(h * sf)), interpolation=cv2.INTER_CUBIC)


# ─── OCR 引擎 ────────────────────────────────────────────────


class OCREngine:
    """
    OCR 引擎封装

    支持 EasyOCR 和 PaddleOCR 两种后端，自动选择可用的引擎。
    """

    def __init__(self, config: OCRConfig | None = None):
        self._cfg = config or default_config.ocr
        self._preprocessor = ImagePreprocessor()
        self._reader = None         # EasyOCR Reader 实例
        self._paddle_ocr = None     # PaddleOCR 实例
        self._engine = None         # 当前使用的引擎名

    # ── 公开接口 ──

    def recognize(
        self,
        image: Image.Image,
        region_name: str = "unknown",
    ) -> OCRResult:
        """对单张图像执行 OCR 识别（含预处理和回退重试）"""
        # 首次尝试：完整预处理流水线
        result = self._recognize_with_preprocess(image, region_name, retry_stage=0)
        if result is not None and result.avg_confidence >= self._cfg.confidence_threshold:
            return result

        # 回退 1：关闭二值化（防止文字被切断）
        logger.info("置信度不足 (%.2f)，关闭二值化重试", result.avg_confidence if result else 0)
        result2 = self._recognize_with_preprocess(
            image, region_name,
            skip_steps=["adaptive_threshold"],
            retry_stage=1,
        )
        if result2 is not None and result2.avg_confidence >= self._cfg.low_confidence_threshold:
            return result2

        # 回退 2：仅灰度 + 放大，不做任何过滤（保留最多文字信息）
        logger.info("继续回退：仅灰度+放大")
        result3 = self._recognize_with_preprocess(
            image, region_name,
            skip_steps=["clahe", "adaptive_threshold", "denoise", "sharpen"],
            retry_stage=2,
        )
        if result3 is not None:
            return result3

        # 返回最佳尝试结果
        candidates = [r for r in (result, result2, result3) if r is not None]
        return max(candidates, key=lambda r: r.avg_confidence)

    # ── 内部方法 ──

    def _recognize_with_preprocess(
        self,
        image: Image.Image,
        region_name: str,
        skip_steps: list[str] | None = None,
        retry_stage: int = 0,
    ) -> Optional[OCRResult]:
        """执行预处理 + OCR 识别"""
        all_steps = ["grayscale", "clahe", "adaptive_threshold", "denoise", "sharpen", "scale"]
        steps = [s for s in all_steps if s not in (skip_steps or [])]

        processed, applied = self._preprocessor.process(image, steps=steps)

        # 如果预处理后是单通道，EasyOCR 需要转回三通道
        if len(processed.shape) == 2:
            import cv2
            processed = cv2.cvtColor(processed, cv2.COLOR_GRAY2RGB)

        # 确保是 PIL Image
        if isinstance(processed, np.ndarray):
            pil_image = Image.fromarray(processed)
        else:
            pil_image = processed

        # 尝试各引擎
        raw_results = self._run_easyocr(pil_image)
        engine = "easyocr"

        if raw_results is None:
            raw_results = self._run_paddleocr(pil_image)
            engine = "paddleocr"

        if raw_results is None:
            return None

        # 组装结果
        text_blocks: list[OCRTextBlock] = []
        for i, item in enumerate(raw_results):
            if engine == "easyocr":
                # EasyOCR 格式: (bbox, text, confidence)
                bbox_raw, text, conf = item
                x1 = int(bbox_raw[0][0])
                y1 = int(bbox_raw[0][1])
                x2 = int(bbox_raw[2][0])
                y2 = int(bbox_raw[2][1])
            else:  # paddleocr
                # PaddleOCR 格式: [[bbox], (text, confidence)]
                bbox_raw, (text, conf) = item
                x1, y1 = int(bbox_raw[0][0]), int(bbox_raw[0][1])
                x2, y2 = int(bbox_raw[2][0]), int(bbox_raw[2][1])

            if conf >= self._cfg.low_confidence_threshold:
                text_blocks.append(OCRTextBlock(
                    text=text,
                    confidence=float(conf),
                    bbox=(x1, y1, x2, y2),
                    line_index=i,
                ))

        if not text_blocks:
            return None

        full_text = " ".join(b.text for b in text_blocks)
        avg_conf = sum(b.confidence for b in text_blocks) / len(text_blocks)

        return OCRResult(
            region_name=region_name,
            text_blocks=text_blocks,
            full_text=full_text,
            avg_confidence=avg_conf,
            engine_used=engine,
            preprocess_steps=applied,
        )

    def _run_easyocr(self, image) -> list | None:
        """执行 EasyOCR 识别"""
        try:
            if self._reader is None:
                import easyocr
                use_gpu = self._cfg.gpu
                try:
                    self._reader = easyocr.Reader(
                        self._cfg.languages, gpu=use_gpu
                    )
                except Exception:
                    logger.warning("EasyOCR GPU 不可用，切换到 CPU 模式")
                    self._reader = easyocr.Reader(
                        self._cfg.languages, gpu=False
                    )

            import numpy as np
            img_array = np.array(image)
            return self._reader.readtext(img_array)
        except ImportError:
            logger.debug("EasyOCR 未安装")
            return None
        except Exception as e:
            logger.warning("EasyOCR 识别失败: %s", e)
            return None

    def _run_paddleocr(self, image) -> list | None:
        """执行 PaddleOCR 识别（备选引擎）"""
        try:
            if self._paddle_ocr is None:
                from paddleocr import PaddleOCR
                self._paddle_ocr = PaddleOCR(
                    lang="ch",
                    use_gpu=self._cfg.gpu,
                    show_log=False,
                )

            import numpy as np
            img_array = np.array(image)
            result = self._paddle_ocr.ocr(img_array, cls=False)
            if result and result[0]:
                return result[0]
            return None
        except ImportError:
            logger.debug("PaddleOCR 未安装")
            return None
        except Exception as e:
            logger.warning("PaddleOCR 识别失败: %s", e)
            return None

    @property
    def available_engines(self) -> list[str]:
        """返回当前可用的 OCR 引擎列表"""
        engines = []
        try:
            import easyocr  # noqa: F401
            engines.append("easyocr")
        except ImportError:
            pass
        try:
            import paddleocr  # noqa: F401
            engines.append("paddleocr")
        except ImportError:
            pass
        return engines
