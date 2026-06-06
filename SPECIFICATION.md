# BOSS直聘桌面客户端 — 界面解析与OCR识别系统

## 一、使用需求与目标环境

### 1.1 功能需求

| 编号 | 需求描述 | 优先级 |
|------|----------|--------|
| R1 | 自动定位 macOS 桌面上的 BOSS直聘 客户端窗口 | P0 |
| R2 | 按预设 ROI 区域精确截取窗口内各个 UI 面板 | P0 |
| R3 | 对截图执行中文 OCR 文字识别 | P0 |
| R4 | 将识别结果解析为结构化业务数据（JSON） | P0 |
| R5 | 窗口未找到时支持手动输入坐标回退 | P1 |
| R6 | OCR 置信度不足时自动切换预处理策略重试 | P1 |
| R7 | 主 OCR 引擎不可用时自动切换备用引擎 | P1 |
| R8 | 支持命令行参数灵活配置运行行为 | P2 |

### 1.2 目标环境

| 项目 | 要求 |
|------|------|
| 操作系统 | macOS 13+ (Ventura/Sonoma/Sequoia) |
| Python | 3.10+ |
| 桌面客户端 | BOSS直聘 macOS 桌面版（已安装并运行） |
| 辅助功能权限 | 不需要（使用 CGWindowList 只读 API） |
| 屏幕录制权限 | 不需要（使用 screencapture 原生命令） |

### 1.3 依赖清单

```
easyocr>=1.7.0              # 主OCR引擎（中文识别）
opencv-python>=4.8.0        # 图像预处理
Pillow>=10.0.0              # 图像读写与格式转换
pyobjc-framework-Quartz>=10.0 # macOS窗口枚举
numpy>=1.24.0               # 数组运算
```

---

## 二、系统架构

### 2.1 模块拓扑

```
┌──────────────────────────────────────────────────┐
│                    main.py                        │
│              (CLI入口 / 生命周期调度)              │
│    ┌──────────────────────────────────────┐      │
│    │         BossZhipinOCR                │      │
│    │        (核心调度控制器)                │      │
│    └────┬──────────┬──────────┬───────────┘      │
│         │          │          │                   │
│    ┌────▼────┐ ┌───▼────┐ ┌──▼────────┐         │
│    │ capture │ │  ocr   │ │  parser   │         │
│    │ 模块    │ │ 模块   │ │  模块     │         │
│    └────┬────┘ └───┬────┘ └──┬────────┘         │
│         │          │          │                   │
│    ┌────▼────┐ ┌───▼────┐ ┌──▼────────┐         │
│    │ config  │ │ config │ │  config   │         │
│    │.capture │ │ .ocr   │ │  (共享)   │         │
│    └─────────┘ └────────┘ └───────────┘         │
└──────────────────────────────────────────────────┘
```

### 2.2 数据流向

```
BOSS直聘窗口 ──截图──▶ PIL Image ──预处理──▶ numpy数组
                                                    │
                                               EasyOCR/PaddleOCR
                                                    │
                                                    ▼
  JSON输出 ◀── BossZhipinUIData ◀── DataParser ◀── OCRResult
```

---

## 三、完整生命周期：启动到退出

### 3.1 生命周期状态机

```
  [启动]
    │
    ▼
  ┌──────────┐    参数解析    ┌──────────────┐
  │ 初始化    │──────────────▶│ 列出区域定义   │──▶ exit(0)
  │ config   │               │ (--list-regions)│
  └────┬─────┘               └──────────────┘
       │
       ▼
  ┌──────────┐    引擎检查失败    ┌──────────────┐
  │ 环境检查  │─────────────────▶│ 打印错误提示   │──▶ exit(1)
  │ OCR引擎   │                  │ 退出          │
  └────┬─────┘                  └──────────────┘
       │ 引擎可用
       ▼
  ┌──────────┐    窗口未找到     ┌──────────────┐
  │ 窗口定位  │────────────────▶│ RuntimeError  │──▶ exit(1)
  │ (二阶段)  │                 │ 提示手动配置   │
  └────┬─────┘                 └──────────────┘
       │ 窗口已定位
       ▼
  ┌──────────┐    截图失败       ┌──────────────┐
  │ 区域截图  │────────────────▶│ RuntimeError  │──▶ exit(1)
  │ (可重试)  │                 │ 截图失败       │
  └────┬─────┘                 └──────────────┘
       │ 截图成功
       ▼
  ┌──────────┐    识别失败       ┌──────────────┐
  │ OCR识别   │────────────────▶│ 不阻断流程     │
  │ (三阶段)  │                 │ 输出空结果     │
  └────┬─────┘                 └──────────────┘
       │ 识别完成
       ▼
  ┌──────────┐    解析异常       ┌──────────────┐
  │ 结构化    │────────────────▶│ 单区域跳过     │
  │ 解析      │                 │ 继续其他区域   │
  └────┬─────┘                 └──────────────┘
       │ 解析完成
       ▼
  ┌──────────┐
  │ 输出结果  │──▶ stdout JSON / 文件写入
  └────┬─────┘
       │
       ▼
    exit(0)
```

