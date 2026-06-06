"""
BOSS直聘桌面客户端 - 界面解析与OCR识别系统

核心模块:
    config:          配置管理（窗口标题、ROI区域、OCR参数）
    window_capture:  窗口定位与区域截图（macOS CGWindowList + screencapture）
    ocr_engine:      图像预处理与OCR识别（EasyOCR / PaddleOCR 双引擎）
    data_parser:     识别结果的结构化解析与格式化输出

典型用法:
    from boss_zhipin_ocr import BossZhipinOCR

    ocr = BossZhipinOCR()
    result = ocr.run()          # 自动定位窗口并识别所有区域
    result = ocr.run(region="chat_list")  # 仅识别指定区域
    print(result.to_json())
"""

__version__ = "1.0.0"
