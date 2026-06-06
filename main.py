#!/usr/bin/env python3
"""
BOSS直聘桌面客户端 - 界面解析与OCR识别 主入口

用法:
    # 自动定位窗口，识别所有预定义区域
    python -m boss_zhipin_ocr.main

    # 仅识别指定区域
    python -m boss_zhipin_ocr.main --region chat_list,chat_messages

    # 手动指定窗口坐标（跳过自动查找）
    python -m boss_zhipin_ocr.main --bounds 100,200,1200,800

    # 输出到文件
    python -m boss_zhipin_ocr.main --output result.json

    # 保存截图到指定目录
    python -m boss_zhipin_ocr.main --save-screenshots ./captures/

    # 列出可用区域
    python -m boss_zhipin_ocr.main --list-regions
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from .config import AppConfig, default_config, RegionDef
from .window_capture import WindowCaptureService, WindowInfo
from .ocr_engine import OCREngine
from .data_parser import DataParser, BossZhipinUIData

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("boss_ocr")


# ─── 核心调度器 ─────────────────────────────────────────────


class BossZhipinOCR:
    """
    BOSS直聘 OCR 识别主控制器

    组合三大模块（截图、OCR、解析）完成端到端识别流水线。
    """

    def __init__(self, config: AppConfig | None = None):
        self._cfg = config or default_config
        self._capture = WindowCaptureService(self._cfg)
        self._ocr = OCREngine(self._cfg.ocr)
        self._parser = DataParser(self._cfg)

    @property
    def available_engines(self) -> list[str]:
        """返回可用的 OCR 引擎"""
        return self._ocr.available_engines

    def run(
        self,
        regions: list[str] | None = None,
        window_bounds: tuple[int, int, int, int] | None = None,
        save_screenshots: Optional[Path] = None,
    ) -> BossZhipinUIData:
        """
        执行完整的识别流水线。

        Args:
            regions: 要识别的区域名称列表，None 表示所有区域
            window_bounds: 手动指定窗口坐标 (x, y, w, h)
            save_screenshots: 截图保存目录（可选）

        Returns:
            BossZhipinUIData: 结构化 UI 数据

        Raises:
            RuntimeError: 窗口未找到
            RuntimeError: 没有可用的 OCR 引擎
        """
        # 1. 查找窗口
        if window_bounds:
            window = WindowInfo(
                title="BOSS直聘 (手动指定)",
                x=window_bounds[0], y=window_bounds[1],
                width=window_bounds[2], height=window_bounds[3],
            )
            logger.info("使用手动指定的窗口坐标: %s", window_bounds)
        else:
            window = self._capture.find_window()
            if window is None:
                raise RuntimeError(
                    "未找到 BOSS直聘 窗口。请确保客户端已启动，"
                    "或使用 --bounds 参数手动指定窗口坐标。"
                )
            logger.info(
                "找到窗口: '%s' 位置=(%d,%d) 尺寸=%dx%d",
                window.title, window.x, window.y, window.width, window.height,
            )

        # 2. 确定要截图的区域
        target_regions = self._resolve_regions(regions)
        if not target_regions:
            raise RuntimeError("没有可截图的区域")

        # 3. 截图
        logger.info("开始截图，共 %d 个区域 ...", len(target_regions))
        capture_result = self._capture.capture_all_regions(window, target_regions)

        if not capture_result.screenshots:
            raise RuntimeError("截图失败：没有成功截取任何区域")

        logger.info("成功截取 %d 个区域", len(capture_result.screenshots))

        # 4. 保存截图（可选）
        if save_screenshots:
            save_screenshots.mkdir(parents=True, exist_ok=True)
            for ss in capture_result.screenshots:
                path = save_screenshots / f"{ss.region_name}.png"
                ss.save(path)
                logger.info("截图已保存: %s", path)

        # 5. OCR 识别
        if not self._ocr.available_engines:
            raise RuntimeError(
                "没有可用的 OCR 引擎。请安装 easyocr: pip install easyocr"
            )

        logger.info("开始 OCR 识别 ...")
        ocr_results: dict[str, any] = {}
        for ss in capture_result.screenshots:
            logger.info("  识别区域: %s ...", ss.region_name)
            result = self._ocr.recognize(ss.image, region_name=ss.region_name)
            ocr_results[ss.region_name] = result
            logger.info(
                "    完成: %d 个文本块, 平均置信度=%.2f, 引擎=%s",
                len(result.text_blocks),
                result.avg_confidence,
                result.engine_used,
            )

        # 6. 结构化解析
        logger.info("开始结构化解析 ...")
        ui_data = self._parser.parse(ocr_results)
        ui_data.window_title = window.title

        logger.info(
            "解析完成: chat_list=%d, messages=%d, job_detail=%s, nav=%d",
            len(ui_data.chat_list),
            len(ui_data.chat_messages),
            "yes" if ui_data.job_detail else "no",
            len(ui_data.left_nav),
        )

        return ui_data

    def run_single_region(
        self,
        region_name: str,
        window_bounds: tuple[int, int, int, int] | None = None,
    ) -> Optional[str]:
        """快速识别单个区域，返回合并文本"""
        window = (
            WindowInfo(title="manual", x=window_bounds[0], y=window_bounds[1],
                        width=window_bounds[2], height=window_bounds[3])
            if window_bounds
            else self._capture.find_window()
        )
        if window is None:
            logger.error("窗口未找到")
            return None

        region = self._find_region_def(region_name)
        if region is None:
            logger.error("未知区域: %s", region_name)
            return None

        ss = self._capture.capture_single(window, region)
        if ss is None:
            logger.error("截图失败: %s", region_name)
            return None

        result = self._ocr.recognize(ss.image, region_name=region_name)
        return result.full_text

    def _resolve_regions(self, names: list[str] | None) -> list[RegionDef]:
        """根据名称列表解析 RegionDef"""
        all_regions = self._cfg.regions
        if names is None:
            return list(all_regions)

        resolved = []
        for name in names:
            rd = self._find_region_def(name)
            if rd:
                resolved.append(rd)
            else:
                logger.warning("忽略未知区域: %s", name)
        return resolved

    def _find_region_def(self, name: str) -> Optional[RegionDef]:
        for r in self._cfg.regions:
            if r.name == name:
                return r
        return None


# ─── CLI 入口 ───────────────────────────────────────────────


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BOSS直聘桌面客户端 - 界面解析与OCR识别",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                                    # 识别所有区域
  %(prog)s --region chat_list,chat_messages   # 仅识别指定区域
  %(prog)s --bounds 100,200,1200,800          # 手动指定窗口坐标
  %(prog)s --output result.json               # 输出到文件
  %(prog)s --save-screenshots ./captures/     # 保存截图
  %(prog)s --list-regions                      # 列出可用区域
        """,
    )
    parser.add_argument(
        "-r", "--region",
        help="要识别的区域名称，逗号分隔（默认: 全部）",
    )
    parser.add_argument(
        "-b", "--bounds",
        help="手动指定窗口坐标 x,y,w,h（跳过自动查找）",
    )
    parser.add_argument(
        "-o", "--output",
        help="将结构化结果输出到 JSON 文件",
    )
    parser.add_argument(
        "-s", "--save-screenshots",
        help="保存截图到指定目录",
    )
    parser.add_argument(
        "--list-regions",
        action="store_true",
        help="列出所有预设区域并退出",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    # --list-regions: 列出区域定义
    if args.list_regions:
        print("预设区域定义（相对窗口比例）：")
        print(f"{'名称':<18} {'left':>6} {'top':>6} {'width':>6} {'height':>6}  说明")
        print("-" * 65)
        for r in default_config.regions:
            print(
                f"  {r.name:<16} {r.left_ratio:>6.2f} {r.top_ratio:>6.2f} "
                f"{r.width_ratio:>6.2f} {r.height_ratio:>6.2f}  {r.description}"
            )
        return 0

    # 解析参数
    region_names = None
    if args.region:
        region_names = [n.strip() for n in args.region.split(",") if n.strip()]

    window_bounds = None
    if args.bounds:
        parts = args.bounds.split(",")
        if len(parts) != 4:
            print("错误: --bounds 参数格式为 x,y,w,h", file=sys.stderr)
            return 1
        try:
            window_bounds = tuple(int(p.strip()) for p in parts)
        except ValueError:
            print("错误: --bounds 参数必须为整数", file=sys.stderr)
            return 1

    output_path = Path(args.output) if args.output else None
    screenshot_dir = Path(args.save_screenshots) if args.save_screenshots else None

    # 执行识别
    ocr = BossZhipinOCR()

    # 检查引擎可用性
    engines = ocr.available_engines
    if not engines:
        print("错误: 没有可用的 OCR 引擎。请安装：", file=sys.stderr)
        print("  pip install easyocr", file=sys.stderr)
        print("  或: pip install paddleocr paddlepaddle", file=sys.stderr)
        return 1
    logger.info("可用 OCR 引擎: %s", ", ".join(engines))

    try:
        ui_data = ocr.run(
            regions=region_names,
            window_bounds=window_bounds,
            save_screenshots=screenshot_dir,
        )
    except RuntimeError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    # 输出结果
    json_output = ui_data.to_json()

    if output_path:
        output_path.write_text(json_output, encoding="utf-8")
        logger.info("结果已写入: %s", output_path)

    print(json_output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