### 3.2 CLI 入口函数调用链

```
main()
  │
  ├── build_argument_parser()          # 构建 argparse 参数解析器
  ├── parser.parse_args()              # 解析命令行参数
  │
  ├── [分支] --list-regions            # ──▶ 打印区域表 ──▶ return 0
  │
  ├── 解析 --region  → region_names    # "chat_list,chat_messages" → ["chat_list", "chat_messages"]
  ├── 解析 --bounds   → window_bounds  # "100,200,1200,800" → (100,200,1200,800)
  ├── 解析 --output   → output_path    # "result.json" → Path
  ├── 解析 --save-screenshots → screenshot_dir
  │
  ├── BossZhipinOCR()                  # 实例化控制器（组合三大模块）
  ├── ocr.available_engines            # 检查OCR引擎可用性
  │   └── [无可用引擎] ──▶ print 错误 ──▶ return 1
  │
  ├── ocr.run(regions, bounds, dir)    # 执行主流水线
  │   └── [RuntimeError] ──▶ print 错误 ──▶ return 1
  │
  ├── ui_data.to_json()                # 序列化结果
  ├── [output_path存在] → 写入文件
  ├── print(json_output)               # stdout 输出
  └── return 0
```

---

## 四、核心模块调用顺序与处理流程

### 4.1 `BossZhipinOCR.run()` — 主流水线（6阶段）

```
run(regions, window_bounds, save_screenshots)
│
├─ 阶段1: 窗口定位 ─────────────────────────────────────────
│   ├─ [window_bounds 手动指定] → WindowInfo("手动指定", x, y, w, h)
│   └─ [自动查找]
│       └─ WindowCaptureService.find_window()
│           ├─ WindowFinder.find_by_keywords(["BOSS直聘", ...])
│           │   ├─ 尝试 CGWindowListCopyWindowInfo (pyobjc)
│           │   │   └─ 遍历窗口列表, 按名称匹配关键词
│           │   └─ 尝试 AppleScript (回退)
│           │       └─ osascript 获取所有前台进程窗口, 正则解析
│           └─ [都失败] → 检查 FALLBACK_WINDOW_BOUNDS 配置
│               └─ [有配置] → WindowInfo("手动配置", ...)
│               └─ [无配置] → raise RuntimeError("未找到窗口")
│
├─ 阶段2: 区域解析 ─────────────────────────────────────────
│   └─ _resolve_regions(region_names)
│       ├─ [names is None] → 返回所有 DEFAULT_REGIONS
│       └─ [指定了名称] → 逐个查找 RegionDef, 忽略未知名称
│
├─ 阶段3: 区域截图 ─────────────────────────────────────────
│   └─ WindowCaptureService.capture_all_regions(window, regions)
│       └─ for each RegionDef:
│           ├─ ScreenCapture.compute_abs_rect(window, region)
│           │   └─ abs_x = window.x + int(window.width * region.left_ratio)
│           │       abs_y = window.y + int(window.height * region.top_ratio)
│           │       abs_w = int(window.width * region.width_ratio)
│           │       abs_h = int(window.height * region.height_ratio)
│           ├─ [w<=0 或 h<=0] → 跳过
│           └─ ScreenCapture.capture_region(x, y, w, h)
│               ├─ for attempt in range(retries+1):
│               │   ├─ screencapture -x -R x,y,w,h /tmp/boss_ocr/{name}.png
│               │   └─ [成功] → Image.open(path) → PIL Image
│               └─ [全部重试失败] → 回退:
│                   ├─ PIL.ImageGrab.grab(bbox)
│                   └─ pyautogui.screenshot(region)
│
├─ 阶段4: 保存截图（可选）──────────────────────────────────
│   └─ if save_screenshots:
│       └─ for each RegionScreenshot:
│           └─ image.save(save_screenshots / f"{region_name}.png")
│
├─ 阶段5: OCR识别 ─────────────────────────────────────────
│   └─ for each RegionScreenshot:
│       └─ OCREngine.recognize(image, region_name)
│           │
│           ├─ 尝试1: 完整预处理流水线
│           │   ├─ ImagePreprocessor.process(image)
│           │   │   └─ grayscale → CLAHE → adaptive_threshold
│           │   │       → denoise → sharpen → scale(2x)
│           │   ├─ EasyOCR Reader.readtext(img_array)
│           │   └─ [置信度 >= 0.5] → 返回结果
│           │
│           ├─ 尝试2: 关闭二值化 (防止文字被切断)
│           │   ├─ process(image, skip=["adaptive_threshold"])
│           │   ├─ EasyOCR 识别
│           │   └─ [置信度 >= 0.3] → 返回结果
│           │
│           ├─ 尝试3: 最小预处理 (仅灰度+放大)
│           │   ├─ process(image, steps=["grayscale", "scale"])
│           │   └─ EasyOCR 识别
│           │
│           └─ [EasyOCR 全部失败] → PaddleOCR 回退
│               └─ PaddleOCR.ocr(img_array, cls=False)
│
└─ 阶段6: 结构化解析 ────────────────────────────────────────
    └─ DataParser.parse(ocr_results)
        └─ for each region_name in ocr_results:
            ├─ _parse_chat_list()    → data.chat_list
            ├─ _parse_chat_messages()→ data.chat_messages
            ├─ _parse_job_detail()   → data.job_detail
            ├─ _parse_left_nav()     → data.left_nav
            ├─ _parse_top_bar()      → data.top_bar
            └─ _parse_chat_input()   → data.chat_input_text
```

