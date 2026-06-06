"""
数据结构化解析模块

将 OCR 原始识别结果按 BOSS直聘 业务语义解析为结构化数据：
    - ChatListEntry:      聊天/职位列表项
    - ChatMessage:         单条聊天消息
    - JobDetail:           职位详情信息
    - LeftNavItem:         左侧导航项

支持 JSON 序列化输出，便于下游消费（数据分析、自动化流程等）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

from .config import AppConfig, default_config
from .ocr_engine import OCRResult, OCRTextBlock


# ─── 业务实体 ────────────────────────────────────────────────


@dataclass
class ChatListEntry:
    """聊天列表中的单个条目"""
    name: str = ""                    # 对方显示名（HR/求职者）
    company: str = ""                 # 公司名
    job_title: str = ""               # 职位名
    last_message: str = ""            # 最后一条消息摘要
    unread_count: int = 0             # 未读消息数
    timestamp: str = ""               # 最后活跃时间
    raw_text: str = ""                # 原始OCR文本（用于校验）


@dataclass
class ChatMessage:
    """单条聊天消息"""
    sender: str = ""                  # 发送者
    content: str = ""                 # 消息内容
    timestamp: str = ""               # 发送时间
    is_self: bool = False             # 是否为本人发送
    message_type: str = "text"        # text | image | file | system
    raw_text: str = ""


@dataclass
class JobDetail:
    """职位详情"""
    title: str = ""                   # 职位名称
    company: str = ""                 # 公司名称
    salary_range: str = ""            # 薪资范围
    location: str = ""                # 工作地点
    experience: str = ""              # 经验要求
    education: str = ""               # 学历要求
    tags: list[str] = field(default_factory=list)      # 职位标签
    description: str = ""             # 职位描述
    hr_name: str = ""                 # HR 姓名
    hr_title: str = ""                # HR 职位
    raw_text: str = ""


@dataclass
class LeftNavItem:
    """左侧导航项"""
    label: str = ""                   # 导航标签
    unread_badge: int = 0             # 消息徽标数
    is_active: bool = False           # 是否为当前激活项


# ─── 聚合结果 ───────────────────────────────────────────────


@dataclass
class BossZhipinUIData:
    """一次解析的完整 UI 数据"""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    window_title: str = ""
    chat_list: list[ChatListEntry] = field(default_factory=list)
    chat_messages: list[ChatMessage] = field(default_factory=list)
    job_detail: Optional[JobDetail] = None
    left_nav: list[LeftNavItem] = field(default_factory=list)
    top_bar: str = ""                   # 顶部标题栏文本
    chat_input_text: str = ""           # 输入框当前文本
    _raw_ocr_results: dict[str, OCRResult] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（不含原始 OCR 结果）"""
        return {
            "timestamp": self.timestamp,
            "window_title": self.window_title,
            "chat_list": [asdict(e) for e in self.chat_list],
            "chat_messages": [asdict(m) for m in self.chat_messages],
            "job_detail": asdict(self.job_detail) if self.job_detail else None,
            "left_nav": [asdict(n) for n in self.left_nav],
            "top_bar": self.top_bar,
            "chat_input_text": self.chat_input_text,
        }

    def to_json(self, indent: int = 2, ensure_ascii: bool = False) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=ensure_ascii)


# ─── 解析器 ─────────────────────────────────────────────────


