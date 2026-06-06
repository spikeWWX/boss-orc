"""
窗口定位与区域截图模块

macOS 平台实现:
    1. 通过 CGWindowListCopyWindowInfo 枚举所有窗口
    2. 按标题关键词匹配 "BOSS直聘"
    3. 获取窗口全局坐标与尺寸
    4. 根据 ROI 相对比例计算绝对截图坐标
    5. 使用 screencapture 命令截取指定区域

回退策略:
    - pyobjc 不可用 → AppleScript 获取窗口列表
    - screencapture 失败 → pyautogui / PIL.ImageGrab
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image

from .config import AppConfig, CaptureConfig, RegionDef, default_config


# ─── 数据结构 ───────────────────────────────────────────────


@dataclass
class WindowInfo:
    """窗口信息"""
    title: str
    x: int
    y: int
    width: int
    height: int
    pid: int = 0

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)


@dataclass
class RegionScreenshot:
    """单张区域截图"""
    region_name: str
    region_def: RegionDef
    image: Image.Image
    abs_bounds: tuple[int, int, int, int]   # 绝对坐标 (x, y, w, h)
    file_path: Optional[Path] = None

    def save(self, path: Path) -> None:
        self.image.save(path)
        self.file_path = path


@dataclass
class CaptureResult:
    """一次截图会话的结果"""
    window: WindowInfo
    screenshots: list[RegionScreenshot]
    timestamp: float


# ─── 窗口查找 ───────────────────────────────────────────────


class WindowFinder:
    """macOS 窗口查找器 - 支持 CGWindowList 和 AppleScript 两种方式"""

    def __init__(self, config: CaptureConfig | None = None):
        self._cfg = config or default_config.capture

    def find_by_keywords(self, keywords: list[str]) -> Optional[WindowInfo]:
        """根据标题关键词查找窗口"""
        for method in (self._find_via_cgwindow, self._find_via_applescript):
            try:
                result = method(keywords)
                if result is not None:
                    return result
            except Exception:
                continue
        return None

    # ── CGWindowList 方式（首选） ──

    @staticmethod
    def _find_via_cgwindow(keywords: list[str]) -> Optional[WindowInfo]:
        """通过 macOS CGWindowList API 枚举窗口"""
        try:
            import Quartz
        except ImportError:
            raise RuntimeError("pyobjc-framework-Quartz 未安装")

        window_list = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )

        for window in window_list:
            name = window.get("kCGWindowName", "")
            bounds = window.get("kCGWindowBounds", {})
            if self._match_keywords(name, keywords):
                return WindowInfo(
                    title=name,
                    x=int(bounds.get("X", 0)),
                    y=int(bounds.get("Y", 0)),
                    width=int(bounds.get("Width", 0)),
                    height=int(bounds.get("Height", 0)),
                    pid=window.get("kCGWindowOwnerPID", 0),
                )
        return None

    # ── AppleScript 方式（回退） ──

    @staticmethod
    def _find_via_applescript(keywords: list[str]) -> Optional[WindowInfo]:
        """通过 AppleScript 获取窗口列表并匹配"""
        script = '''
        tell application "System Events"
            set windowList to {}
            repeat with proc in (every process whose background only is false)
                try
                    repeat with w in (every window of proc)
                        set end of windowList to {name:name of w, ¬
                            pos:position of w, size:size of w, pid:unix id of proc}
                    end repeat
                end try
            end repeat
            return windowList
        end tell
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

        # 解析 AppleScript 返回的列表格式 (name:xxx, pos:1,2, size:3,4)
        output = result.stdout.strip()
        if not output:
            return None

        return WindowFinder._parse_applescript_windows(output, keywords)

    @staticmethod
    def _parse_applescript_windows(
        output: str, keywords: list[str]
    ) -> Optional[WindowInfo]:
        """解析 AppleScript 返回的窗口列表"""
        import re
        # 匹配模式: name:标题, pos:横,纵, size:宽,高, pid:进程ID
        pattern = r"name:([^,]*), pos:(\d+), (\d+), size:(\d+), (\d+), pid:(\d+)"
        for match in re.finditer(pattern, output):
            name = match.group(1)
            if WindowFinder._match_keywords(name, keywords):
                return WindowInfo(
                    title=name,
                    x=int(match.group(2)),
                    y=int(match.group(3)),
                    width=int(match.group(4)),
                    height=int(match.group(5)),
                    pid=int(match.group(6)),
                )
        return None

    # ── 关键词匹配 ──

    @staticmethod
    def _match_keywords(name: str, keywords: list[str]) -> bool:
        if not name:
            return False
        return any(kw.lower() in name.lower() for kw in keywords)


# ─── 区域截取 ───────────────────────────────────────────────


class ScreenCapture:
    """macOS 区域截图器"""

    def __init__(self, config: CaptureConfig | None = None):
        self._cfg = config or default_config.capture
        os.makedirs(self._cfg.temp_dir, exist_ok=True)

    def capture_region(
        self, x: int, y: int, width: int, height: int, name: str = "capture"
    ) -> Optional[Image.Image]:
        """截取屏幕指定区域，返回 PIL Image"""
        # macOS 坐标系：左上角为原点，Y轴向下
        rect = f"{x},{y},{width},{height}"
        output_path = os.path.join(self._cfg.temp_dir, f"{name}.png")

        for attempt in range(self._cfg.capture_retries + 1):
            try:
                self._capture_via_screencapture(rect, output_path)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return Image.open(output_path)
            except Exception as e:
                if attempt == self._cfg.capture_retries:
                    return self._capture_fallback(x, y, width, height)
                time.sleep(self._cfg.retry_delay)

        return None

    @staticmethod
    def _capture_via_screencapture(rect: str, output_path: str) -> None:
        """通过 macOS screencapture CLI 截图"""
        subprocess.run(
            ["screencapture", "-x", "-R", rect, output_path],
            check=True, capture_output=True, timeout=5,
        )

    @staticmethod
    def _capture_fallback(
        x: int, y: int, width: int, height: int
    ) -> Optional[Image.Image]:
        """回退方案：PIL.ImageGrab（需额外的屏幕录制权限）"""
        try:
            from PIL import ImageGrab
            return ImageGrab.grab(bbox=(x, y, x + width, y + height))
        except ImportError:
            pass

        # 最后尝试 pyautogui
        try:
            import pyautogui
            screenshot = pyautogui.screenshot(
                region=(x, y, width, height)
            )
            return screenshot
        except ImportError:
            pass

        return None

    @staticmethod
    def compute_abs_rect(
        window: WindowInfo, region: RegionDef
    ) -> tuple[int, int, int, int]:
        """根据窗口bounds和区域相对比例计算绝对截图坐标"""
        abs_x = window.x + int(window.width * region.left_ratio)
        abs_y = window.y + int(window.height * region.top_ratio)
        abs_w = int(window.width * region.width_ratio)
        abs_h = int(window.height * region.height_ratio)
        return (abs_x, abs_y, abs_w, abs_h)


# ─── 统一捕获接口 ───────────────────────────────────────────


class WindowCaptureService:
    """
    窗口截图统一服务

    组合 WindowFinder + ScreenCapture，提供一站式的窗口定位与截图能力。
    """

    def __init__(self, config: AppConfig | None = None):
        self._cfg = config or default_config
        self._finder = WindowFinder(self._cfg.capture)
        self._capture = ScreenCapture(self._cfg.capture)

    def find_window(self) -> Optional[WindowInfo]:
        """查找 BOSS直聘 窗口"""
        window = self._finder.find_by_keywords(self._cfg.window_title_keywords)
        if window is None and self._cfg.fallback_window_bounds:
            fb = self._cfg.fallback_window_bounds
            window = WindowInfo(
                title="BOSS直聘 (手动配置)",
                x=fb[0], y=fb[1], width=fb[2], height=fb[3],
            )
        return window

    def capture_all_regions(
        self, window: WindowInfo, regions: list[RegionDef] | None = None
    ) -> CaptureResult:
        """截取窗口的所有配置区域"""
        regions = regions or self._cfg.regions
        screenshots: list[RegionScreenshot] = []

        for region in regions:
            abs_rect = self._capture.compute_abs_rect(window, region)
            x, y, w, h = abs_rect
            if w <= 0 or h <= 0:
                continue

            image = self._capture.capture_region(x, y, w, h, name=region.name)
            if image is None:
                continue

            screenshots.append(RegionScreenshot(
                region_name=region.name,
                region_def=region,
                image=image,
                abs_bounds=abs_rect,
            ))

        return CaptureResult(
            window=window,
            screenshots=screenshots,
            timestamp=time.time(),
        )

    def capture_single(
        self, window: WindowInfo, region: RegionDef
    ) -> Optional[RegionScreenshot]:
        """截取单个区域"""
        abs_rect = self._capture.compute_abs_rect(window, region)
        x, y, w, h = abs_rect
        if w <= 0 or h <= 0:
            return None

        image = self._capture.capture_region(x, y, w, h, name=region.name)
        if image is None:
            return None

        return RegionScreenshot(
            region_name=region.name,
            region_def=region,
            image=image,
            abs_bounds=abs_rect,
        )