---

## 五、各层异常捕获与处理机制

### 5.1 异常处理决策表

| 层级 | 异常场景 | 处理策略 | 是否阻断 |
|------|----------|----------|----------|
| **窗口查找** | pyobjc 未安装 | 自动切换到 AppleScript 方式 | 否 |
| **窗口查找** | AppleScript 超时/失败 | 返回 None, 检查手动回退配置 | 否 |
| **窗口查找** | 两种方式均失败且无手动配置 | RuntimeError("未找到窗口") | **是** |
| **截图** | screencapture 执行失败 | 重试 capture_retries 次, 每次间隔 retry_delay 秒 | 否 |
| **截图** | 重试耗尽 | 回退到 PIL.ImageGrab, 再回退到 pyautogui | 否 |
| **截图** | 所有截图方式均失败 | 跳过该区域, 继续下一个 | 否 |
| **截图** | 所有区域截图均失败 | RuntimeError("截图失败") | **是** |
| **OCR** | EasyOCR 未安装 | 自动切换到 PaddleOCR | 否 |
| **OCR** | PaddleOCR 也未安装 | available_engines 返回空列表 | 是(启动阶段) |
| **OCR** | GPU 不可用 | 自动切换 EasyOCR/PaddleOCR 到 CPU 模式 | 否 |
| **OCR** | 单次识别返回空结果 | 降低预处理强度重试(最多3次) | 否 |
| **OCR** | 所有重试均返回空结果 | 返回空的 OCRResult, 不阻断流程 | 否 |
| **OCR** | 单个预处理步骤异常 | 跳过该步骤, 继续后续步骤 | 否 |
| **解析** | 单个区域解析异常 | 捕获异常, 记录warning, 继续其他区域 | 否 |

### 5.2 异常处理代码模式

```python
# 模式A: 多级回退（窗口查找）
for method in (primary, fallback):
    try:
        result = method()
        if result:
            return result
    except Exception:
        continue
return None  # 所有方式用尽

# 模式B: 重试+回退（截图）
for attempt in range(retries + 1):
    try:
        return primary_method()
    except Exception:
        if attempt == retries:
            return fallback_method()
        time.sleep(delay)

# 模式C: 降级重试（OCR识别）
strategies = [full_pipeline, mild_pipeline, minimal_pipeline]
for strategy in strategies:
    result = strategy()
    if result and result.confidence >= threshold:
        return result
return best_among_attempts

# 模式D: 隔离故障（解析）
for region_name, result in ocr_results.items():
    try:
        parse_method(result, data)
    except Exception as exc:
        logger.warning("解析区域 %s 失败: %s", region_name, exc)
        # 不阻断其他区域的解析
```

---

## 六、输入输出处理流程

### 6.1 输入源

```
┌─────────────────────────────────────────────┐
│                 输入源                        │
├─────────────┬───────────────┬───────────────┤
│ 命令行参数   │ 配置文件       │ 屏幕内容       │
│             │               │               │
│ --region    │ config.py     │ BOSS直聘       │
│ --bounds    │ AppConfig     │ 窗口截图       │
│ --output    │               │               │
│ --save-     │ ROI区域定义    │               │
│   screenshots│ OCR参数       │               │
│             │ 预处理参数     │               │
└─────────────┴───────────────┴───────────────┘
```

### 6.2 输出格式