class DataParser:
    """
    OCR 结果到业务实体的解析器

    针对 BOSS直聘 客户端各 UI 区域的特征进行结构化提取。
    每个区域的解析逻辑独立封装，便于维护和扩展。
    """

    # BOSS直聘 常见的高频模式
    SALARY_PATTERN = re.compile(
        r"(\d+[Kk]?\s*[-~—]\s*\d+[Kk]?(?:·\d+薪)?)"
    )
    LOCATION_PATTERN = re.compile(
        r"(北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|重庆|苏州|天津|长沙|"
        r"东莞|宁波|佛山|合肥|青岛|郑州|厦门|福州|无锡|济南|大连|昆明|沈阳|"
        r"长春|哈尔滨|石家庄|太原|贵阳|南宁|海口|乌鲁木齐|兰州|银川|西宁|拉萨)"
    )
    EXPERIENCE_PATTERN = re.compile(
        r"(\d+[-~]\d+年|应届|不限|在校/应届|\d+年以内|\d+年以上)"
    )
    EDUCATION_PATTERN = re.compile(
        r"(博士|硕士|本科|大专|中专|高中|学历不限)"
    )
    TIME_PATTERN = re.compile(
        r"(\d{1,2}:\d{2}|昨天|今天|\d+月\d+日|\d{4}-\d{2}-\d{2})"
    )

    def __init__(self, config: AppConfig | None = None):
        self._cfg = config or default_config

    def parse(self, ocr_results: dict[str, OCRResult]) -> BossZhipinUIData:
        """解析所有区域的 OCR 结果，返回结构化 UI 数据"""
        data = BossZhipinUIData()
        data._raw_ocr_results = ocr_results

        for region_name, result in ocr_results.items():
            method = getattr(self, f"_parse_{region_name}", None)
            if method is None:
                continue
            try:
                method(result, data)
            except Exception as exc:
                # 单个区域解析失败不影响其他区域
                import logging
                logging.getLogger(__name__).warning(
                    "解析区域 %s 失败: %s", region_name, exc
                )

        return data

    # ── 各区域解析器 ──

    def _parse_chat_list(self, result: OCRResult, data: BossZhipinUIData) -> None:
        """解析聊天/职位列表区域"""
        lines = result.text_lines
        entries: list[ChatListEntry] = []
        current: Optional[ChatListEntry] = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 启发式：包含公司名或职位关键词时为新条目
            if self._is_chat_entry_start(line):
                if current is not None:
                    entries.append(current)
                current = ChatListEntry(raw_text=line)

            if current is None:
                current = ChatListEntry(raw_text=line)

            # 提取已知字段
            current.raw_text += " | " + line

            if not current.name:
                current.name = self._extract_name(line)
            if not current.company:
                current.company = self._extract_company(line)
            if not current.job_title:
                current.job_title = self._extract_job_title(line)
            if not current.last_message:
                current.last_message = line

            time_match = self.TIME_PATTERN.search(line)
            if time_match:
                current.timestamp = time_match.group(1)

        if current is not None:
            entries.append(current)

        data.chat_list = entries

    def _parse_chat_messages(self, result: OCRResult, data: BossZhipinUIData) -> None:
        """解析聊天消息区域"""
        messages: list[ChatMessage] = []
        lines = result.text_lines

        for line in lines:
            line = line.strip()
            if not line:
                continue

            msg = ChatMessage(raw_text=line)

            # 检测时间戳
            time_match = self.TIME_PATTERN.search(line)
            if time_match:
                msg.timestamp = time_match.group(1)
                # 去掉时间戳部分后的内容作为消息正文
                content = self.TIME_PATTERN.sub("", line).strip()
                msg.content = content
            else:
                msg.content = line

            # 检测是否为本人发送（通常靠右或特定前缀）
            msg.is_self = self._detect_self_message(line)

            # 检测发送者
            msg.sender = self._extract_sender(line)

            # 检测消息类型
            if any(kw in line for kw in ["[图片]", "[image]", "发送了图片"]):
                msg.message_type = "image"
            elif any(kw in line for kw in ["[文件]", "[file]", "发送了文件"]):
                msg.message_type = "file"
            elif "交换了" in line or "对方正在" in line or "已读" in line:
                msg.message_type = "system"

            messages.append(msg)

        data.chat_messages = messages

    def _parse_job_detail(self, result: OCRResult, data: BossZhipinUIData) -> None:
        """解析职位详情面板"""
        full = result.full_text
        detail = JobDetail(raw_text=full)

        # 薪资
        salary_match = self.SALARY_PATTERN.search(full)
        if salary_match:
            detail.salary_range = salary_match.group(1)

        # 地点
        loc_match = self.LOCATION_PATTERN.search(full)
        if loc_match:
            detail.location = loc_match.group(1)

        # 经验
        exp_match = self.EXPERIENCE_PATTERN.search(full)
        if exp_match:
            detail.experience = exp_match.group(1)

        # 学历
        edu_match = self.EDUCATION_PATTERN.search(full)
        if edu_match:
            detail.education = edu_match.group(1)

        # 标签：通常在职位描述上方
        detail.tags = self._extract_tags(full)

        # 职位描述：取标签之后的文本
        detail.description = self._extract_description(full, detail.tags)

        # HR 信息
        detail.hr_name = self._extract_hr_name(full)
        detail.hr_title = self._extract_hr_title(full)

        # 职位名称和公司名
        lines = result.text_lines
        if lines:
            detail.title = lines[0] if lines else ""
            if len(lines) > 1:
                detail.company = lines[1]

        data.job_detail = detail

    def _parse_left_nav(self, result: OCRResult, data: BossZhipinUIData) -> None:
        """解析左侧导航栏"""
        nav_items: list[LeftNavItem] = []

        for block in result.text_blocks:
            text = block.text.strip()
            if not text:
                continue
            item = LeftNavItem(label=text)
            # 检查是否有未读标记（通常紧邻导航标签或以其形式出现）
            badge_match = re.search(r"(\d{1,3})", text)
            if badge_match:
                item.unread_badge = int(badge_match.group(1))
                item.label = re.sub(r"\d{1,3}", "", text).strip()
            nav_items.append(item)

        data.left_nav = nav_items

    def _parse_top_bar(self, result: OCRResult, data: BossZhipinUIData) -> None:
        """解析顶部标题栏"""
        data.top_bar = result.full_text.strip()

    def _parse_chat_input(self, result: OCRResult, data: BossZhipinUIData) -> None:
        """解析聊天输入框"""
        data.chat_input_text = result.full_text.strip()

    # ── 辅助方法 ──

    @staticmethod
    def _is_chat_entry_start(text: str) -> bool:
        """判断文本行是否为聊天条目起始（含姓名+公司+职位特征）"""
        indicators = ["HR", "经理", "主管", "总监", "工程师", "运营", "设计",
                      "产品", "技术", "销售", "市场", "行政", "财务",
                      "有限公司", "科技", "网络", "信息"]
        return any(ind in text for ind in indicators)

    @staticmethod
    def _extract_name(text: str) -> str:
        """从文本中提取人名（HR/求职者）"""
        # 简化：取前2-4个中文字符
        chars = re.findall(r"[一-鿿]{2,4}", text)
        return chars[0] if chars else ""

    @staticmethod
    def _extract_company(text: str) -> str:
        """从文本中提取公司名"""
        match = re.search(r"([一-鿿]+(?:有限公司|科技|网络|信息|集团|股份)(?:公司)?)", text)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_job_title(text: str) -> str:
        """从文本中提取职位名"""
        patterns = [
            r"([一-鿿]+(?:工程师|经理|主管|总监|专员|助理|设计师|运营|开发|测试))",
        ]
        for pat in patterns:
            match = re.search(pat, text)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _detect_self_message(text: str) -> bool:
        """检测消息是否为本人发送（启发式：包含"我"或特定标识）"""
        self_indicators = ["我：", "我说：", "我:", "已读"]
        return any(ind in text for ind in self_indicators)

    @staticmethod
    def _extract_sender(text: str) -> str:
        """从消息文本中提取发送者"""
        match = re.match(r"^([一-鿿]{2,4})[：:]", text)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def _extract_tags(full_text: str) -> list[str]:
        """从职位详情文本中提取标签"""
        # 标签通常以特殊符号分隔
        tags = re.findall(r"[一-鿿a-zA-Z+#]+", full_text)
        # 过滤掉过短或过长的标签
        return [t.strip() for t in tags if 2 <= len(t.strip()) <= 10]

    @staticmethod
    def _extract_description(full_text: str, tags: list[str]) -> str:
        """提取职位描述（标签之后的文本）"""
        if tags:
            last_tag = tags[-1]
            idx = full_text.find(last_tag)
            if idx != -1:
                return full_text[idx + len(last_tag):].strip()
        return full_text

    @staticmethod
    def _extract_hr_name(text: str) -> str:
        """提取HR姓名"""
        match = re.search(r"(HR|人事|招聘)[：:]?\s*([一-鿿]{2,4})", text)
        if match:
            return match.group(2)
        return ""

    @staticmethod
    def _extract_hr_title(text: str) -> str:
        """提取HR头衔"""
        match = re.search(r"(招聘(?:经理|主管|专员|HR|负责人|总监))", text)
        return match.group(1) if match else ""
