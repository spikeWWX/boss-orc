"""
配置管理模块

集中管理所有可调参数：窗口匹配规则、ROI区域定义、OCR参数、预处理策略。
可通过修改本文件或传入自定义字典来覆盖默认配置。
"""

from dataclasses import dataclass, field
from typing import Optional

# ─── 窗口匹配 ───────────────────────────────────────────────

WINDOW_TITLE_KEYWORDS = ["BOSS直聘", "Boss直聘", "boss直聘", "BOSS"]

# 若自动定位失败，使用手动配置的全局坐标 (x, y, width, height)
FALLBACK_WINDOW_BOUNDS: Optional[tuple[int, int, int, int]] = None

# ─── ROI 区域定义 ───────────────────────────────────────────
# 每个 ROI 以 (left_ratio, top_ratio, width_ratio, height_ratio) 表示窗口内相对比例
# 坐标原点为窗口左上角，(0,0) 左上，(1,1) 右下


@dataclass
class RegionDef:
    """单个截取区域的定义"""
    name: str                # 区域名称
    left_ratio: float        # 左边距占窗口宽度比例
    top_ratio: float         # 上边距占窗口高度比例
    width_ratio: float       # 区域宽度占窗口宽度比例
    height_ratio: float      # 区域高度占窗口高度比例
    description: str = ""    # 区域用途说明


# 预设的 BOSS直聘 界面区域（基于典型布局）
DEFAULT_REGIONS: list[RegionDef] = [
    RegionDef("left_nav",       0.00, 0.04, 0.06, 0.96, "左侧导航栏"),
    RegionDef("chat_list",      0.06, 0.04, 0.24, 0.96, "聊天/职位列表"),
    RegionDef("chat_messages",  0.30, 0.04, 0.42, 0.76, "聊天消息区域"),
    RegionDef("chat_input",     0.30, 0.80, 0.42, 0.20, "消息输入框"),
    RegionDef("job_detail",     0.72, 0.04, 0.28, 0.96, "职位详情面板"),
    RegionDef("top_bar",        0.00, 0.00, 1.00, 0.04, "顶部标题栏"),
]

# ─── OCR 参数 ───────────────────────────────────────────────


@dataclass
class OCRConfig:
    """OCR引擎配置"""
    engine: str = "easyocr"               # "easyocr" | "paddleocr"
    languages: list[str] = field(default_factory=lambda: ["ch_sim", "en"])
    gpu: bool = True                       # 是否使用GPU加速
    confidence_threshold: float = 0.5      # 置信度最低阈值 (0~1)
    low_confidence_threshold: float = 0.3  # 低置信度回退阈值
    max_retries: int = 2                   # OCR失败最大重试次数


# ─── 图像预处理参数 ─────────────────────────────────────────


@dataclass
class PreprocessConfig:
    """图像预处理配置"""
    grayscale: bool = True             # 是否转为灰度
    denoise: bool = True               # 是否降噪
    sharpen: bool = True               # 是否锐化
    adaptive_threshold: bool = True    # 是否自适应二值化
    scale_factor: float = 2.0         # 放大倍数（提升小字识别率）
    clip_limit: float = 2.0           # CLAHE 对比度限制
    tile_grid_size: tuple = (8, 8)    # CLAHE 网格大小


# ─── 截图参数 ───────────────────────────────────────────────


@dataclass
class CaptureConfig:
    """截图捕获配置"""
    method: str = "screencapture"       # "screencapture" | "pyautogui" | "quartz"
    capture_retries: int = 2            # 截图失败重试次数
    retry_delay: float = 0.5            # 重试间隔（秒）
    temp_dir: str = "/tmp/boss_ocr"     # 临时截图存放目录


# ─── 全局配置聚合 ───────────────────────────────────────────


@dataclass
class AppConfig:
    """应用全局配置"""
    window_title_keywords: list[str] = field(
        default_factory=lambda: WINDOW_TITLE_KEYWORDS
    )
    fallback_window_bounds: Optional[tuple[int, int, int, int]] = FALLBACK_WINDOW_BOUNDS
    regions: list[RegionDef] = field(default_factory=lambda: DEFAULT_REGIONS)
    ocr: OCRConfig = field(default_factory=OCRConfig)
    preprocess: PreprocessConfig = field(default_factory=PreprocessConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)


# 可被外部覆盖的默认配置实例
default_config = AppConfig()