```json
{
  "timestamp": "2026-06-06T10:30:00",
  "window_title": "BOSS直聘",
  "chat_list": [
    {
      "name": "张HR",
      "company": "字节跳动科技有限公司",
      "job_title": "前端开发工程师",
      "last_message": "您好，方便聊一下吗",
      "unread_count": 2,
      "timestamp": "10:25",
      "raw_text": "..."
    }
  ],
  "chat_messages": [
    {
      "sender": "张HR",
      "content": "您好，您的简历我们看了很满意",
      "timestamp": "10:20",
      "is_self": false,
      "message_type": "text",
      "raw_text": "..."
    }
  ],
  "job_detail": {
    "title": "高级前端开发工程师",
    "company": "字节跳动科技有限公司",
    "salary_range": "25K-50K·16薪",
    "location": "北京",
    "experience": "3-5年",
    "education": "本科",
    "tags": ["React", "TypeScript", "前端架构"],
    "description": "负责核心业务前端架构设计...",
    "hr_name": "张HR",
    "hr_title": "招聘经理",
    "raw_text": "..."
  },
  "left_nav": [
    {"label": "消息", "unread_badge": 5, "is_active": true},
    {"label": "职位", "unread_badge": 0, "is_active": false}
  ],
  "top_bar": "BOSS直聘",
  "chat_input_text": ""
}
```

### 6.3 输出通道

| 通道 | 触发条件 | 格式 |
|------|----------|------|
| stdout | 始终 | 格式化 JSON 字符串 |
| 文件 | `--output result.json` | UTF-8 JSON |
| 截图文件 | `--save-screenshots ./dir/` | PNG 图像 |
| 日志 | 始终 | 时间戳 + 级别 + 模块名 + 消息 |

---

## 七、程序初始化配置

### 7.1 配置加载优先级

```
1. config.py 中 AppConfig 默认值 (最低)
       ↓ 被覆盖
2. 用户代码中 BossZhipinOCR(AppConfig(...)) 自定义实例
       ↓ 被覆盖
3. 命令行参数 --bounds --region (最高，仅当前运行生效)
```

### 7.2 默认配置清单

| 分类 | 配置项 | 默认值 | 说明 |
|------|--------|--------|------|
| 窗口查找 | `window_title_keywords` | `["BOSS直聘","Boss直聘","boss直聘","BOSS"]` | 匹配关键词列表 |
| 窗口查找 | `fallback_window_bounds` | `None` | 手动回退坐标 |
| 截图 | `method` | `"screencapture"` | 截图方式 |
| 截图 | `capture_retries` | `2` | 失败重试次数 |
| 截图 | `retry_delay` | `0.5` | 重试间隔(秒) |
| 截图 | `temp_dir` | `"/tmp/boss_ocr"` | 临时文件目录 |
| OCR | `engine` | `"easyocr"` | 主引擎 |
| OCR | `languages` | `["ch_sim","en"]` | 识别语言 |
| OCR | `gpu` | `True` | GPU加速 |
| OCR | `confidence_threshold` | `0.5` | 置信度阈值 |
| OCR | `low_confidence_threshold` | `0.3` | 低置信回退阈值 |
| 预处理 | `grayscale` | `True` | 灰度化 |
| 预处理 | `denoise` | `True` | 降噪 |
| 预处理 | `sharpen` | `True` | 锐化 |
| 预处理 | `adaptive_threshold` | `True` | 自适应二值化 |
| 预处理 | `scale_factor` | `2.0` | 放大倍数 |
| 预处理 | `clip_limit` | `2.0` | CLAHE对比度限制 |
| 预处理 | `tile_grid_size` | `(8, 8)` | CLAHE网格 |

### 7.3 自定义配置示例

```python
from boss_zhipin_ocr import BossZhipinOCR
from boss_zhipin_ocr.config import AppConfig, OCRConfig, PreprocessConfig

# 自定义配置：使用CPU、提高置信度阈值、关闭二值化
config = AppConfig()
config.ocr.gpu = False
config.ocr.confidence_threshold = 0.7
config.preprocess.adaptive_threshold = False
config.preprocess.scale_factor = 3.0  # 更高倍放大

ocr = BossZhipinOCR(config)
result = ocr.run()
```

---

## 八、错误码定义

| 退出码 | 含义 | 触发条件 |
|--------|------|----------|
| 0 | 成功 | 正常完成 |
| 1 | 运行失败 | 窗口未找到、截图失败、引擎不可用、参数格式错误 |

---

## 九、文件清单

```
boss_zhipin_ocr/
├── __init__.py          # 包入口，版本 1.0.0
├── config.py            # 全局配置：窗口/ROI/OCR/预处理/截图参数
├── window_capture.py    # 窗口查找 + 区域截图 (macOS)
├── ocr_engine.py        # 图像预处理 + EasyOCR/PaddleOCR 双引擎
├── data_parser.py       # OCR文本 → 业务实体结构化解析
├── main.py              # 主控制器 + CLI入口
└── requirements.txt     # Python依赖清单 (5项)
```
