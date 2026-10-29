"""麦麦很好奇（MaiBot Curator）

一句话：在后台定期巡检「黑话 / 表达方式 / 表情包」三个学习库，做补全、去重、清洗，
让麦麦对群里的说法理解得更准，同时不把上下文和 token 浪费在噪音上。

== 三个模块做什么 ==
1. 黑话（jargons）：找出群内反复出现但库里没有释义的词，结合【群内真实用法】+【联网检索】
   生成中文释义；两路证据一致才写回（is_jargon=1、is_complete=1），宿主会在 planner 上下文里
   带最多 10 条释义帮 bot 理解聊天。
2. 表达方式（expressions）：宿主会自动学习"表达方式"（AI 生成、默认不勾选）。本模块定期做
   embedding 聚类去重、改写情景描述、删除无意义碎片（纯符号、断句残片等）。
   只有 checked=1 的表达会真正注入 replyer，默认不自动勾选（见 config 的 auto_check）。
3. 表情包（images, EMOJI）：给已注册表情补「画面描述｜标签｜适用场景」，并按描述相似度
   合并近似重复（保留更常用的那张、取消注册其余）。不改宿主随机选图的逻辑。

== 多轮多源自证（v2.1，三模块共用） ==
每条候选同时收集两路证据：
  ① 群内用法：chat.get_group_streams 取群会话 → message.get_by_time_in_chat 找该词/表达/表情的
     真实原句（带时间与昵称）；
  ② 联网检索：DeepSeek /v1/tools/search，用 2~3 种不同问法各查一轮，必要时抓首条正文。
两路交给 LLM 一次判定 {agree, confidence, group_specific}：
  - agree=true 且置信度 ≥ 各模块 min_confidence 且含义非空 → 锁定，写入 *_verified.json；
  - 群内用法与联网矛盾时以群内为准，并标 group_specific=true（群内专属说法）；
  - 未锁定记 attempts，超过 verify_max_attempts 次后放弃，避免无限花 token；
  - 锁定过的条目后续巡检直接跳过，不再调用 LLM。

== 文件结构导航（按行号大致分组） ==
  配置模型        PluginSectionConfig / ScheduleConfig / JargonConfig / WebSearchConfig /
                  ExpressionConfig / EmojiConfig / LearningKeeperPluginConfig
  生命周期与调度  on_load / on_unload / on_config_update / _loop / run_now / last_report /
                  _module_due / run_due_modules / run_all_modules / _run_modules
  通用工具        _db / _llm / _embed（全部走宿主 capability；不直连数据库/模型）
  自证层          _group_stream_ids / _stream_usage_lines / _verify_query_variants / _verify_targets
  黑话模块        _run_jargon / _jargon_evidence / _jargon_judge_batch / _jargon_prompt /
                  _parse_jargon_decisions / _jargon_verify_rounds / _jargon_apply
  联网检索        _search_credentials / _http_json / _web_search_term / 缓存
  表达模块        _run_expression（聚类合并 → LLM 判质量 → 写回 → 自证）/ _expression_judge
  表情模块        _run_emoji（去重 → 补描述 → 自证）/ _emoji_enrich
  入口            create_plugin()

== 常用调参速查（对应 config.toml 同名字段） ==
  想更省 token          → 调小 verify_terms_per_run / batch_size / max_rows；
                          缩短 group_usage_hours；调大 interval_minutes
  想判定更严格          → 调高各模块 min_confidence（黑话 0.6、表达 0.65、表情默认同）
  只想看报告不改库      → writeback = false（报告仍会写 last_report*.json；日程归档人物卡也跳过）
  太频繁/太吵           → interval_minutes 调大（黑话 360=6h，表达 10080=7d，表情 43200=30d）
  不想联网              → [web] enabled = false（只用群内证据，自证变弱）
  想立刻重跑            → WebUI 插件页「运行一次」，或 state.json 对应 *_last_run_ts 置 0 后重启

== 数据文件（插件 data_dir，即 data/plugins/<插件 id>/） ==
  state.json              各模块上次运行时间戳
  web_cache.json          联网检索缓存（可删，删了会重新查）
  {jargon,expression,emoji}_verified.json   自证台账（locked=已固定，failed=待重试）
  last_report.json        最近一轮汇总
  last_report_{jargon,expression,emoji}.json 各模块明细（含 candidates/decisions/sample/errors）

== 设计原则 ==
  - 全部通过宿主 capability（database/llm/message/chat）访问，不直接 import 宿主内部模块；
  - 写库前先出报告，所有写回都有上限：黑话/表达/表情用 max_writes_per_run，日程归档用 max_archive_per_run；
  - 联网只使用插件自己配置的 [web] api_key（留空=不联网），检索结果命中缓存后复用，避免重复花费；
  - 任何一个模块出错只记录到 errors，不影响其他模块。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import math
import re
import time
from datetime import datetime
from uuid import uuid4
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from maibot_sdk import API, Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

try:  # 宿主运行环境自带 httpx（插件 SDK 亦依赖它）；缺失时联网功能自动禁用
    import httpx  # type: ignore
except Exception:  # noqa: BLE001
    httpx = None  # type: ignore

# 宿主数据库模型名 → 实际表：Jargon=jargons、Messages=mai_messages、
# Expression=expressions、Images=images、HighFrequencyTerm=high_frequency_terms
JARGON_MODEL = "Jargon"
MESSAGE_MODEL = "Messages"
EXPRESSION_MODEL = "Expression"
IMAGE_MODEL = "Images"
HF_TERM_MODEL = "HighFrequencyTerm"

MAX_EVIDENCE_IDS = 12
MAX_SURROUNDING = 8
WEB_FETCH_MAX_CHARS = 1500

_COMMON_WORDS_EXCLUDED = (
    "抽象 交学费 命硬 摸鱼 神了 干饭 水群 绷不住了 我去 好叭 好吧 哈哈 确实 真的 不是 没有 "
    "这个 那个 什么 怎么 可以 不行 就是 还是 但是 所以 因为 如果 然后 现在 今天 明天 昨天"
)

_AUTO_ADOPT_BLOCK_WORDS = (
    "傻逼", "妈的", "操你", "卧槽", "尼玛", "草泥马", "cnm", "nmsl", "tmd",
    "fuck", "shit", "涩涩", "约炮", "裸聊",
)

_EMOJI_TAGS = (
    "自嘲 无语 卖萌 看戏 开心 委屈 生气 惊讶 害羞 敷衍 加油 安慰 "
    "吐槽 得意 摆烂 疑惑 亲昵 尴尬 温柔 坏笑"
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_json_array(raw: str) -> Optional[list]:
    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso_ts(value: Any) -> float:
    """把 "2026-10-29T18:00" / "2026-10-29 18:00" 解析成 epoch 秒；解析失败返回 0。"""
    raw = _text(value)
    if not raw:
        return 0.0
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[: len(fmt) + 2].strip(), fmt).timestamp()
        except ValueError:
            continue
    return 0.0


CONTEXT_ITEM_SCHEMA_VERSION = 1


def _new_user_context_item(text: str) -> Dict[str, Any]:
    """构造 Hook 可反序列化的 UserMessageItem（MaiBot 1.2+ Context Item 协议）。"""
    return {
        "item_type": "UserMessageItem",
        "meta": {
            "item_id": uuid4().hex,
            "logical_turn_id": None,
            "timestamp": datetime.now().astimezone().isoformat(),
        },
        "parts": [{"type": "text", "text": text}],
    }


# ====== 配置模型 ======


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="插件总开关（true/false，默认 true；关闭后不再巡检，也不再注入上下文）")
    config_version: str = Field(default="0.1.4", description="配置版本（内部记录用，随插件升级自动更新，默认与插件版本一致；一般无需修改）")


class ScheduleConfig(PluginConfigBase):
    """调度配置。"""

    __ui_label__ = "调度"
    __ui_icon__ = "clock"
    __ui_order__ = 1

    tick_minutes: int = Field(default=30, description="调度器多久检查一次到期模块（分钟，默认 30；范围 1–1440；例：30=每半小时检查一次）")
    run_on_load: bool = Field(default=True, description="插件加载后是否立刻执行一轮到期模块（true/false，默认 true）")
    startup_delay_seconds: int = Field(default=90, description="加载后等待多少秒再开始首轮巡检（秒，默认 90；范围 0–600）")


class JargonConfig(PluginConfigBase):
    """黑话模块。"""

    __ui_label__ = "黑话学习"
    __ui_icon__ = "message-circle"
    __ui_order__ = 2

    enabled: bool = Field(default=True, description="是否启用黑话学习模块（true/false，默认 true）")
    interval_minutes: int = Field(default=240, description="黑话模块运行间隔（分钟，默认 240=4 小时；范围 0–43200；0=不自动运行）")
    batch_size: int = Field(default=6, description="每次交给 LLM 判定的候选词数量（默认 6；范围 1–50）")
    max_candidates: int = Field(default=30, description="单次最多处理多少个候选词（默认 30；范围 1–500）")
    min_count: int = Field(default=2, description="一个词至少出现过多少次才进入候选（默认 2；范围 1–100）")
    evidence_lines: int = Field(default=12, description="每个候选词最多带多少条上下文证据（默认 12；范围 1–50）")
    llm_task: str = Field(default="utils", description="调用宿主哪个 LLM 任务（默认 utils；示例：utils / chat，需与宿主模型配置里的任务名一致）")
    max_tokens: int = Field(default=4000, description="单次 LLM 输出 token 上限（默认 4000；范围 256–32768）")
    temperature: float = Field(default=0.1, description="LLM 采样温度（默认 0.1；范围 0–2；越低越保守）")
    writeback: bool = Field(default=True, description="关闭时只出报告；打开后才会更新/删除黑话库（true/false，默认 true；首次建议先 false 观察报告）")
    min_confidence: float = Field(default=0.6, description="低于该置信度的判定不写回（默认 0.6；范围 0–1；例：0.6=置信度 60% 以上才写）")
    delete_rejected: bool = Field(default=True, description="把判定为常见词/无意义的候选从黑话库删除（true/false，默认 true）")
    clear_high_frequency_terms: bool = Field(default=False, description="写回成功时清空高频词表（整表清空、不可恢复；只影响黑话匹配打分，一般保持关闭；true/false，默认 false）")
    max_writes_per_run: int = Field(default=40, description="单次最多写回多少条黑话（默认 40；范围 0–1000；0=不写回）")
    group_usage_hours: int = Field(default=336, description="自证时回看群内用法的时间窗口（小时，默认 336=14 天；范围 1–8760）")
    dedupe_similar: bool = Field(default=True, description="收录前是否先整理：同一词的不同写法归一化去重、合并词频（true/false，默认 true）")
    recheck_complete: bool = Field(default=True, description="是否定期复核已完成的词条（看是否仍在使用、含义是否准确；true/false，默认 true）")
    recheck_days: int = Field(default=30, description="已完成词条多少天未复核才重新判定（默认 30；范围 1–365）")
    context_lines: int = Field(default=6, description="判定时参考的群聊上下文条数（默认 6；范围 0–30；0=只使用该词自身证据）")


class WebSearchConfig(PluginConfigBase):
    """联网检索配置（三个模块的自证共用：黑话补含义、表达/表情交叉验证）。

    本插件不读取宿主的任何配置文件；联网检索只使用下面 [web] 里填写的 api_key / base_url。
    不填 api_key 时联网检索会自动跳过，只使用群内用法证据（见 README「配置」一节）。
    """

    __ui_label__ = "联网检索"
    __ui_icon__ = "globe"
    __ui_order__ = 3

    enabled: bool = Field(default=True, description="联网检索总开关（true/false，默认 true；未填写 api_key 时即使开启也只使用群内证据）")
    api_key: str = Field(default="", description="DeepSeek API Key，联网检索自证用（示例：sk-xxxx；在 platform.deepseek.com 自行申请；留空=不联网，只用群内证据）")
    base_url: str = Field(default="", description="DeepSeek API 地址（留空=默认 https://api.deepseek.com；仅建议填写可信的 API 地址）")
    timeout_seconds: int = Field(default=25, description="单次联网请求超时（秒，默认 25；范围 1–120）")
    per_term_results: int = Field(default=3, description="每个词最多取几条搜索结果（默认 3；范围 1–10）")
    max_searches_per_run: int = Field(default=6, description="单次巡检最多联网检索多少个词（默认 6；范围 0–50）")
    fetch_top_page: bool = Field(default=True, description="是否抓取排名第一结果的正文字摘要（true/false，默认 true）")
    cache_ttl_hours: int = Field(default=720, description="检索结果缓存时长（小时，默认 720=30 天；范围 1–8760；避免重复花费）")
    verification_rounds: int = Field(default=2, description="多轮自证轮数（默认 2；范围 1–5；各轮结果一致才固定含义）")
    verify_terms_per_run: int = Field(default=4, description="单次最多对多少个词做多轮自证（默认 4；范围 0–50）")
    verify_max_attempts: int = Field(default=3, description="单个词最多自证几次，超过就放弃（默认 3；范围 1–10；避免无限花 token）")


class ExpressionConfig(PluginConfigBase):
    """表达方式模块。"""

    __ui_label__ = "表达方式"
    __ui_icon__ = "quote"
    __ui_order__ = 4

    enabled: bool = Field(default=True, description="是否启用表达方式优化模块（true/false，默认 true）")
    interval_minutes: int = Field(default=720, description="表达模块运行间隔（分钟，默认 720=12 小时；范围 0–525600；0=不自动运行）")
    batch_size: int = Field(default=10, description="每次交给 LLM 处理的表达条数（默认 10；范围 1–40；太大易触发输出截断）")
    max_rows: int = Field(default=300, description="单次最多扫描多少条 AI 学习表达（默认 300；范围 1–1000）")
    llm_task: str = Field(default="utils", description="调用宿主哪个 LLM 任务（默认 utils；需与宿主模型配置里的任务名一致）")
    max_tokens: int = Field(default=4000, description="单次 LLM 输出 token 上限（默认 4000；范围 256–32768）")
    similarity_threshold: float = Field(default=0.92, description="embedding 相似度达到多少视为重复并合并（默认 0.92；范围 0–1）")
    writeback: bool = Field(default=True, description="关闭时只出报告；打开后才会合并/改写/删除表达（true/false，默认 true）")
    delete_meaningless: bool = Field(default=True, description="是否删除判定为无意义的表达（true/false，默认 true）")
    min_confidence: float = Field(default=0.65, description="低于该置信度的判定不写回（默认 0.65；范围 0–1）")
    max_writes_per_run: int = Field(default=200, description="单次最多写回多少条表达（默认 200；范围 0–2000）")
    auto_check: bool = Field(default=False, description="旧版自动勾选开关（true/false，默认 false；已由 auto_check_mode 取代，仅为兼容保留）")
    verification_rounds: int = Field(default=2, description="多轮自证轮数（默认 2；范围 1–5；群内用法 + 联网检索）")
    verify_terms_per_run: int = Field(default=8, description="单次最多对多少条表达做多轮自证（默认 8；范围 0–50）")
    group_usage_hours: int = Field(default=336, description="自证时回看群内用法的时间窗口（小时，默认 336=14 天；范围 1–8760）")
    persona_filter: bool = Field(default=True, description="是否对将被采用的表达做「人格适配严判」（true/false，默认 true；按宿主人格设定删掉攻击性强、适用面窄、不符合角色的表达）")
    persona_delete: bool = Field(default=True, description="人格严判不通过时是否直接删除（true/false，默认 true；false=只写进报告不删，建议先观察一轮）")
    persona_max_rows: int = Field(default=80, description="单次最多严判多少条表达（默认 80；范围 1–300；只判第一轮认定有意义的条目）")
    persona_max_chars: int = Field(default=1200, description="读入宿主人格设定的字数上限（默认 1200；范围 200–4000；太小判断不准，太大费 token）")
    persona_traits: str = Field(default="友善、温和，偶尔毒舌和俏皮；不刻薄、不贬低他人、不油腻", description="目标角色性格关键词（默认「友善、温和，偶尔毒舌和俏皮；不刻薄、不贬低他人、不油腻」；用顿号或分号分隔）")
    auto_check_mode: str = Field(default="report", description="自动采纳模式（off=不自动；report=只生成候选清单不勾选；on=合格即自动勾选；默认 report，建议先跑一轮 report 观察）")
    auto_check_min_confidence: float = Field(default=0.9, description="自动采纳所需的判定置信度（默认 0.9；范围 0–1）")
    auto_check_double_check: bool = Field(default=True, description="自动采纳前是否二次复核（true/false，默认 true；多一次 LLM 调用换更高准确率）")
    auto_check_max_per_run: int = Field(default=10, description="单次最多自动勾选多少条（默认 10；范围 0–500）")
    auto_check_log: bool = Field(default=True, description="是否记录自动勾选清单到 auto_checked.json（true/false，默认 true；便于检查与回滚）")
    context_lines: int = Field(default=6, description="判定时参考的群聊上下文条数（默认 6；范围 0–30；0=不参考上下文）")
    persona_mode: str = Field(
        default="conservative",
        description=(
            "人格判定取向：conservative=保守学习，优先角色一致性，群内流行但与角色口吻冲突的表达也删；"
            "adapt=主动向本群交流风格靠拢，允许群内流行、干净、不伤人的说法（与角色口吻略偏也可采用）；"
            "默认 conservative，建议先保守观察一轮再切 adapt"
        ),
    )


class EmojiConfig(PluginConfigBase):
    """表情包模块。"""

    __ui_label__ = "表情包"
    __ui_icon__ = "smile"
    __ui_order__ = 5

    enabled: bool = Field(default=True, description="是否启用表情包库优化模块（true/false，默认 true）")
    interval_minutes: int = Field(default=43200, description="表情模块运行间隔（分钟，默认 43200=30 天；范围 0–525600；0=不自动运行）")
    batch_size: int = Field(default=8, description="每次交给 LLM 处理的表情数量（默认 8；范围 1–40）")
    max_emoji: int = Field(default=300, description="单次最多处理多少张已注册表情（默认 300；范围 1–2000）")
    llm_task: str = Field(default="utils", description="调用宿主哪个 LLM 任务（默认 utils；需与宿主模型配置里的任务名一致）")
    max_tokens: int = Field(default=4000, description="单次 LLM 输出 token 上限（默认 4000；范围 256–32768）")
    writeback: bool = Field(default=True, description="关闭时只出报告；打开后才会改写描述/取消注册重复表情（true/false，默认 true）")
    enrich_description: bool = Field(default=True, description="是否给表情补「语气标签 + 适用场景」（true/false，默认 true）")
    similarity_threshold: float = Field(default=0.95, description="描述相似度达到多少视为近似重复（默认 0.95；范围 0–1）")
    dedupe_unregister: bool = Field(default=True, description="近似重复表情是否取消注册（true/false，默认 true；保留更常用的那张）")
    max_unregister_per_run: int = Field(default=20, description="单次最多取消注册多少张表情（默认 20；范围 0–500）")
    max_enrich_per_run: int = Field(default=300, description="单次最多补写多少条表情描述（默认 300；范围 0–2000）")
    verification_rounds: int = Field(default=2, description="多轮自证轮数（默认 2；范围 1–5；群内用法 + 联网检索）")
    verify_terms_per_run: int = Field(default=15, description="单次最多对多少张表情做多轮自证（默认 15；范围 0–50）")
    group_usage_hours: int = Field(default=720, description="自证时回看群内用法的时间窗口（小时，默认 720=30 天；范围 1–8760）")
    context_lines: int = Field(default=4, description="补写描述时参考的群聊上下文条数（默认 4；范围 0–30）")


class AgendaConfig(PluginConfigBase):
    """群友日程模块配置（抽取 → 待办 → 复证 → 确认 → 注入/归档 → 清理）。"""

    __ui_label__ = "群友日程"
    __ui_icon__ = "calendar"
    __ui_order__ = 7

    enabled: bool = Field(default=True, description="是否启用群友日程（true/false，默认 true）")
    interval_minutes: int = Field(default=60, description="扫描间隔（分钟，默认 60=每小时；范围 1–1440）")
    lookback_hours: int = Field(default=3, description="每次回看最近多少小时的消息（小时，默认 3；范围 1–72）")
    max_streams: int = Field(default=3, description="每轮最多扫几个群/私聊会话（默认 3；范围 1–20）")
    scan_private: bool = Field(default=False, description="私聊也扫描（true/false，默认 false=只扫群）")
    max_messages_per_stream: int = Field(default=200, description="每个会话最多取多少条消息（默认 200；范围 20–2000）")
    max_events: int = Field(default=5, description="注入上下文的日程条数上限（默认 5；范围 1–20）")
    llm_task: str = Field(default="utils", description="调用宿主哪个 LLM 任务（默认 utils；需与宿主模型配置里的任务名一致）")
    max_tokens: int = Field(default=4000, description="单次调用最大输出 token（默认 4000；范围 256–32768）")
    temperature: float = Field(default=0.2, description="采样温度（默认 0.2；范围 0–2）")
    writeback: bool = Field(default=True, description="是否写回（true/false，默认 true；false=只出报告，dry-run，人物卡归档也会跳过）")
    archive_to_person: bool = Field(default=True, description="是否把结束的日程归档到宿主人物卡 PersonInfo.memory_points（true/false，默认 true；dry-run 时不写）")
    expire_hours: int = Field(default=48, description="已确认事件过了时间多久视为结束（小时，默认 48；范围 1–720）")
    stale_days: int = Field(default=14, description="已确认事件多少天没人再提就归档（天，默认 14；范围 1–365）")
    max_archive_per_run: int = Field(default=10, description="每轮最多归档多少条（默认 10；范围 0–1000；0=不限制，超出顺延到下一轮；dry-run 不写人物卡、不受此限）")
    candidate_ttl_hours: int = Field(default=72, description="待办候选最多保留多少小时，未确认就丢弃（小时，默认 72；范围 1–720）")
    confirm_min_hits: int = Field(default=2, description="同一件事被提到几次才确认（默认 2；范围 1–20；模糊时间用）")
    exact_confirm_confidence: float = Field(default=0.75, description="时间明确时，置信度达到多少可直接确认（默认 0.75；范围 0–1）")
    abstract_window_hours: int = Field(default=24, description="「一会儿/之后」这类模糊时间的观察窗（小时，默认 24；范围 1–168）")
    relative_window_hours: int = Field(default=36, description="「明天/周末」这类只有日期的观察窗（小时，默认 36；范围 1–336）")
    purge_closed_days: int = Field(default=3, description="取消/丢弃的记录保留几天后删除（天，默认 3；范围 1–90）")
    purge_archived_days: int = Field(default=7, description="归档记录保留几天后删除（天，默认 7；范围 1–365；人物卡记忆保留）")
    inject: bool = Field(default=True, description="是否在聊天请求前注入最近安排（true/false，默认 true）")


class InjectConfig(PluginConfigBase):
    """上下文注入配置（planner / replyer 两个 Hook 共用）。"""

    __ui_label__ = "上下文注入"
    __ui_icon__ = "message-square"
    __ui_order__ = 8

    enabled: bool = Field(default=True, description="注入总开关（true/false，默认 true）")
    in_planner: bool = Field(default=True, description="planner 请求前注入（true/false，默认 true）")
    in_replyer: bool = Field(default=True, description="replyer 请求前注入（true/false，默认 true）")
    agenda: bool = Field(default=True, description="注入最近安排（true/false，默认 true）")
    max_events: int = Field(default=5, description="注入的日程条数上限（默认 5；范围 1–20）")
    max_chars: int = Field(default=420, description="注入块总字数上限，超了截断（默认 420；范围 80–2000）")


class LearningKeeperPluginConfig(PluginConfigBase):
    """麦麦很好奇配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    jargon: JargonConfig = Field(default_factory=JargonConfig)
    web: WebSearchConfig = Field(default_factory=WebSearchConfig)
    expression: ExpressionConfig = Field(default_factory=ExpressionConfig)
    emoji: EmojiConfig = Field(default_factory=EmojiConfig)
    agenda: AgendaConfig = Field(default_factory=AgendaConfig)
    inject: InjectConfig = Field(default_factory=InjectConfig)


class LearningKeeperPlugin(MaiBotPlugin):
    """麦麦很好奇插件。"""

    config_model = LearningKeeperPluginConfig

    @classmethod
    def build_config_schema(cls, **kwargs: Any) -> Dict[str, Any]:
        """在 SDK 生成的 WebUI 配置 Schema 上补齐字段提示（hint）。

        MaiBot WebUI 只有在字段带 ``hint``（来自 ``json_schema_extra={"hint": ...}``）
        时才会在输入框下方显示灰色说明；这里统一把字段的 ``description`` 回填到
        ``hint``，避免 90 个字段逐条重复写 hint，也保证说明只有一处真相。
        """
        schema = super().build_config_schema(**kwargs)
        sections = schema.get("sections") if isinstance(schema, dict) else None
        if isinstance(sections, dict):
            for section in sections.values():
                fields = section.get("fields") if isinstance(section, dict) else None
                if not isinstance(fields, dict):
                    continue
                for field in fields.values():
                    if not isinstance(field, dict):
                        continue
                    if not field.get("hint") and field.get("description"):
                        field["hint"] = field["description"]
        return schema

    def __init__(self) -> None:
        super().__init__()
        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = False
        self._running = False
        self._last_report: Optional[Dict[str, Any]] = None
        self._last_run_at: Optional[str] = None
        self._state: Dict[str, Any] = {}
        self._web_cache: Dict[str, Any] = {}
        self._jargon_verified: Dict[str, Any] = {}
        self._expression_verified: Dict[str, Any] = {}
        self._emoji_verified: Dict[str, Any] = {}
        self._agenda: Dict[str, Any] = {}
        self._persona_cache: Optional[str] = None

    # ===== 生命周期 =====

    async def on_load(self) -> None:
        self._state = self._read_json(self._data_path("state.json"), {}) or {}
        self._web_cache = self._read_json(self._data_path("web_cache.json"), {}) or {}
        self._jargon_verified = self._read_json(self._data_path("jargon_verified.json"), {}) or {}
        self._expression_verified = self._read_json(self._data_path("expression_verified.json"), {}) or {}
        self._emoji_verified = self._read_json(self._data_path("emoji_verified.json"), {}) or {}
        self._agenda = self._read_json(self._data_path("agenda.json"), {}) or {}
        self.ctx.logger.info(
            "[maibot-curator] 已加载：黑话=%s 表达=%s 表情=%s/%s 日程=%s，联网=%s",
            self.config.jargon.enabled,
            self.config.expression.enabled,
            self.config.emoji.enabled,
            "写回开" if self.config.emoji.writeback else "只报告",
            self.config.agenda.enabled,
            self.config.web.enabled,
        )
        self._start_loop()

    async def on_unload(self) -> None:
        self._stopping = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.ctx.logger.info("[maibot-curator] 已卸载")

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        self.ctx.logger.info("[maibot-curator] 配置已更新: scope=%s version=%s", scope, version)
        if not self.config.plugin.enabled:
            self._stopping = True
            task = self._task
            self._task = None
            if task is not None and not task.done():
                task.cancel()
            return
        # 重新启用（或改调度）时确保后台循环在跑：任务被 cancel 后只有 on_load 会重新拉起，这里补上
        self._start_loop()

    def _start_loop(self) -> None:
        if not self.config.plugin.enabled:
            self.ctx.logger.info("[maibot-curator] 插件未启用，跳过自动巡检")
            return
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="maibot-curator-loop")

    async def _loop(self) -> None:
        try:
            delay = max(0, int(self.config.schedule.startup_delay_seconds))
            if delay:
                await asyncio.sleep(delay)
            if self.config.schedule.run_on_load:
                await self._safe_run_due()
            while not self._stopping:
                tick = max(5, int(self.config.schedule.tick_minutes))
                await asyncio.sleep(tick * 60)
                if self._stopping:
                    break
                await self._safe_run_due()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 后台任务必须自愈
            self.ctx.logger.error("[maibot-curator] 调度循环异常: %s", exc, exc_info=True)

    async def _safe_run_due(self) -> None:
        """跑一轮到期的模块；单轮异常只记录、绝不终止循环（下一轮照常）。"""
        try:
            await self.run_due_modules()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单轮失败不能结束后台巡检
            self.ctx.logger.error("[maibot-curator] 本轮巡检异常（循环继续）: %s", exc, exc_info=True)

    # ===== 对外 API（WebUI 可手动触发） =====

    @API("run_now", description="立即执行一轮全部模块（遵循各模块写回开关）", version="1")
    async def run_now(self) -> Dict[str, Any]:
        report = await self.run_all_modules()
        return report

    @API("last_report", description="读取最近一次运行报告", version="1")
    async def last_report(self) -> Dict[str, Any]:
        return {
            "enabled": self.config.plugin.enabled,
            "last_run_at": self._last_run_at,
            "report": self._last_report or {},
        }

    # ===== 调度 =====

    def _module_interval(self, module: str) -> int:
        if module == "jargon":
            return int(self.config.jargon.interval_minutes)
        if module == "expression":
            return int(self.config.expression.interval_minutes)
        if module == "emoji":
            return int(self.config.emoji.interval_minutes)
        if module == "agenda":
            return int(self.config.agenda.interval_minutes)
        return 0

    def _module_enabled(self, module: str) -> bool:
        if module == "jargon":
            return bool(self.config.jargon.enabled)
        if module == "expression":
            return bool(self.config.expression.enabled)
        if module == "emoji":
            return bool(self.config.emoji.enabled)
        if module == "agenda":
            return bool(self.config.agenda.enabled)
        return False

    def _module_due(self, module: str, now_ts: float) -> bool:
        # 到期判断：interval_minutes=0 表示不自动跑；从未跑过或已超期则返回 True
        interval = self._module_interval(module)
        if interval <= 0:
            return False
        last = _as_float(self._state.get(f"{module}_last_run_ts"), 0.0)
        return now_ts - last >= interval * 60

    def _mark_module(self, module: str, ts: float) -> None:
        self._state[f"{module}_last_run_ts"] = ts
        self._write_json(self._data_path("state.json"), self._state)

    async def run_due_modules(self) -> Dict[str, Any]:
        if self._running:
            return {"success": False, "message": "上一轮巡检还在运行"}
        now_ts = time.time()
        modules = [m for m in ("jargon", "expression", "emoji", "agenda") if self._module_enabled(m) and self._module_due(m, now_ts)]
        if not modules:
            return {"success": True, "message": "没有到期的模块", "modules": []}
        return await self._run_modules(modules)

    async def run_all_modules(self) -> Dict[str, Any]:
        if self._running:
            return {"success": False, "message": "上一轮巡检还在运行"}
        modules = [m for m in ("jargon", "expression", "emoji", "agenda") if self._module_enabled(m)]
        if not modules:
            return {"success": True, "message": "所有模块都已关闭", "modules": []}
        return await self._run_modules(modules)

    async def _run_modules(self, modules: List[str]) -> Dict[str, Any]:
        # 按顺序跑指定模块；某个模块抛错只记进 errors，不影响后续模块
        self._running = True
        self._bot_ids = None
        self._web_warned = False
        self._persona_cache = None
        report: Dict[str, Any] = {
            "success": False,
            "generated_at": _now_iso(),
            "modules": modules,
            "results": {},
            "errors": [],
        }
        try:
            for module in modules:
                started = time.time()
                self.ctx.logger.info("[maibot-curator] 开始模块: %s", module)
                try:
                    if module == "jargon":
                        result = await self._run_jargon(report)
                    elif module == "expression":
                        result = await self._run_expression(report)
                    elif module == "emoji":
                        result = await self._run_emoji(report)
                    elif module == "agenda":
                        result = await self._run_agenda(report)
                    else:
                        continue
                    result["elapsed_seconds"] = round(time.time() - started, 1)
                    report["results"][module] = result
                    self._mark_module(module, started)
                    self._write_json(self._data_path(f"last_report_{module}.json"), result)
                except Exception as exc:  # noqa: BLE001 - 单模块失败不影响其他模块
                    self.ctx.logger.error("[maibot-curator] 模块 %s 失败: %s", module, exc, exc_info=True)
                    report["errors"].append(f"{module}: {exc}")
            report["success"] = True
        finally:
            self._running = False
            self._last_run_at = report["generated_at"]
            self._last_report = report
            self._write_json(self._data_path("last_report.json"), report)
            self.ctx.logger.info(
                "[maibot-curator] 一轮完成：%s",
                json.dumps({m: r.get("summary", "") for m, r in report["results"].items()}, ensure_ascii=False),
            )
        return report

    # ===== 通用工具 =====

    def _data_path(self, name: str) -> Path:
        return Path(self.ctx.paths.data_dir) / name

    def _read_json(self, path: Path, default: Any) -> Any:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        return default

    def _write_json(self, path: Path, payload: Any) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("[maibot-curator] 写入文件失败 %s: %s", path.name, exc)

    async def _db(self, model_name: str, query_type: str = "get", **kwargs: Any) -> Any:
        payload: Dict[str, Any] = {"model_name": model_name, "query_type": query_type}
        payload.update(kwargs)
        return await self.ctx.call_capability("database.query", **payload)

    async def _llm(
        self,
        prompt: str,
        *,
        task_name: str,
        max_tokens: int,
        temperature: float,
        timeout: float = 180.0,
    ) -> str:
        raw = await asyncio.wait_for(
            self.ctx.call_capability(
                "llm.generate",
                timeout_ms=int(max(30.0, float(timeout)) * 1000),
                prompt=prompt,
                task_name=task_name,
                temperature=max(0.0, float(temperature)),
                max_tokens=max(128, int(max_tokens)),
            ),
            timeout=timeout,
        )
        if not isinstance(raw, dict):
            raise RuntimeError(f"LLM 返回格式异常: {type(raw).__name__}")
        if not raw.get("success"):
            raise RuntimeError(f"LLM 调用失败: {raw.get('error') or raw}")
        return _text(raw.get("response"))

    async def _embed(self, texts: List[str], *, batch: int = 16) -> List[List[float]]:
        vectors: List[List[float]] = []
        for start in range(0, len(texts), batch):
            chunk = texts[start : start + batch]
            raw = await self.ctx.call_capability("llm.embed", timeout_ms=120000, texts=chunk, task_name="embedding")
            if not isinstance(raw, dict) or not raw.get("success"):
                raise RuntimeError(f"embedding 调用失败: {raw!r}")
            for item in raw.get("results") or []:
                vectors.append([float(x) for x in (item.get("embedding") or [])])
        return vectors

    # ===== 多轮多源自证（群内用法 + 联网检索） =====

    def _verified_store(self, kind: str) -> Dict[str, Any]:
        # 三类自证台账（jargon/expression/emoji）分别存在内存 + data_dir/*_verified.json
        if kind == "jargon":
            return self._jargon_verified
        if kind == "expression":
            return self._expression_verified
        return self._emoji_verified

    def _save_verified(self, kind: str) -> None:
        self._write_json(self._data_path(f"{kind}_verified.json"), self._verified_store(kind))

    async def _group_stream_ids(self, limit: int = 3) -> List[str]:
        """取最近活跃的群聊 session_id，用于回看群内真实用法。"""
        try:
            raw = await self.ctx.call_capability("chat.get_group_streams", platform="qq")
        except Exception:  # noqa: BLE001 - 读不到就退化为无群内证据
            return []
        streams = raw.get("streams") if isinstance(raw, dict) else raw
        ids: List[str] = []
        for item in streams or []:
            if not isinstance(item, dict):
                continue
            sid = _text(item.get("session_id") or item.get("stream_id"))
            if sid and sid not in ids:
                ids.append(sid)
        return ids[:max(1, int(limit))]

    async def _stream_usage_lines(
        self,
        needles: List[str],
        stream_ids: List[str],
        *,
        hours: int,
        max_scan: int = 400,
        max_lines: int = 6,
        hash_mode: bool = False,
    ) -> List[str]:
        """在群聊最近消息里找 needle 的真实用法，返回带时间/昵称的片段。"""
        targets = [n.lower() for n in needles if n]
        if not targets or not stream_ids:
            return []
        lines: List[str] = []
        await self._bot_account_ids()
        for sid in stream_ids:
            end_ts = time.time()
            start_ts = end_ts - max(1.0, float(hours)) * 3600
            try:
                # 使用 SDK 公开签名的时间范围能力（chat_id / start_time / end_time / limit）
                raw = await self.ctx.call_capability(
                    "message.get_by_time_in_chat",
                    chat_id=sid,
                    start_time=start_ts,
                    end_time=end_ts,
                    limit=int(max_scan),
                )
            except Exception:  # noqa: BLE001
                continue
            if isinstance(raw, list):
                messages = raw
            elif isinstance(raw, dict):
                messages = raw.get("messages")
            else:
                messages = None
            for msg in messages or []:
                if not isinstance(msg, dict):
                    continue
                flat = self._flatten_message(msg, sid, sid)
                if flat is None:
                    continue
                # 本地兜底过滤：时间窗口 + Bot 自己的发言（不依赖宿主 filter_mai 私有参数）
                when = _as_float(msg.get("timestamp"))
                if when and when < start_ts:
                    continue
                if self._is_bot_message(flat):
                    continue
                text = _text(flat.get("processed_plain_text"))
                raw_content = json.dumps(msg, ensure_ascii=False, default=str)
                haystack = (raw_content if hash_mode else text).lower()
                if not haystack or not any(t in haystack for t in targets):
                    continue
                when = _as_float(msg.get("timestamp"))
                stamp = time.strftime("%m-%d %H:%M", time.localtime(when)) if when else ""
                who = _text(msg.get("user_cardname") or msg.get("user_nickname") or msg.get("user_id"))
                body = "（发过这张表情）" if hash_mode else text[:80]
                lines.append(f"[{stamp}] {who}：{body}")
                if len(lines) >= max_lines:
                    break
            if len(lines) >= max_lines:
                break
        unique: List[str] = []
        for line in lines:
            if line not in unique:
                unique.append(line)
        return unique[:max_lines]

    def _verify_query_variants(self, seed: str) -> List[str]:
        # 三种不同问法，让「多轮检索」不是同一句话重复查，减少被单条结果带偏
        base = _text(seed)[:40]
        return [f"{base} 什么意思 网络用语", f"{base} 梗 出处 用法", f"{base} 含义 解释"]

    async def _verify_targets(
        self,
        kind: str,
        items: List[Dict[str, Any]],
        *,
        rounds: int,
        budget: int,
        max_attempts: int,
        min_confidence: float,
        label: str,
        schema_hint: str,
    ) -> Dict[str, Any]:
        """统一自证：每条候选带【群内用法】+ 2~3 轮【联网检索】，一致才锁定。"""
        store = self._verified_store(kind)
        rounds = max(2, min(3, int(rounds)))
        budget = max(0, int(budget))
        verdicts: Dict[str, Any] = {}
        picked: List[Dict[str, Any]] = []
        for item in items:
            key = _text(item.get("key"))
            if not key:
                continue
            record = store.get(key) or {}
            if record.get("status") == "locked":
                continue
            if _as_int(record.get("attempts")) >= int(max_attempts):
                continue
            picked.append(item)
            if len(picked) >= budget:
                break
        if not picked:
            return verdicts
        blocks: Dict[str, List[str]] = {}
        for item in picked:
            key = _text(item.get("key"))
            lines: List[str] = []
            group_lines = item.get("group_lines") or []
            if group_lines:
                lines.append("【群内用法】")
                lines.extend(f"  {line}" for line in group_lines[:6])
            else:
                lines.append("【群内用法】近段时间群聊里没找到更多上下文（以词频和历史证据为准）")
            seed = _text(item.get("seed"))[:40]
            if self.config.web.enabled and seed:
                for idx, query in enumerate(self._verify_query_variants(seed)[:rounds]):
                    try:
                        info = await self._web_search_term(seed, query)
                    except Exception as exc:  # noqa: BLE001
                        lines.append(f"【联网第{idx + 1}轮】检索失败：{exc}")
                        continue
                    hits: List[str] = []
                    for hit in (info.get("results") or [])[:2]:
                        hits.append(f"{hit.get('title', '')} | {hit.get('summary') or hit.get('snippet') or ''}")
                    page = info.get("page") or {}
                    if page.get("content"):
                        hits.append(f"正文摘要：{_text(page.get('content'))[:400]}")
                    joined = "；".join(hits) if hits else "无有效资料"
                    lines.append(f"【联网第{idx + 1}轮（{query}）】{joined}")
            blocks[key] = lines
        parts = [
            f"你在给 QQ 机器人整理{label}，做「多轮多源自证」。每条候选都有 1 组【群内用法】（真实聊天记录）和若干轮【联网检索】（不同提问）。",
            "多向的含义：既要看群里的实际用法，也要看网络资料；两边结论必须一致才算 agree=true。",
            "群内专属表达以群内用法为准：网络查不到、但群内用法清晰稳定 → agree=true 且 group_specific=true。",
            "如果群内证据和网络资料互相矛盾、或两边都太弱 → agree=false。",
            schema_hint,
            "只输出 JSON 数组，不要解释。",
            "",
        ]
        for item in picked:
            key = _text(item.get("key"))
            parts.append(f"- 候选 key={key}｜{_text(item.get('label'))[:90]}")
            parts.extend(f"  {line}" for line in blocks.get(key, []))
        try:
            text = await self._llm(
                "\n".join(parts),
                task_name=self.config.jargon.llm_task,
                max_tokens=4000,
                temperature=0.05,
            )
        except Exception:  # noqa: BLE001
            return verdicts
        parsed = _parse_json_array(text) or []
        allowed = {_text(item.get("key")) for item in picked}
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            key = _text(entry.get("key"))
            if key not in allowed:
                continue
            agree = bool(entry.get("agree"))
            confidence = round(_as_float(entry.get("confidence")), 3)
            sources = [_text(src)[:200] for src in (entry.get("sources") or []) if _text(src)][:2]
            record = store.get(key) or {}
            attempts = _as_int(record.get("attempts")) + 1
            meaning = _text(entry.get("meaning") or entry.get("description") or entry.get("style"))[:120]
            locked = agree and confidence >= float(min_confidence) and bool(meaning)
            if locked:
                store[key] = {
                    "status": "locked",
                    "meaning": meaning,
                    "confidence": confidence,
                    "rounds": rounds,
                    "sources": sources,
                    "group_specific": bool(entry.get("group_specific")),
                    "attempts": attempts,
                    "ts": time.time(),
                }
                entry["_locked"] = True
            else:
                store[key] = {
                    "status": "failed",
                    "meaning": "",
                    "confidence": confidence,
                    "rounds": rounds,
                    "sources": sources,
                    "attempts": attempts,
                    "ts": time.time(),
                }
            verdicts[key] = entry
        self._save_verified(kind)
        return verdicts

    # ===== 黑话模块 =====

    async def _run_jargon(self, report: Dict[str, Any]) -> Dict[str, Any]:
        # 流程：读黑话库 → 筛候选（出现够多且缺含义/未完成）→ 分批 LLM 判定 →
        #      低置信/新词走多轮自证（群内用法+联网）→ 写回（更新含义/删除/标记完成）→ 汇总
        cfg = self.config.jargon
        dry_run = not cfg.writeback
        result: Dict[str, Any] = {
            "dry_run": dry_run,
            "candidates": 0,
            "decisions": [],
            "web_used": 0,
            "organized": {"merged": 0, "deleted": 0},
            "rechecked": 0,
            "applied": {"updated": 0, "deleted": 0, "settled": 0, "hf_cleared": False},
            "summary": "",
            "errors": [],
        }
        rows = await self._db(JARGON_MODEL, "get", order_by="-count", limit=3000)
        if not isinstance(rows, list):
            result["errors"].append(f"读取黑话库失败: {rows!r}")
            return result
        rows = await self._jargon_organize(rows, result)
        min_count = max(1, int(cfg.min_count))
        candidates: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            content = _text(row.get("content"))
            if not content:
                continue
            count = _as_int(row.get("count"))
            meaning = _text(row.get("meaning"))
            complete = bool(row.get("is_complete"))
            if count < min_count:
                continue
            if meaning and complete:
                if not cfg.recheck_complete:
                    continue
                last_ts = _as_float(row.get("last_seen_timestamp") or row.get("updated_timestamp") or row.get("created_timestamp"))
                if last_ts > 0 and (time.time() - last_ts) < max(1, int(cfg.recheck_days)) * 86400:
                    continue
                row = dict(row)
                row["_recheck"] = True
                result["rechecked"] = _as_int(result.get("rechecked")) + 1
            candidates.append(row)
        candidates = candidates[: max(1, int(cfg.max_candidates))]
        result["candidates"] = len(candidates)
        if not candidates:
            result["summary"] = "没有需要处理的黑话候选"
            return result

        decisions: List[Dict[str, Any]] = []
        batch_size = max(1, int(cfg.batch_size))
        for start in range(0, len(candidates), batch_size):
            batch = candidates[start : start + batch_size]
            decisions.extend(await self._jargon_judge_batch(batch, result))

        # 联网复核：新收录词 / 低置信 / 模型自己要求联网的词 → 多轮多源自证（群内用法 + 联网检索），一致才固定
        result["verified_locked"] = 0
        result["verified_rounds"] = 0
        if self.config.web.enabled:
            web_budget = max(0, int(self.config.web.max_searches_per_run))
            verify_cap = max(1, int(self.config.web.verify_terms_per_run))
            web_needed: List[Dict[str, Any]] = []
            for decision in decisions:
                if len(web_needed) >= min(web_budget, verify_cap):
                    break
                record = self._jargon_verified.get(decision["content"]) or {}
                if record.get("status") == "locked":
                    continue
                if _as_int(record.get("attempts")) >= max(1, int(self.config.web.verify_max_attempts)):
                    continue
                # 新词（判定为值得收录的）也走多轮自证：群内用法 + 联网检索一致才固定
                need = (
                    bool(decision.get("needs_web"))
                    or _as_float(decision.get("confidence")) < 0.75
                    or bool(decision.get("keep"))
                )
                if need:
                    web_needed.append(decision)
            if web_needed:
                decisions = await self._jargon_verify_rounds(web_needed, decisions, result)
                result["web_used"] = result["verified_rounds"]

        max_attempts = max(1, int(self.config.web.verify_max_attempts))
        for decision in decisions:
            record = self._jargon_verified.get(decision["content"]) or {}
            if _as_int(record.get("attempts")) >= max_attempts and record.get("status") != "locked":
                decision["give_up"] = True
        result["decisions"] = decisions
        if dry_run:
            result["summary"] = f"候选 {len(candidates)} 个，判定 {len(decisions)} 条（只报告）"
            return result
        await self._jargon_apply(candidates, decisions, result)
        result["summary"] = (
            f"候选 {len(candidates)} 个，更新 {result['applied']['updated']}，"
            f"删除 {result['applied']['deleted']}，标记完成 {result['applied'].get('settled', 0)}，自证固定 {result.get('verified_locked', 0)} 个"
            f"（{result.get('verified_rounds', 0)} 轮检索）"
        )
        return result


    async def _jargon_organize(self, rows: List[Dict[str, Any]], result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """收录前整理：同一词的不同写法归一化去重、合并词频，并剔除无意义条目。"""
        cfg = self.config.jargon
        if not cfg.dedupe_similar:
            return rows
        dry_run = not cfg.writeback
        budget = max(0, int(cfg.max_writes_per_run))
        merged_count = 0
        deleted = 0

        def norm(value: str) -> str:
            text_value = _text(value).strip()
            text_value = re.sub(r"[\s\u3000]+", "", text_value)
            return text_value.lower()

        groups: Dict[str, List[Dict[str, Any]]] = {}
        dropped: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            content = _text(row.get("content"))
            key = norm(content)
            if not key or not self._jargon_meaningful(key):
                dropped.append(row)
                continue
            groups.setdefault(key, []).append(row)
        meaningful: List[Dict[str, Any]] = []
        for items in groups.values():
            if len(items) < 2:
                meaningful.extend(items)
                continue
            keeper = max(items, key=lambda row: (_as_int(row.get("count")), -_as_int(row.get("id"))))
            total = sum(_as_int(row.get("count")) for row in items)
            if not dry_run and budget > 0:
                try:
                    await self._db(JARGON_MODEL, "update", filters={"id": keeper.get("id")}, data={"count": total})
                    budget -= 1
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"黑话合并计数失败 {keeper.get('id')}: {exc}")
            keeper["count"] = total
            for row in items:
                if row is keeper:
                    continue
                merged_count += 1
                if dry_run or budget <= 0:
                    continue
                try:
                    await self._db(JARGON_MODEL, "delete", filters={"id": row.get("id")})
                    budget -= 1
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"黑话去重删除失败 {row.get('id')}: {exc}")
            meaningful.append(keeper)
        if dropped and cfg.delete_rejected and not dry_run:
            for row in dropped:
                if budget <= 0:
                    break
                try:
                    await self._db(JARGON_MODEL, "delete", filters={"id": row.get("id")})
                    budget -= 1
                    deleted += 1
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"黑话清理失败 {row.get('id')}: {exc}")
        result["organized"] = {"merged": merged_count, "deleted": deleted}
        if deleted:
            result["applied"]["deleted"] = _as_int(result["applied"].get("deleted")) + deleted
        return meaningful

    def _jargon_meaningful(self, key: str) -> bool:
        if not key:
            return False
        if len(key) > 24:
            return False
        if len(key) == 1 and key.isascii():
            return False
        if re.fullmatch(r"[\W_]+", key):
            return False
        if re.fullmatch(r"\d+", key):
            return False
        if key in _COMMON_WORDS_EXCLUDED:
            return False
        return True

    async def _jargon_evidence(self, item: Dict[str, Any], result: Dict[str, Any]) -> List[str]:
        limit = max(3, int(self.config.jargon.evidence_lines))
        lines: List[str] = []
        message_ids: List[str] = []
        groups: Any = []
        raw = item.get("evidence_messages")
        try:
            groups = json.loads(raw) if isinstance(raw, str) and raw.strip() else []
        except (ValueError, TypeError):
            groups = []
        if isinstance(groups, list):
            for group in groups:
                if not isinstance(group, list):
                    continue
                for message in group:
                    if not isinstance(message, dict):
                        continue
                    mid = _text(message.get("message_id"))
                    if mid and mid not in message_ids:
                        message_ids.append(mid)
                    if len(message_ids) >= MAX_EVIDENCE_IDS:
                        break
                if len(message_ids) >= MAX_EVIDENCE_IDS:
                    break
        anchor: Optional[Dict[str, Any]] = None
        for mid in message_ids:
            if len(lines) >= limit:
                break
            try:
                rows = await self._db(MESSAGE_MODEL, "get", filters={"message_id": mid}, limit=1)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"读取证据失败 {mid}: {exc}")
                continue
            if not isinstance(rows, list) or not rows:
                continue
            row = rows[0]
            text = _text(row.get("processed_plain_text"))
            if not text:
                continue
            speaker = _text(row.get("user_cardname") or row.get("user_nickname") or "群友")
            lines.append(f"{speaker}: {text[:160]}")
            if anchor is None:
                anchor = row
        if anchor is not None and len(lines) < limit:
            session_id = _text(anchor.get("session_id"))
            ts = _as_float(anchor.get("timestamp"))
            if session_id and ts > 0:
                try:
                    raw_window = await self.ctx.call_capability(
                        "message.get_by_time_in_chat",
                        chat_id=session_id,
                        start_time=ts - 600,
                        end_time=ts + 600,
                        limit=MAX_SURROUNDING,
                    )
                    window_messages = (
                        raw_window
                        if isinstance(raw_window, list)
                        else raw_window.get("messages")
                        if isinstance(raw_window, dict)
                        else None
                    )
                    if window_messages is not None:
                        for message in window_messages:
                            flat = self._flatten_message(message, session_id, "")
                            if flat is None:
                                continue
                            text = _text(flat.get("processed_plain_text"))
                            if not text:
                                continue
                            speaker = _text(flat.get("user_cardname") or "群友")
                            line = f"{speaker}: {text[:160]}"
                            if line not in lines:
                                lines.append(line)
                            if len(lines) >= limit:
                                break
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"读取上下文失败: {exc}")
        return lines[:limit]

    async def _jargon_judge_batch(
        self, batch: List[Dict[str, Any]], result: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        evidence: Dict[str, List[str]] = {}
        for item in batch:
            content = _text(item.get("content"))
            if not content:
                continue
            evidence[content] = await self._jargon_evidence(item, result)
        prompt = self._jargon_prompt(batch, evidence)
        try:
            text = await self._llm(
                prompt,
                task_name=self.config.jargon.llm_task,
                max_tokens=int(self.config.jargon.max_tokens),
                temperature=float(self.config.jargon.temperature),
            )
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"黑话判定失败: {exc}")
            return []
        return self._parse_jargon_decisions(text, batch, result)

    def _jargon_prompt(self, batch: List[Dict[str, Any]], evidence: Dict[str, List[str]]) -> str:
        parts = [
            "你是 QQ 群黑话词库的整理助手。下面是从群聊里自动收集的候选词，以及它们在真实聊天里出现的片段。",
            "请逐条判断：",
            "1) keep：这个词是否值得收录进黑话库，用来帮助机器人听懂群友在说什么。",
            "   值得收录：拼音缩写（zdjd=真的假的、yyds）、英文缩写或空耳（ds、be like、onp）、谐音换字（史=屎、铸=猪、窝=我）、群内反复出现且指代固定的内部梗。",
            f"   不要收录：常见词或正常表达（{_COMMON_WORDS_EXCLUDED}）、普通名词、人名昵称群名、纯数字或标点、常见英文与技术名词（bot token AI MC cos UP主 APP API）。",
            "2) meaning：若 keep 为 true，用不超过 20 个汉字说明它在群里的意思；不要复述词本身，不要编造。证据不足或不确定，就把 keep 设为 false。",
            "3) confidence：0 到 1 的置信度。",
            "4) needs_web：如果你觉得这是网络流行语/缩写，但仅凭群聊证据无法确定含义，设为 true（最多 6 个词）。",
            "5) 结合证据里的真实聊天上下文综合判断：这个词在群里怎么用、使用场景是什么；如果证据显示它已不再使用、或含义与已有释义不符，就把 keep 设为 false 或给出更准确的 meaning。",
            "6) 标记为「复核」的词条：重点看它是否仍在使用、原含义是否准确；仍准确就 keep=true 并给出 meaning，不再使用则 keep=false。",
            "",
            "候选：",
        ]
        for item in batch:
            content = _text(item.get("content"))
            if not content:
                continue
            tag = "（复核）" if item.get("_recheck") else ""
            parts.append(f"- 词：{content}{tag}（出现 {_as_int(item.get('count'))} 次）")
            lines = evidence.get(content) or []
            if lines:
                parts.append("  证据：")
                parts.extend(f"  {line}" for line in lines)
        parts.extend(
            [
                "",
                "只输出一个 JSON 数组，不要解释、不要代码块。示例：",
                '[{"content":"zdjd","keep":true,"is_jargon":true,"meaning":"真的假的","confidence":0.92,"needs_web":false}]',
                "keep 为 false 时 meaning 留空字符串。",
            ]
        )
        return "\n".join(parts)

    def _parse_jargon_decisions(
        self, text: str, batch: List[Dict[str, Any]], result: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        parsed = _parse_json_array(text)
        if parsed is None:
            result["errors"].append(f"黑话判定输出无法解析: {text[:120]}")
            return []
        known = {_text(item.get("content")) for item in batch}
        decisions: List[Dict[str, Any]] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            content = _text(entry.get("content"))
            if not content or content not in known:
                continue
            keep = bool(entry.get("keep"))
            decisions.append(
                {
                    "content": content,
                    "keep": keep,
                    "is_jargon": bool(entry.get("is_jargon", keep)),
                    "meaning": _text(entry.get("meaning"))[:60],
                    "confidence": round(_as_float(entry.get("confidence")), 3),
                    "needs_web": bool(entry.get("needs_web")),
                }
            )
        return decisions

    async def _jargon_verify_rounds(
        self,
        targets: List[Dict[str, Any]],
        decisions: List[Dict[str, Any]],
        result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """多轮多向自证：同一批词用不同提问检索 2-3 轮，LLM 比对；一致则固定含义并记住，之后不再花 token。"""
        cfg = self.config.web
        rounds = max(2, min(3, int(cfg.verification_rounds)))
        query_templates = [
            "{term} 什么意思 网络用语",
            "{term} 梗 出处 用法",
            "{term} 含义 解释",
        ]
        group_lines: Dict[str, List[str]] = {}
        if int(self.config.jargon.group_usage_hours) > 0:
            stream_ids = await self._group_stream_ids()
            for term in targets:
                content = term["content"]
                try:
                    group_lines[content] = await self._stream_usage_lines(
                        [content], stream_ids, hours=int(self.config.jargon.group_usage_hours), max_lines=5
                    )
                except Exception:  # noqa: BLE001
                    group_lines[content] = []
        evidence: Dict[str, List[Dict[str, Any]]] = {}
        for term in targets:
            content = term["content"]
            items: List[Dict[str, Any]] = []
            for round_idx in range(rounds):
                try:
                    info = await self._web_search_term(content, query_templates[round_idx].format(term=content))
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"联网自证失败 {content}: {exc}")
                    continue
                result["verified_rounds"] = _as_int(result.get("verified_rounds")) + 1
                if info:
                    items.append({"round": round_idx + 1, "info": info})
            evidence[content] = items
        if not evidence:
            return decisions

        parts = [
            "你在做黑话含义的多轮多源自证。下面每个词都有【群内用法】（真实聊天记录）和多轮【联网检索】（不同提问方式）。",
            "请逐词判断各轮资料是否指向同一个含义：",
            "1) rounds_agree：群内用法与联网结论是否一致（true/false）。只有真正一致才算 true；出现矛盾、或某轮没有资料，都算 false。",
            "1.5) 群内专属用法以群内为准：网络查不到但群内用法清晰稳定时也算 true，并在 meaning 里写群内约定含义。",
            "2) keep、is_jargon、meaning（不超过 20 个汉字）、confidence（0-1）。",
            "3) sources：主要依据的网址（最多 2 个）。",
            "只输出 JSON 数组，不要解释。",
            "",
        ]
        for term in targets:
            content = term["content"]
            items = evidence.get(content) or []
            if not items:
                continue
            parts.append(f"- 词：{content}（群内出现 {_as_int(term.get('count'))} 次）")
            glines = group_lines.get(content) or []
            if glines:
                parts.append("  【群内用法】")
                parts.extend(f"    {line}" for line in glines[:5])
            else:
                parts.append("  【群内用法】群内暂时找不到更多上下文（以词频和证据为准）")
            for item in items:
                info = item["info"]
                hit_lines: List[str] = []
                for hit in (info.get("results") or [])[:2]:
                    hit_lines.append(f"{hit.get('title', '')} | {hit.get('summary') or hit.get('snippet') or ''}")
                page = info.get("page") or {}
                if page.get("content"):
                    hit_lines.append(f"正文摘要：{_text(page.get('content'))[:500]}")
                joined = "；".join(hit_lines) if hit_lines else "无有效资料"
                parts.append(f"  第{item['round']}轮（{info.get('query', '')}）：{joined}")
        if len(parts) <= 7:
            return decisions
        try:
            text = await self._llm(
                "\n".join(parts),
                task_name=self.config.jargon.llm_task,
                max_tokens=int(self.config.jargon.max_tokens),
                temperature=0.05,
            )
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"自证判定失败: {exc}")
            return decisions
        parsed = _parse_json_array(text) or []
        allowed = {_text(t["content"]) for t in targets}
        by_content: Dict[str, Dict[str, Any]] = {d["content"]: d for d in decisions}
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            content = _text(entry.get("content"))
            if content not in allowed:
                continue
            base = by_content.get(content) or {
                "content": content,
                "keep": False,
                "is_jargon": False,
                "meaning": "",
                "confidence": 0.0,
                "needs_web": False,
            }
            agree = bool(entry.get("rounds_agree"))
            meaning = _text(entry.get("meaning"))[:60]
            confidence = round(_as_float(entry.get("confidence")), 3)
            sources = [_text(src)[:200] for src in (entry.get("sources") or []) if _text(src)][:2]
            record = self._jargon_verified.get(content) or {}
            attempts = _as_int(record.get("attempts")) + 1
            locked = agree and confidence >= float(self.config.jargon.min_confidence) and bool(meaning)
            if locked:
                self._jargon_verified[content] = {
                    "status": "locked",
                    "meaning": meaning,
                    "confidence": confidence,
                    "rounds": rounds,
                    "sources": sources,
                    "attempts": attempts,
                    "ts": time.time(),
                }
                base.update(
                    {
                        "keep": True,
                        "is_jargon": True,
                        "meaning": meaning,
                        "confidence": max(confidence, _as_float(base.get("confidence"))),
                        "needs_web": False,
                        "source": "verified",
                        "rounds": rounds,
                    }
                )
                result["verified_locked"] = _as_int(result.get("verified_locked")) + 1
            else:
                self._jargon_verified[content] = {
                    "status": "failed",
                    "meaning": _text(base.get("meaning")),
                    "confidence": confidence,
                    "rounds": rounds,
                    "sources": sources,
                    "attempts": attempts,
                    "ts": time.time(),
                }
            by_content[content] = base
        for term in targets:
            content = term["content"]
            record = self._jargon_verified.get(content) or {}
            if record.get("status") == "locked":
                continue
            if not record:
                self._jargon_verified[content] = {
                    "status": "failed",
                    "meaning": "",
                    "confidence": 0.0,
                    "rounds": rounds,
                    "sources": [],
                    "attempts": 1,
                    "ts": time.time(),
                }
        self._write_json(self._data_path("jargon_verified.json"), self._jargon_verified)
        return list(by_content.values())

    async def _jargon_apply(
        self,
        candidates: List[Dict[str, Any]],
        decisions: List[Dict[str, Any]],
        result: Dict[str, Any],
    ) -> None:
        cfg = self.config.jargon
        min_conf = float(cfg.min_confidence)
        max_writes = max(1, int(cfg.max_writes_per_run))
        by_content = {_text(item.get("content")): item for item in candidates}
        updated = 0
        deleted = 0
        settled = 0
        for decision in decisions:
            if updated + deleted >= max_writes:
                break
            candidate = by_content.get(decision["content"])
            if not candidate or candidate.get("id") is None:
                continue
            row_id = candidate.get("id")
            if decision.get("give_up") and not decision["keep"]:
                try:
                    await self._db(
                        JARGON_MODEL,
                        "update",
                        filters={"id": row_id},
                        data={"is_complete": True, "last_inference_count": _as_int(candidate.get("count"))},
                    )
                    settled += 1
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"标记放弃失败 {decision['content']}: {exc}")
                continue
            if decision["keep"] and decision["confidence"] >= min_conf and decision["meaning"]:
                try:
                    await self._db(
                        JARGON_MODEL,
                        "update",
                        filters={"id": row_id},
                        data={
                            "meaning": decision["meaning"],
                            "is_jargon": True,
                            "is_complete": True,
                            "last_inference_count": _as_int(candidate.get("count")),
                        },
                    )
                    updated += 1
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"写回失败 {decision['content']}: {exc}")
            elif not decision["keep"] and decision["confidence"] >= min_conf:
                if cfg.delete_rejected and not _text(candidate.get("meaning")):
                    try:
                        await self._db(JARGON_MODEL, "delete", filters={"id": row_id})
                        deleted += 1
                    except Exception as exc:  # noqa: BLE001
                        result["errors"].append(f"删除失败 {decision['content']}: {exc}")
                elif _text(candidate.get("meaning")):
                    # 已有含义但未完成：判定为“不收录”时标记完成，避免反复花 token
                    try:
                        await self._db(
                            JARGON_MODEL,
                            "update",
                            filters={"id": row_id},
                            data={"is_complete": True, "last_inference_count": _as_int(candidate.get("count"))},
                        )
                        settled += 1
                    except Exception as exc:  # noqa: BLE001
                        result["errors"].append(f"标记完成失败 {decision['content']}: {exc}")
        result["applied"]["updated"] = updated
        result["applied"]["deleted"] = deleted
        result["applied"]["settled"] = settled
        if cfg.clear_high_frequency_terms and (updated or deleted):
            try:
                await self._db(HF_TERM_MODEL, "delete", filters={})
                result["applied"]["hf_cleared"] = True
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"清空高频词失败: {exc}")

    # ===== 联网检索 =====

    def _search_credentials(self) -> Tuple[str, str]:
        """联网检索凭证：只使用插件配置 [web].api_key / [web].base_url，不读取宿主安装目录中的任何文件。"""
        api_key = _text(self.config.web.api_key)
        base_url = _text(self.config.web.base_url) or "https://api.deepseek.com"
        return api_key, base_url

    async def _http_json(self, url: str, payload: Dict[str, Any], api_key: str) -> Dict[str, Any]:
        timeout = max(5, int(self.config.web.timeout_seconds))
        if httpx is not None:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload, headers={"Authorization": f"Bearer {api_key}"})
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, dict) else {}
        raise RuntimeError("当前运行环境缺少 httpx，联网检索不可用（插件会跳过联网、只用群内证据）")

    def _web_cache_key(self, term: str, query: str = "") -> str:
        if not query:
            return f"term::{term}"
        return f"term::{term}::q::{_text(query)[:80]}"

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        item = self._web_cache.get(key)
        if not isinstance(item, dict):
            return None
        ttl = max(1, int(self.config.web.cache_ttl_hours)) * 3600
        if time.time() - _as_float(item.get("ts")) > ttl:
            return None
        data = item.get("data")
        return data if isinstance(data, dict) else None

    def _cache_put(self, key: str, data: Dict[str, Any]) -> None:
        self._web_cache[key] = {"ts": time.time(), "data": data}
        self._write_json(self._data_path("web_cache.json"), self._web_cache)

    async def _web_search_term(self, term: str, query: Optional[str] = None) -> Dict[str, Any]:
        query = (query or f"{term} 什么意思 网络用语")[:120]
        cache_key = self._web_cache_key(term, query)
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        api_key, base_url = self._search_credentials()
        if not api_key:
            if not getattr(self, "_web_warned", False):
                self._web_warned = True
                self.ctx.logger.warning(
                    "[maibot-curator] 未配置 [web] api_key，联网检索已跳过（只使用群内证据）"
                )
            return {}
        if httpx is None:
            if not getattr(self, "_web_warned", False):
                self._web_warned = True
                self.ctx.logger.warning(
                    "[maibot-curator] 当前运行环境缺少 httpx，联网检索已跳过（只使用群内证据）"
                )
            return {}
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        data = await self._http_json(
            f"{base}/tools/search",
            {"query": query, "count": max(1, int(self.config.web.per_term_results)), "need_content": True},
            api_key,
        )
        results = []
        for item in (data.get("results") or [])[: max(1, int(self.config.web.per_term_results))]:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": _text(item.get("title")),
                    "url": _text(item.get("url")),
                    "snippet": _text(item.get("snippet")),
                    "summary": _text(item.get("summary")),
                }
            )
        info: Dict[str, Any] = {"query": query, "results": results}
        if results and self.config.web.fetch_top_page:
            top_url = results[0].get("url")
            if top_url:
                try:
                    page = await self._http_json(f"{base}/tools/fetch", {"url": top_url}, api_key)
                    info["page"] = {
                        "title": _text(page.get("title")),
                        "content": _text(page.get("content"))[:WEB_FETCH_MAX_CHARS],
                    }
                except Exception:  # noqa: BLE001 - 抓正文失败不影响搜索结论
                    info["page"] = {}
        self._cache_put(cache_key, info)
        return info

    # ===== 表达方式模块 =====

    async def _run_expression(self, report: Dict[str, Any]) -> Dict[str, Any]:
        # 流程：读库 → 只取 AI 学习且未勾选的条目 → embedding 聚类相似合并 →
        #      分批 LLM 判质量/改情景/删无意义 → 写回 → 对低置信条目做自证 → 汇总
        cfg = self.config.expression
        dry_run = not cfg.writeback
        result: Dict[str, Any] = {
            "dry_run": dry_run,
            "scanned": 0,
            "clusters": 0,
            "merged": 0,
            "judged": 0,
            "rewritten": 0,
            "deleted": 0,
            "checked": 0,
            "persona_judged": 0,
            "persona_deleted": 0,
            "auto_candidates": 0,
            "auto_checked": 0,
            "summary": "",
            "errors": [],
        }
        rows = await self._db(EXPRESSION_MODEL, "get", order_by="-count", limit=3000)
        if not isinstance(rows, list):
            result["errors"].append(f"读取表达库失败: {rows!r}")
            return result
        picked: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if bool(row.get("checked")):
                continue
            modified_by = _text(row.get("modified_by")).upper()
            if modified_by == "USER":
                continue
            if not _text(row.get("style")):
                continue
            picked.append(row)
        picked = picked[: max(1, int(cfg.max_rows))]
        result["scanned"] = len(picked)
        if not picked:
            result["summary"] = "没有需要优化的表达条目"
            return result

        # 1) embedding 聚类去重
        merged_away: List[int] = []
        try:
            styles = [_text(row.get("style"))[:120] for row in picked]
            vectors = await self._embed(styles)
            clusters: List[Dict[str, Any]] = []
            for row, vector in zip(picked, vectors):
                best = None
                best_sim = 0.0
                for cluster in clusters:
                    sim = _cosine(vector, cluster["centroid"])
                    if sim > best_sim:
                        best_sim = sim
                        best = cluster
                if best is not None and best_sim >= float(cfg.similarity_threshold):
                    best["items"].append(row)
                else:
                    clusters.append({"centroid": vector, "items": [row]})
            result["clusters"] = len(clusters)
            survivors: List[Dict[str, Any]] = []
            for cluster in clusters:
                items = cluster["items"]
                if len(items) == 1:
                    survivors.append(items[0])
                    continue
                keeper = max(
                    items,
                    key=lambda r: (_as_int(r.get("count")), len(_text(r.get("style"))), -_as_int(r.get("id"))),
                )
                survivors.append(keeper)
                for row in items:
                    if row is keeper:
                        continue
                    row_id = row.get("id")
                    if row_id is None:
                        continue
                    merged_away.append(row_id)
            if merged_away and cfg.writeback:
                written = 0
                for row_id in merged_away:
                    if written >= int(cfg.max_writes_per_run):
                        break
                    try:
                        await self._db(EXPRESSION_MODEL, "delete", filters={"id": row_id})
                        written += 1
                    except Exception as exc:  # noqa: BLE001
                        result["errors"].append(f"合并删除失败 {row_id}: {exc}")
                result["merged"] = written
            picked = survivors
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"聚类去重失败: {exc}")

        # 2) LLM 质量判定与改写
        judged_rows: List[Dict[str, Any]] = []
        batch_size = max(1, int(cfg.batch_size))
        writes = 0
        for start in range(0, len(picked), batch_size):
            batch = picked[start : start + batch_size]
            decisions = await self._expression_judge(batch, result)
            result["judged"] += len(decisions)
            by_id = {_as_int(row.get("id")): row for row in batch}
            for decision in decisions:
                row = by_id.get(_as_int(decision.get("id")))
                if not row:
                    continue
                judged_rows.append({"row": row, "decision": decision})
                if not cfg.writeback:
                    continue
                if writes >= int(cfg.max_writes_per_run):
                    continue
                row_id = row.get("id")
                confidence = _as_float(decision.get("confidence"))
                if not decision.get("useful"):
                    if cfg.delete_meaningless and confidence >= float(cfg.min_confidence):
                        try:
                            await self._db(EXPRESSION_MODEL, "delete", filters={"id": row_id})
                            result["deleted"] += 1
                            writes += 1
                        except Exception as exc:  # noqa: BLE001
                            result["errors"].append(f"删除表达失败 {row_id}: {exc}")
                    continue
                new_situation = _text(decision.get("situation"))[:60]
                new_style = _text(decision.get("style"))[:80]
                data: Dict[str, Any] = {}
                if new_situation and new_situation != _text(row.get("situation")):
                    data["situation"] = new_situation
                if new_style and new_style != _text(row.get("style")):
                    data["style"] = new_style
                if cfg.auto_check and confidence >= 0.85 and _as_int(row.get("count")) >= 2:
                    data["checked"] = True
                    result["checked"] += 1
                if not data:
                    continue
                try:
                    await self._db(EXPRESSION_MODEL, "update", filters={"id": row_id}, data=data)
                    result["rewritten"] += 1
                    writes += 1
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"改写表达失败 {row_id}: {exc}")
        # 2.5) 人格适配严判：对会被采用的表达再判一轮，删掉攻击性强、适用面窄、不符合角色的
        persona_deleted_ids, writes, persona_verdicts = await self._expression_persona_purge(
            judged_rows, result, max(0, int(cfg.max_writes_per_run) - writes)
        )
        await self._expression_auto_adopt(judged_rows, persona_verdicts, result)
        result.pop("_persona_verdicts", None)
        # 3) 多轮多源自证：群内用法 + 联网检索（只对低置信条目，控制 token）
        result["verified_locked"] = 0
        result["group_specific"] = 0
        vcfg = self.config.expression
        if int(vcfg.verify_terms_per_run) > 0:
            stream_ids = await self._group_stream_ids()
            items: List[Dict[str, Any]] = []
            for item in judged_rows:
                row = item["row"]
                decision = item["decision"]
                if not decision.get("useful"):
                    continue
                if _as_int(row.get("id")) in persona_deleted_ids:
                    continue
                if _as_float(decision.get("confidence")) >= 0.85:
                    continue
                seed = _text(decision.get("style") or row.get("style"))[:20].strip("，。！？…“”\"'()（）")
                if not seed:
                    continue
                sid = _text(row.get("session_id"))
                sids = [sid] if sid else stream_ids
                try:
                    group_lines = await self._stream_usage_lines(
                        [seed[:6]], sids, hours=int(vcfg.group_usage_hours), max_lines=5
                    )
                except Exception:  # noqa: BLE001
                    group_lines = []
                items.append(
                    {
                        "key": f"expr:{row.get('id')}",
                        "label": f"情景 {_text(row.get('situation'))[:30]} / 风格 {_text(row.get('style'))[:40]} / 出现 {_as_int(row.get('count'))} 次",
                        "seed": seed,
                        "group_lines": group_lines,
                    }
                )
                if len(items) >= int(vcfg.verify_terms_per_run):
                    break
            if items:
                verdicts = await self._verify_targets(
                    "expression",
                    items,
                    rounds=int(vcfg.verification_rounds),
                    budget=int(vcfg.verify_terms_per_run),
                    max_attempts=int(self.config.web.verify_max_attempts),
                    min_confidence=float(vcfg.min_confidence),
                    label="表达方式库",
                    schema_hint='每个条目输出：{"key":"expr:ID","agree":true/false,"keep":true/false,"situation":"6~14字情景","style":"30字以内可直接照着说的短句","group_specific":true/false,"confidence":0-1,"sources":["url"]}',
                )
                result["verified_locked"] = sum(1 for entry in verdicts.values() if entry.get("_locked"))
                result["group_specific"] = sum(
                    1 for entry in verdicts.values() if entry.get("_locked") and entry.get("group_specific")
                )
                if cfg.writeback:
                    for entry in verdicts.values():
                        if not entry.get("_locked"):
                            continue
                        row_id = _as_int(_text(entry.get("key")).split(":", 1)[-1])
                        data: Dict[str, Any] = {}
                        new_situation = _text(entry.get("situation") or entry.get("meaning"))[:60]
                        new_style = _text(entry.get("style"))[:80]
                        if new_situation:
                            data["situation"] = new_situation
                        if new_style:
                            data["style"] = new_style
                        if not data:
                            continue
                        try:
                            await self._db(EXPRESSION_MODEL, "update", filters={"id": row_id}, data=data)
                            result["rewritten"] += 1
                        except Exception as exc:  # noqa: BLE001
                            result["errors"].append(f"表达自证写回失败 {row_id}: {exc}")
        result["summary"] = (
            f"扫描 {result['scanned']}，合并 {result['merged']}，判定 {result['judged']}，"
            f"改写 {result['rewritten']}，删除 {result['deleted']}，自证固定 {result['verified_locked']}"
            f"，人格严判 {result['persona_judged']}（删 {result['persona_deleted']}）"
            f"，自动采纳候选 {result['auto_candidates']}（勾选 {result['auto_checked']}）"
            f"（{'只报告' if dry_run else '已写回'}）"
        )
        result["sample"] = [
            {
                "situation": _text(item["row"].get("situation"))[:40],
                "style": _text(item["row"].get("style"))[:60],
                "decision": item["decision"],
            }
            for item in judged_rows[:15]
        ]
        return result

    async def _expression_judge(self, batch: List[Dict[str, Any]], result: Dict[str, Any]) -> List[Dict[str, Any]]:
        lines = [
            "你是 QQ 群机器人表达库的整理助手。下面是机器人从群聊里自动学到的「表达方式」条目（情景 + 说话风格）。",
            "对每一条判断并改写：",
            "1) useful：这条是否是有意义的表达习惯（能帮助机器人在特定情景下说得自然）。纯碎片、半句话、无意义重复、特定人名/无关内容 → false。",
            "2) situation：如果是 useful，改写成 4~14 个字的「什么时候用」，比如「被调侃时」「想接话但没话说时」。",
            "3) style：保持原意，压缩到 30 字以内、可以直接照着说话的短句。",
            "4) confidence：0-1。",
            "只输出 JSON 数组，不要解释。示例：",
            '[{"id":1,"useful":true,"situation":"被调侃时","style":"才没有呢，别乱说","confidence":0.8}]',
            "",
        ]
        try:
            cfg_ctx = self.config.expression
            context_lines: List[str] = []
            if int(cfg_ctx.context_lines) > 0:
                sessions = [_text(row.get("session_id")) for row in batch if _text(row.get("session_id"))]
                context_lines = await self._expression_context_lines(
                    sessions[0] if sessions else "", int(cfg_ctx.context_lines)
                )
            if context_lines:
                lines.append("")
                lines.append("最近群聊（仅供判断使用场景，不要照抄）：")
                lines.extend(f"- {entry}" for entry in context_lines)
        except Exception:  # noqa: BLE001
            pass
        lines.append("条目：")
        for row in batch:
            lines.append(
                f'- id={row.get("id")} 情景：{_text(row.get("situation"))[:60]} ｜ 风格：{_text(row.get("style"))[:60]} ｜ 出现次数：{_as_int(row.get("count"))}'
            )
        try:
            text = await self._llm(
                "\n".join(lines),
                task_name=self.config.expression.llm_task,
                max_tokens=int(self.config.expression.max_tokens),
                temperature=0.2,
            )
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"表达判定失败: {exc}")
            return []
        parsed = _parse_json_array(text)
        if parsed is None:
            result["errors"].append(f"表达判定输出无法解析: {text[:120]}")
            return []
        known = {_as_int(row.get("id")) for row in batch}
        decisions: List[Dict[str, Any]] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            row_id = _as_int(entry.get("id"))
            if row_id not in known:
                continue
            decisions.append(
                {
                    "id": row_id,
                    "useful": bool(entry.get("useful")),
                    "situation": _text(entry.get("situation")),
                    "style": _text(entry.get("style")),
                    "confidence": round(_as_float(entry.get("confidence")), 3),
                }
            )
        return decisions


    async def _expression_context_lines(self, session_id: str, limit: int) -> List[str]:
        """读取最近群聊，作为判定表达使用场景的上下文。"""
        if not session_id or limit <= 0:
            return []
        try:
            now = time.time()
            raw = await self.ctx.call_capability(
                "message.get_by_time_in_chat",
                chat_id=session_id,
                start_time=now - 6 * 3600,
                end_time=now,
                limit=max(1, min(30, int(limit))),
            )
        except Exception:  # noqa: BLE001
            return []
        if isinstance(raw, list):
            messages = raw
        elif isinstance(raw, dict):
            messages = raw.get("messages")
        else:
            messages = None
        lines: List[str] = []
        for message in messages or []:
            flat = self._flatten_message(message, session_id, "")
            if not flat or self._is_bot_message(flat):
                continue
            body = _text(flat.get("processed_plain_text"))[:60]
            if not body:
                continue
            speaker = _text(flat.get("user_cardname") or flat.get("user_nickname") or "群友")[:12]
            lines.append(f"{speaker}：{body}")
        return lines[-int(limit):]

    def _expression_auto_gate(self, style: str) -> str:
        """自动采纳前的确定性过滤：返回不通过原因，空串=通过。"""
        text = _text(style).strip()
        if len(text) < 2 or len(text) > 40:
            return "长度不合适"
        if not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in text):
            return "纯符号"
        if re.search(r"[A-Za-z]{3,}|\d{2,}", text):
            return "含网络缩写或数字"
        if re.search(r"https?://|www\.", text):
            return "含链接"
        for word in _AUTO_ADOPT_BLOCK_WORDS:
            if word in text:
                return f"含不适合词:{word}"
        return ""

    async def _expression_persona_purge(
        self,
        judged_rows: List[Dict[str, Any]],
        result: Dict[str, Any],
        write_budget: int,
    ) -> Tuple[set, int, Dict[int, Dict[str, Any]]]:
        cfg = self.config.expression
        verdicts: Dict[int, Dict[str, Any]] = {}
        if not cfg.persona_filter:
            result["_persona_verdicts"] = verdicts
            return set(), 0, verdicts
        persona = await self._persona_text()
        if not persona:
            result["_persona_verdicts"] = verdicts
            return set(), 0, verdicts
        rows: List[Dict[str, Any]] = []
        for item in judged_rows:
            decision = item.get("decision") or {}
            if decision.get("useful"):
                rows.append(item["row"])
        rows = rows[: max(1, int(cfg.persona_max_rows))]
        if not rows:
            result["_persona_verdicts"] = verdicts
            return set(), 0, verdicts
        nickname = await self._bot_nickname()
        batch_size = max(1, int(cfg.batch_size))
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            for entry in await self._expression_persona_judge(batch, persona, nickname, result):
                verdicts[_as_int(entry.get("id"))] = entry
        result["persona_judged"] = len(verdicts)
        deleted: set = set()
        writes = 0
        if cfg.persona_delete and cfg.writeback:
            for row in rows:
                row_id = _as_int(row.get("id"))
                entry = verdicts.get(row_id)
                if not entry or entry.get("suitable") is not False:
                    continue
                if writes >= write_budget:
                    break
                try:
                    await self._db(EXPRESSION_MODEL, "delete", filters={"id": row_id})
                    deleted.add(row_id)
                    writes += 1
                    result["persona_deleted"] = _as_int(result.get("persona_deleted")) + 1
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"人格严判删除失败 {row_id}: {exc}")
        result["_persona_verdicts"] = verdicts
        return deleted, writes, verdicts

    async def _expression_persona_judge(
        self,
        batch: List[Dict[str, Any]],
        persona: str,
        nickname: str,
        result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        cfg = self.config.expression
        mode = _text(getattr(cfg, "persona_mode", "conservative")).strip().lower()
        lines = [
            f"你是 QQ 群机器人「{nickname or '麦麦'}」的发言审查员。机器人准备把这些「表达方式」用于群聊回复。",
            "机器人人设（节选）：",
            persona[:1000],
            "",
            f"目标性格：{_text(cfg.persona_traits)}",
            (
                "学习取向：主动向本群交流风格靠拢——允许群内流行、干净、不伤人的说法"
                "（即使与角色口吻略有出入，也可以采用）；只拦攻击性、低俗、纯碎片、容易让人不适的内容。"
                if mode == "adapt"
                else "学习取向：保守学习——优先角色一致性；群内流行但与角色口吻冲突的表达也判为不合适；"
                "只保留干净、温和、像角色本人会说的话。"
            ),
            "请判断每条表达在群聊里说出来是否合适：",
            "不合适（suitable=false）：攻击性、阴阳怪气、贬低他人；低俗擦边；只在小圈子/特定梗里成立、换群就突兀；与目标性格冲突；纯碎片或无意义；油腻、老成、过度玩梗。",
            "合适（suitable=true）：在本群聊天里自然、干净、能接得上话（adapt 模式下也接受贴合群内风格的流行说法）。",
            "只输出 JSON 数组，不要解释。示例：",
            '[{"id":1,"suitable":true,"reason":"温和自然","risk":"none"}]',
            "",
            "条目：",
        ]
        for row in batch:
            lines.append(
                f'- id={row.get("id")} 情景：{_text(row.get("situation"))[:60]} ｜ 风格：{_text(row.get("style"))[:60]}'
            )
        try:
            text = await self._llm(
                "\n".join(lines),
                task_name=cfg.llm_task,
                max_tokens=int(cfg.max_tokens),
                temperature=0.1,
            )
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"人格严判失败: {exc}")
            return []
        parsed = _parse_json_array(text)
        if parsed is None:
            result["errors"].append(f"人格严判输出无法解析: {text[:120]}")
            return []
        known = {_as_int(row.get("id")) for row in batch}
        entries: List[Dict[str, Any]] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            row_id = _as_int(entry.get("id"))
            if row_id not in known or "suitable" not in entry:
                continue
            entries.append(
                {
                    "id": row_id,
                    "suitable": bool(entry.get("suitable")),
                    "reason": _text(entry.get("reason"))[:20],
                    "risk": _text(entry.get("risk")).lower() or "none",
                }
            )
        return entries

    async def _expression_auto_adopt(
        self,
        judged_rows: List[Dict[str, Any]],
        verdicts: Dict[int, Dict[str, Any]],
        result: Dict[str, Any],
    ) -> None:
        """自动精选：人格严判通过 + 确定性过滤 + 二次复核 → 候选清单/自动勾选。"""
        cfg = self.config.expression
        mode = _text(cfg.auto_check_mode).lower() or "off"
        if mode not in ("off", "report", "on"):
            mode = "report"
        if mode == "off" and bool(cfg.auto_check):
            mode = "on"
        result["auto_candidates"] = 0
        result["auto_checked"] = 0
        if mode == "off" or not cfg.writeback:
            return
        candidates: List[Dict[str, Any]] = []
        for item in judged_rows:
            row = item["row"]
            decision = item.get("decision") or {}
            row_id = _as_int(row.get("id"))
            if not decision.get("useful"):
                continue
            if _as_float(decision.get("confidence")) < float(cfg.auto_check_min_confidence):
                continue
            if cfg.persona_filter:
                entry = verdicts.get(row_id)
                if not entry or entry.get("suitable") is not True:
                    continue
                if _text(entry.get("risk")) not in ("none", ""):
                    continue
            if self._expression_auto_gate(_text(decision.get("style") or row.get("style"))):
                continue
            candidates.append(item)
        if not candidates:
            return
        if cfg.auto_check_double_check and cfg.persona_filter:
            persona = await self._persona_text()
            nickname = await self._bot_nickname()
            if persona:
                confirmed: set = set()
                rows = [item["row"] for item in candidates]
                batch_size = max(1, int(cfg.batch_size))
                for start in range(0, len(rows), batch_size):
                    batch = rows[start : start + batch_size]
                    for entry in await self._expression_persona_judge(batch, persona, nickname, result):
                        if entry.get("suitable") is True and _text(entry.get("risk")) in ("none", ""):
                            confirmed.add(_as_int(entry.get("id")))
                candidates = [item for item in candidates if _as_int(item["row"].get("id")) in confirmed]
        candidates = candidates[: max(0, int(cfg.auto_check_max_per_run))]
        result["auto_candidates"] = len(candidates)
        if not candidates:
            return
        log_entries: List[Dict[str, Any]] = []
        budget = max(0, int(cfg.max_writes_per_run))
        for item in candidates:
            row = item["row"]
            decision = item.get("decision") or {}
            entry = {
                "id": row.get("id"),
                "situation": _text(decision.get("situation") or row.get("situation"))[:60],
                "style": _text(decision.get("style") or row.get("style"))[:80],
                "confidence": round(_as_float(decision.get("confidence")), 3),
                "mode": mode,
                "adopted": False,
                "ts": int(time.time()),
            }
            if mode == "on" and budget > 0:
                try:
                    await self._db(EXPRESSION_MODEL, "update", filters={"id": row.get("id")}, data={"checked": True})
                    entry["adopted"] = True
                    result["auto_checked"] = _as_int(result.get("auto_checked")) + 1
                    budget -= 1
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"自动勾选失败 {row.get('id')}: {exc}")
            log_entries.append(entry)
        if cfg.auto_check_log and log_entries:
            try:
                existing = self._read_json(self._data_path("auto_checked.json"), [])
                if not isinstance(existing, list):
                    existing = []
                existing.extend(log_entries)
                self._write_json(self._data_path("auto_checked.json"), existing[-500:])
            except Exception:  # noqa: BLE001
                pass


    # ===== 表情包模块 =====

    async def _run_emoji(self, report: Dict[str, Any]) -> Dict[str, Any]:
        # 流程：读已注册表情 → 描述 embedding 去重（保留更常用、其余取消注册）→
        #      LLM 补「画面描述｜标签｜场景」→ 写回 → 对带标签的表情做自证 → 汇总
        cfg = self.config.emoji
        dry_run = not cfg.writeback
        result: Dict[str, Any] = {
            "dry_run": dry_run,
            "scanned": 0,
            "enriched": 0,
            "duplicates": 0,
            "unregistered": 0,
            "summary": "",
            "errors": [],
        }
        rows = await self._db(IMAGE_MODEL, "get", limit=5000)
        if not isinstance(rows, list):
            result["errors"].append(f"读取表情库失败: {rows!r}")
            return result
        emojis: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _text(row.get("image_type")).upper() != "EMOJI":
                continue
            if not bool(row.get("is_registered")) or bool(row.get("is_banned")) or bool(row.get("no_file_flag")):
                continue
            emojis.append(row)
        emojis = emojis[: max(1, int(cfg.max_emoji))]
        result["scanned"] = len(emojis)
        if not emojis:
            result["summary"] = "没有需要处理的表情"
            return result

        # 1) 近似重复：按描述 embedding 聚类，保留更常用的
        try:
            descriptions = [_text(row.get("description")) or "未描述表情" for row in emojis]
            vectors = await self._embed(descriptions)
            clusters: List[Dict[str, Any]] = []
            for row, vector in zip(emojis, vectors):
                best = None
                best_sim = 0.0
                for cluster in clusters:
                    sim = _cosine(vector, cluster["centroid"])
                    if sim > best_sim:
                        best_sim = sim
                        best = cluster
                if best is not None and best_sim >= float(cfg.similarity_threshold):
                    best["items"].append(row)
                else:
                    clusters.append({"centroid": vector, "items": [row]})
            unregister: List[Dict[str, Any]] = []
            for cluster in clusters:
                items = cluster["items"]
                if len(items) < 2:
                    continue
                result["duplicates"] += 1
                keeper = max(items, key=lambda r: (_as_int(r.get("query_count")), -_as_int(r.get("id"))))
                for row in items:
                    if row is keeper:
                        continue
                    unregister.append(row)
            if unregister and cfg.writeback and cfg.dedupe_unregister:
                written = 0
                for row in unregister:
                    if written >= int(cfg.max_unregister_per_run):
                        break
                    try:
                        await self._db(IMAGE_MODEL, "update", filters={"id": row.get("id")}, data={"is_registered": False})
                        written += 1
                    except Exception as exc:  # noqa: BLE001
                        result["errors"].append(f"取消注册失败 {row.get('id')}: {exc}")
                result["unregistered"] = written
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"表情去重失败: {exc}")

        # 2) 补写描述：语气标签 + 适用场景
        if cfg.enrich_description:
            batch_size = max(1, int(cfg.batch_size))
            writes = 0
            pending = [row for row in emojis if "｜标签：" not in _text(row.get("description"))]
            for start in range(0, len(pending), batch_size):
                batch = pending[start : start + batch_size]
                decisions = await self._emoji_enrich(batch, result)
                by_id = {_as_int(row.get("id")): row for row in batch}
                for decision in decisions:
                    row = by_id.get(_as_int(decision.get("id")))
                    if not row:
                        continue
                    if not cfg.writeback or writes >= int(cfg.max_enrich_per_run):
                        continue
                    description = _text(decision.get("description"))[:120]
                    if not description or description == _text(row.get("description")):
                        continue
                    try:
                        await self._db(IMAGE_MODEL, "update", filters={"id": row.get("id")}, data={"description": description})
                        result["enriched"] += 1
                        writes += 1
                    except Exception as exc:  # noqa: BLE001
                        result["errors"].append(f"表情描述写回失败 {row.get('id')}: {exc}")
        # 3) 多轮多源自证：群内用法 + 联网检索
        result["verified_locked"] = 0
        result["group_specific"] = 0
        vcfg = self.config.emoji
        if int(vcfg.verify_terms_per_run) > 0:
            stream_ids = await self._group_stream_ids()
            items: List[Dict[str, Any]] = []
            for row in emojis:
                description = _text(row.get("description"))
                if not description or "｜标签：" not in description:
                    continue
                tag_part = description.split("｜标签：", 1)[-1]
                tags = [t.strip() for t in tag_part.replace("；", "/").split("/") if t.strip()]
                seed = ("表情 " + "、".join(tags[:2])) if tags else description[:16]
                image_hash = _text(row.get("image_hash"))
                try:
                    group_lines = (
                        await self._stream_usage_lines(
                            [image_hash], stream_ids, hours=int(vcfg.group_usage_hours), max_lines=5, hash_mode=True
                        )
                        if image_hash
                        else []
                    )
                except Exception:  # noqa: BLE001
                    group_lines = []
                items.append(
                    {
                        "key": f"emoji:{row.get('id')}",
                        "label": f"{description[:60]} / 被用 {_as_int(row.get('query_count'))} 次",
                        "seed": seed[:24],
                        "group_lines": group_lines,
                    }
                )
                if len(items) >= int(vcfg.verify_terms_per_run):
                    break
            if items:
                verdicts = await self._verify_targets(
                    "emoji",
                    items,
                    rounds=int(vcfg.verification_rounds),
                    budget=int(vcfg.verify_terms_per_run),
                    max_attempts=int(self.config.web.verify_max_attempts),
                    min_confidence=0.6,
                    label="表情包库",
                    schema_hint='每个条目输出：{"key":"emoji:ID","agree":true/false,"keep":true/false,"description":"画面描述｜标签：a/b；场景：…（总长≤100字）","group_specific":true/false,"confidence":0-1,"sources":["url"]}',
                )
                result["verified_locked"] = sum(1 for entry in verdicts.values() if entry.get("_locked"))
                result["group_specific"] = sum(
                    1 for entry in verdicts.values() if entry.get("_locked") and entry.get("group_specific")
                )
                if cfg.writeback:
                    for entry in verdicts.values():
                        if not entry.get("_locked"):
                            continue
                        row_id = _as_int(_text(entry.get("key")).split(":", 1)[-1])
                        description = _text(entry.get("description") or entry.get("meaning"))[:120]
                        if not description:
                            continue
                        try:
                            await self._db(IMAGE_MODEL, "update", filters={"id": row_id}, data={"description": description})
                            result["enriched"] += 1
                        except Exception as exc:  # noqa: BLE001
                            result["errors"].append(f"表情自证写回失败 {row_id}: {exc}")
        result["summary"] = (
            f"扫描 {result['scanned']}，重复组 {result['duplicates']}，取消注册 {result['unregistered']}，"
            f"补描述 {result['enriched']}，自证固定 {result['verified_locked']}（{'只报告' if dry_run else '已写回'}）"
        )
        return result

    async def _emoji_enrich(self, batch: List[Dict[str, Any]], result: Dict[str, Any]) -> List[Dict[str, Any]]:
        lines = [
            "你在帮 QQ 机器人整理表情包库。下面每张表情都有一段画面描述。",
            "请为每张表情写一句更实用的描述，格式固定为：画面描述｜标签：标签1/标签2；场景：什么时候适合发。",
            "要求：",
            f"- 标签从这些词里选 1~3 个：{'、'.join(_EMOJI_TAGS)}；",
            "- 场景 6~14 个字，例如「被调侃时」「想搞笑收尾时」「安慰对方时」；",
            "- 总长度不超过 100 字，不要编造看不清的内容；",
            "- 只输出 JSON 数组，不要解释。示例：",
            '[{"id":1,"description":"一只猫趴在薯条上｜标签：自嘲/看戏；场景：被调侃想躺平时"}]',
        ]
        try:
            cfg_ctx = self.config.emoji
            if int(cfg_ctx.context_lines) > 0:
                streams = await self._chat_streams("group", 1)
                session_id = _text(streams[0].get("session_id")) if streams else ""
                ctx_lines = await self._expression_context_lines(session_id, int(cfg_ctx.context_lines))
                if ctx_lines:
                    lines.append("")
                    lines.append("这个群的近期聊天（用于判断表情适用场景，不要照抄）：")
                    lines.extend(f"- {entry}" for entry in ctx_lines)
        except Exception:  # noqa: BLE001
            pass
        lines.append("")
        lines.append("表情：")
        for row in batch:
            lines.append(f'- id={row.get("id")} 描述：{_text(row.get("description"))[:100]}')
        try:
            text = await self._llm(
                "\n".join(lines),
                task_name=self.config.emoji.llm_task,
                max_tokens=int(self.config.emoji.max_tokens),
                temperature=0.3,
            )
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"表情描述生成失败: {exc}")
            return []
        parsed = _parse_json_array(text)
        if parsed is None:
            result["errors"].append(f"表情描述输出无法解析: {text[:120]}")
            return []
        known = {_as_int(row.get("id")) for row in batch}
        decisions: List[Dict[str, Any]] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            row_id = _as_int(entry.get("id"))
            if row_id not in known:
                continue
            decisions.append({"id": row_id, "description": _text(entry.get("description"))})
        return decisions


    #     体积由 inject.max_chars 管控，避免每轮都塞。

    # ===== 群友日程 + 上下文注入（v2.2 新增；v2.4 起人物档案已移除） =====
    #
    #  日程模块：抽取“正在做/准备做/计划做”的短期事件存 agenda.json；
    #  后续相关聊天里按需注入；过期或长期未提后，生成一句摘要归档到
    #  宿主人物卡 PersonInfo.memory_points（也就是 maibot 的人物长期记忆字段）。
    #
    #  注入：注册 maisaka.planner.before_request / maisaka.replyer.before_model_request
    #  两个 Hook，只在“能识别出当前对象且有内容”时插入一条 user 消息，
    #  体积由 inject.max_chars 管控，避免每轮都塞。

    def _agenda_events(self) -> List[Dict[str, Any]]:
        if not isinstance(self._agenda, dict):
            self._agenda = {"events": []}
        events = self._agenda.get("events")
        if not isinstance(events, list):
            events = []
            self._agenda["events"] = events
        return events

    def _save_agenda(self) -> None:
        self._write_json(self._data_path("agenda.json"), self._agenda)

    async def _bot_nickname(self) -> str:
        try:
            raw = await self.ctx.call_capability("config.get", key="bot.nickname", default="")
        except Exception:  # noqa: BLE001
            return ""
        value = raw.get("value") if isinstance(raw, dict) else raw
        return _text(value)

    async def _persona_text(self) -> str:
        """读取宿主人格设定，拼成表达严判用的角色描述（只走 config.get 能力）。"""
        if self._persona_cache is not None:
            return self._persona_cache
        cfg = self.config.expression
        cap = max(200, int(getattr(cfg, "persona_max_chars", 1200) or 1200))
        parts: List[str] = []
        used = 0
        for key in (
            "personality.personality",
            "personality.behavior_style",
            "personality.reply_style",
            "chat.reply_style.group_chat_prompt",
        ):
            if used >= cap:
                break
            try:
                raw = await self.ctx.call_capability("config.get", key=key, default="")
            except Exception:  # noqa: BLE001
                raw = ""
            value = raw.get("value") if isinstance(raw, dict) else raw
            text = _text(value).strip()
            if not text:
                continue
            parts.append(text)
            used += len(text)
        self._persona_cache = "\n".join(parts)[:cap]
        return self._persona_cache

    async def _person_id(self, user_id: str, platform: str = "qq") -> str:
        """QQ 号 → 宿主 person_id（跨群/私聊同一人）。"""
        uid = _text(user_id)
        if not uid:
            return ""
        try:
            raw = await self.ctx.call_capability("person.get_id", platform=platform, user_id=uid)
        except Exception:  # noqa: BLE001
            return ""
        if isinstance(raw, dict):
            return _text(raw.get("person_id"))
        # SDK 归一化后 person_id 直接是字符串
        return _text(raw)

    async def _session_row(self, session_id: str) -> Dict[str, Any]:
        """查 chat_sessions，拿当前会话是群聊还是私聊、对面是谁。"""
        sid = _text(session_id)
        if not sid:
            return {}
        try:
            rows = await self._db("ChatSession", "get", filters={"session_id": sid}, limit=1)
        except Exception:  # noqa: BLE001
            return {}
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
        return {}

    async def _chat_streams(self, kind: str = "group", limit: int = 3) -> List[Dict[str, Any]]:
        """取最近活跃的群聊/私聊会话列表（优先运行时，回退 DB）。"""
        cap = "chat.get_group_streams" if kind == "group" else "chat.get_private_streams"
        out: List[Dict[str, Any]] = []
        try:
            raw = await self.ctx.call_capability(cap, platform="qq")
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("[maibot-curator] 读取会话列表失败(%s): %s", cap, exc)
            raw = None
        streams = raw.get("streams") if isinstance(raw, dict) else raw
        for item in streams or []:
            if not isinstance(item, dict):
                continue
            sid = _text(item.get("session_id") or item.get("stream_id"))
            if not sid:
                continue
            row = dict(item)
            row["session_id"] = sid
            out.append(row)
        if not out:
            # 重启后运行时会话表可能还没建立（要等新消息），直接查 chat_sessions 兜底。
            try:
                rows = await self._db(
                    "ChatSession", "get",
                    order_by="-last_active_timestamp",
                    limit=max(1, int(limit)) * 4,
                )
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("[maibot-curator] 读取 chat_sessions 兜底失败: %s", exc)
                rows = []
            self.ctx.logger.info(
                "[maibot-curator] %s 会话：运行时=%d 条，DB 兜底=%d 条",
                kind,
                len(streams or []),
                len(rows or []),
            )
            for item in rows or []:
                if not isinstance(item, dict):
                    continue
                is_group = bool(_text(item.get("group_id")))
                if kind == "group" and not is_group:
                    continue
                if kind == "private" and is_group:
                    continue
                sid = _text(item.get("session_id"))
                if not sid:
                    continue
                out.append({
                    "session_id": sid,
                    "platform": _text(item.get("platform")) or "qq",
                    "group_id": _text(item.get("group_id")),
                    "group_name": _text(item.get("group_name")),
                    "user_id": _text(item.get("user_id")),
                    "user_nickname": _text(item.get("user_nickname")),
                    "user_cardname": _text(item.get("user_cardname")),
                    "is_group_session": is_group,
                })
        return out[: max(1, int(limit))]

    async def _scan_messages(self, streams: List[Dict[str, Any]], hours: float, limit: int) -> List[Dict[str, Any]]:
        """批量读取多个会话的最近消息，按时间升序返回（已拍平成统一结构）。"""
        rows: List[Dict[str, Any]] = []
        await self._bot_account_ids()
        for stream in streams:
            sid = _text(stream.get("session_id"))
            if not sid:
                continue
            end_ts = time.time()
            start_ts = end_ts - max(1.0, float(hours)) * 3600
            try:
                # 使用 SDK 公开签名的时间范围能力（chat_id / start_time / end_time / limit）
                raw = await self.ctx.call_capability(
                    "message.get_by_time_in_chat",
                    chat_id=sid,
                    start_time=start_ts,
                    end_time=end_ts,
                    limit=int(max(1, limit)),
                )
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("[maibot-curator] 读取消息失败(chat=%s): %s", sid, exc)
                continue
            # SDK 归一化后这里通常是 list；旧版/异常情况下可能是 dict
            if isinstance(raw, list):
                messages = raw
            elif isinstance(raw, dict):
                if raw.get("success") is False:
                    self.ctx.logger.warning("[maibot-curator] 读取消息返回异常(chat=%s): %s", sid, raw)
                    continue
                messages = raw.get("messages")
            else:
                self.ctx.logger.warning("[maibot-curator] 读取消息返回未知结构(chat=%s): %s", sid, type(raw))
                continue
            chat_name = _text(
                stream.get("group_name") or stream.get("user_nickname") or stream.get("group_id") or sid
            )
            for msg in messages or []:
                flat = self._flatten_message(msg, sid, chat_name)
                if flat is not None and not self._is_bot_message(flat):
                    rows.append(flat)
        rows.sort(key=lambda m: _as_float(m.get("timestamp")))
        self.ctx.logger.info(
            "[maibot-curator] 扫描会话 %d 个，读到消息 %d 条", len(streams), len(rows)
        )
        return rows

    def _flatten_message(
        self, msg: Any, chat_id: str, chat_name: str
    ) -> Optional[Dict[str, Any]]:
        """把宿主消息结构拍平成 {user_id, 昵称, processed_plain_text, timestamp, ...}。"""
        if not isinstance(msg, dict):
            return None
        info = msg.get("message_info") if isinstance(msg.get("message_info"), dict) else {}
        user = info.get("user_info") if isinstance(info.get("user_info"), dict) else {}
        uid = _text(msg.get("user_id") or user.get("user_id"))
        nick = _text(
            msg.get("user_cardname")
            or msg.get("user_nickname")
            or user.get("user_cardname")
            or user.get("user_nickname")
            or uid
        )
        body = _text(msg.get("processed_plain_text"))
        if not body:
            parts = msg.get("raw_message")
            if isinstance(parts, list):
                texts = [
                    _text(p.get("data")) for p in parts
                    if isinstance(p, dict) and _text(p.get("type")) == "text"
                ]
                body = _text("".join(t for t in texts if t))
        if not uid and not body:
            return None
        return {
            "message_id": _text(msg.get("message_id")),
            "timestamp": _as_float(msg.get("timestamp")),
            "user_id": uid,
            "user_cardname": nick,
            "user_nickname": nick,
            "processed_plain_text": body,
            "_chat_id": chat_id,
            "_chat_name": chat_name,
        }

    async def _bot_account_ids(self) -> set:
        """宿主上报的 Bot 平台账号集合（用于过滤 Bot 自己的发言，不依赖宿主私有参数）。"""
        cached = getattr(self, "_bot_ids", None)
        if isinstance(cached, set):
            return cached
        ids: set = set()
        try:
            rows = await self._db("BotPlatformAccount", "get", limit=20)
            for row in rows or []:
                if isinstance(row, dict):
                    account_id = _text(row.get("account_id"))
                    if account_id:
                        ids.add(account_id)
        except Exception:  # noqa: BLE001 - 读不到就退化为不过滤
            ids = set()
        self._bot_ids = ids
        return ids

    def _is_bot_message(self, flat: Optional[Dict[str, Any]]) -> bool:
        """判断一条已拍平的消息是否由 Bot 自己发出。"""
        if not isinstance(flat, dict):
            return False
        uid = _text(flat.get("user_id"))
        if not uid:
            return False
        return uid in (getattr(self, "_bot_ids", None) or set())

    def _message_speaker(self, msg: Dict[str, Any]) -> Tuple[str, str]:
        """返回 (user_id, 昵称)。"""
        uid = _text(msg.get("user_id"))
        nick = _text(msg.get("user_cardname") or msg.get("user_nickname") or uid)
        return uid, nick

    async def _append_person_memory(self, person_id: str, memory_line: str) -> bool:
        """把一条记忆要点追加到宿主人物卡 PersonInfo.memory_points（JSON 字符串数组）。"""
        pid = _text(person_id)
        line = _text(memory_line)
        if not pid or not line:
            return False
        try:
            rows = await self._db("PersonInfo", "get", filters={"person_id": pid}, limit=1)
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return False
        points: List[str] = []
        raw = _text(rows[0].get("memory_points"))
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, list):
                    points = [str(x) for x in loaded if isinstance(x, str)]
            except Exception:  # noqa: BLE001
                points = []
        if any(line[:16] in point and line[-16:] in point for point in points):
            return True
        points.append(line)
        points = points[-200:]
        try:
            await self._db(
                "PersonInfo",
                "update",
                filters={"person_id": pid},
                data={"memory_points": json.dumps(points, ensure_ascii=False)},
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    # ===== 日程状态机辅助（v2.3） =====

    def _agenda_text_key(self, text: str) -> str:
        """把文本压成只含汉字/字母/数字的 key，用于宽松比对同一件事。"""
        raw = _text(text)
        chars = [ch.lower() for ch in raw if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"]
        return "".join(chars)[:80]

    def _agenda_similar(self, a: str, b: str) -> bool:
        """判断两条日程是否指同一件事（更新/取消/归档时用）。"""
        ta = self._agenda_text_key(a)
        tb = self._agenda_text_key(b)
        if not ta or not tb:
            return False
        if ta == tb or ta in tb or tb in ta:
            return True
        try:
            return difflib.SequenceMatcher(None, ta, tb).ratio() >= 0.6
        except Exception:  # noqa: BLE001
            return False

    def _agenda_parse_time(self, time_hint: str, content: str, base_ts: float) -> Tuple[float, str]:
        """把“今晚/明天/周末/一会儿/之后…”换算成时间戳。

        返回 (due_ts, kind)：
        - exact    能落到具体日期+时刻（今晚 8 点、明天 15:30）
        - relative 只有日期粒度（明天、周末、3 月 5 日）
        - abstract 只有模糊相对说法（一会儿、之后、回头、晚点）——不硬猜，交给观察窗
        - none     没有时间信息
        """
        body = _text(time_hint) + " " + _text(content)
        base = float(base_ts or time.time())
        lt = time.localtime(base)
        day0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, lt.tm_wday, lt.tm_yday, -1))
        DAY = 86400.0
        # 1) 模糊说法：只记“待办”，靠后续聊天观察是否执行
        if re.search(r"(一会儿|一会|待会|待会儿|稍后|等会|等会儿|之后|回头|晚点|马上|现在|等下|等一会儿)", body):
            return 0.0, "abstract"
        # 2) 相对日期
        day_offset = None
        if re.search(r"大后天", body):
            day_offset = 3
        elif re.search(r"后天", body):
            day_offset = 2
        elif re.search(r"(明天|明日|明早|明晚)", body):
            day_offset = 1
        elif re.search(r"(今天|今日|今晚|今早|今夜)", body):
            day_offset = 0
        if day_offset is None:
            m = re.search(r"(下周末|周末|本周|这周|周[一二三四五六日天]|星期[一二三四五六日天])", body)
            if m:
                token = m.group(1)
                weekday = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
                target = 5 if "周末" in token else weekday.get(token[-1])
                if target is not None:
                    cur = lt.tm_wday
                    delta = (target - cur) % 7
                    if "下周" in body and delta == 0:
                        delta = 7
                    day_offset = delta
        # 3) 绝对日期 X月X日
        month_day = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", body)
        if month_day:
            month, day = int(month_day.group(1)), int(month_day.group(2))
            try:
                due = time.mktime((lt.tm_year, month, day, 20, 0, 0, 0, 0, -1))
            except Exception:  # noqa: BLE001
                due = 0.0
            if due and due < base - 30 * DAY:
                try:
                    due = time.mktime((lt.tm_year + 1, month, day, 20, 0, 0, 0, 0, -1))
                except Exception:  # noqa: BLE001
                    due = 0.0
            return (due if due else 0.0), "relative"
        # 4) 具体时刻
        hour = None
        minute = 0
        m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", body)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
        else:
            m = re.search(r"(\d{1,2})\s*点\s*(半|\d{1,2})?", body)
            if m:
                hour = int(m.group(1))
                if m.group(2) == "半":
                    minute = 30
                elif m.group(2) and m.group(2).isdigit():
                    minute = int(m.group(2))
        period = None
        if re.search(r"(下午|傍晚)", body):
            period = "pm"
        elif re.search(r"(晚上|今晚|夜里|今夜)", body):
            period = "night"
        elif re.search(r"中午", body):
            period = "noon"
        elif re.search(r"(早上|上午|今早)", body):
            period = "am"
        if hour is not None:
            if period == "pm" and hour < 12:
                hour += 12
            elif period == "night" and hour < 18:
                hour += 12
            elif period == "noon" and hour < 11:
                hour = 12
            due = day0 + (day_offset or 0) * DAY + hour * 3600 + minute * 60
            if day_offset is None and due < base:
                due += DAY
            return due, "exact"
        # 5) 只有日期粒度
        if day_offset is not None:
            target_day = day0 + day_offset * DAY
            if period == "night":
                return target_day + 20 * 3600, "relative"
            if period == "am":
                return target_day + 9 * 3600, "relative"
            if period == "noon":
                return target_day + 12 * 3600, "relative"
            return target_day + 12 * 3600, "relative"
        return 0.0, "none"

    def _agenda_window_seconds(self, kind: str) -> float:
        """按时间类型给出“观察窗”：窗口内没等到证据，就当作没发生。"""
        cfg = self.config.agenda
        if kind == "abstract":
            return max(1.0, float(cfg.abstract_window_hours)) * 3600
        if kind == "relative":
            return max(1.0, float(cfg.relative_window_hours)) * 3600
        return max(1.0, float(cfg.candidate_ttl_hours)) * 3600

    def _agenda_new_event(
        self,
        item: Dict[str, Any],
        uid: str,
        person_id: str,
        content: str,
        time_hint: str,
        kind: str,
        due_ts: float,
        base_ts: float,
        now: float,
        chat_id: str,
        chat_name: str,
    ) -> Dict[str, Any]:
        """新建一条待办（pending）。"""
        base = float(base_ts or now)
        if due_ts:
            resolve_by = due_ts + max(1.0, float(self.config.agenda.candidate_ttl_hours)) * 3600
        else:
            resolve_by = base + self._agenda_window_seconds(kind)
        stamp = time.strftime("%m-%d %H:%M", time.localtime(base))
        return {
            "id": uuid4().hex[:10],
            "person_id": person_id,
            "user_id": uid,
            "person_name": _text(item.get("person_name")) or uid,
            "content": content[:120],
            "time_hint": time_hint,
            "time_kind": kind,
            "due_ts": due_ts,
            "resolve_by": resolve_by,
            "chat_id": chat_id,
            "chat_name": chat_name,
            "status": "pending",
            "confidence": round(_as_float(item.get("confidence")), 3),
            "mentions": 1,
            "evidence": [f"{stamp} 原话：{_text(item.get('evidence'))[:60] or content[:60]}"],
            "created_ts": now,
            "last_mentioned_ts": now,
        }

    async def _agenda_archive(
        self, event: Dict[str, Any], now: float, kind_label: str = "已结束", dry_run: bool = False
    ) -> bool:
        """把事件收尾并归档到宿主人物卡 memory_points；dry_run=True 时只标记、不写人物卡。"""
        cfg = self.config.agenda
        event["status"] = "archived"
        event["archived_ts"] = now
        event["closed_ts"] = now
        if not cfg.archive_to_person:
            return True
        pid = _text(event.get("person_id"))
        who = _text(event.get("person_name"))
        content = _text(event.get("content"))
        due = _as_float(event.get("due_ts")) or _as_float(event.get("last_mentioned_ts")) or now
        day = time.strftime("%Y-%m-%d", time.localtime(due))
        memory_line = f"日程:{day} {who}{content}（{kind_label}）:1"
        event["archived_memory"] = memory_line
        if dry_run:  # dry-run：事件照常标记归档、报告照常统计，但不碰宿主人物卡
            return True
        if not pid:
            return False
        return await self._append_person_memory(pid, memory_line)

    def _agenda_purge(self, events: List[Dict[str, Any]], now: float) -> int:
        """定时清理：取消/丢弃的记录保留几天，已归档的保留 N 天后从文件里删掉。"""
        cfg = self.config.agenda
        keep_closed = max(1.0, float(cfg.purge_closed_days)) * 86400
        keep_archived = max(1.0, float(cfg.purge_archived_days)) * 86400
        survivors: List[Dict[str, Any]] = []
        purged = 0
        for event in events:
            if not isinstance(event, dict):
                purged += 1
                continue
            status = _text(event.get("status"))
            closed = _as_float(event.get("closed_ts") or event.get("archived_ts"))
            if status in ("cancelled", "dropped") and closed and now - closed > keep_closed:
                purged += 1
                continue
            if status == "archived" and closed and now - closed > keep_archived:
                purged += 1
                continue
            survivors.append(event)
        if purged:
            del events[:]
            events.extend(survivors)
        return purged

    # ===== 群友日程模块（v2.3） =====

    async def _run_agenda(self, report: Dict[str, Any]) -> Dict[str, Any]:
        """日程模块：抽取 → 待办池(pending) → 观察复证 → confirmed 才注入 →
        执行/取消后归档人物卡 → 定时清理。

        设计要点：
        - “今晚/明天/周末”这类能换算成时间的，先当待办；置信度够高才直接 confirmed。
        - “一会儿/之后/回头/晚点”这类抽象说法不硬猜日期，只进待办观察窗，
          靠后续聊天里的“我出门了 / 刚回来 / 吃完了”等证据判断是否执行。
        - 只有 confirmed 的事件才会注入上下文（也就是 bot 才会提及）。
        - 没确认的计划到期静默丢弃，不进人物卡；已确认并结束的才归档成一句记忆。
        """
        cfg = self.config.agenda
        dry_run = not cfg.writeback
        result: Dict[str, Any] = {
            "dry_run": dry_run,
            "scanned": 0,
            "extracted": 0,
            "pending_new": 0,
            "pending_update": 0,
            "confirmed": 0,
            "done": 0,
            "cancelled": 0,
            "dropped": 0,
            "archived": 0,
            "deferred": 0,
            "purged": 0,
            "errors": [],
            "summary": "",
        }
        if not cfg.enabled:
            result["summary"] = "模块未启用"
            return result
        streams = await self._chat_streams("group", cfg.max_streams)
        if bool(getattr(cfg, "scan_private", False)):
            streams += await self._chat_streams("private", 2)
        messages = await self._scan_messages(streams, cfg.lookback_hours, cfg.max_messages_per_stream)
        bot_name = await self._bot_nickname()
        lines: List[str] = []
        msg_by_uid: Dict[str, List[Dict[str, Any]]] = {}
        for msg in messages:
            uid, nick = self._message_speaker(msg)
            text = _text(msg.get("processed_plain_text"))
            if not uid or not text:
                continue
            if bot_name and nick == bot_name:
                continue
            ts = _as_float(msg.get("timestamp"))
            stamp = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "?"
            chat_name = _text(msg.get("_chat_name"))
            lines.append(f"[{stamp}] [{chat_name}] {nick}（{uid}）：{text[:150]}")
            msg_by_uid.setdefault(uid, []).append(msg)
        lines = lines[-max(20, int(cfg.max_messages_per_stream)):]
        result["scanned"] = len(lines)
        events = self._agenda_events()
        now = time.time()
        archive_cap = max(0, _as_int(cfg.max_archive_per_run))
        archived_this_run = 0

        async def try_archive(event: Dict[str, Any], kind_label: str) -> bool:
            """归档入口：按 [agenda] max_archive_per_run 限制每轮实际写人物卡的条数。"""
            nonlocal archived_this_run
            if not dry_run and archive_cap > 0 and archived_this_run >= archive_cap:
                result["deferred"] += 1
                return False
            ok = await self._agenda_archive(event, now, kind_label=kind_label, dry_run=dry_run)
            if ok:
                archived_this_run += 1
            return ok

        # 兼容旧版本：以前的 active 全部视为已确认，之后按新状态机处理
        for event in events:
            if isinstance(event, dict) and _text(event.get("status")) == "active":
                event["status"] = "confirmed"
        known = [
            event for event in events
            if isinstance(event, dict) and _text(event.get("status")) in ("pending", "confirmed")
        ]
        known_lines = [
            f'- id={_text(event.get("id"))} {_text(event.get("person_name"))}：{_text(event.get("content"))}'
            f'（时间：{_text(event.get("time_hint")) or "未说"}｜状态：{_text(event.get("status"))}）'
            for event in known[:20]
        ]
        if not lines and not known:
            result["summary"] = "最近没有可扫描的消息"
            return result
        prompt = (
            "你在帮 QQ 机器人维护一份「群友日程 / 待办」清单。请从下面的群聊片段里，找出每个群友"
            "自己正在做、准备做、计划做，或者刚做完的事。\n"
            "规则：\n"
            "1) 只记和他本人有关、有时效的事；不要记新闻、不要评论别人、不要猜。\n"
            "2) time_kind 分类：exact=能确定到具体时刻；relative=只有今天/明天/周末这种日期；"
            "abstract=只有一会儿/之后/回头/晚点/等会这种模糊说法；none=没有时间信息。\n"
            "3) action 分类：plan=新计划；update=补充或修改已有计划；cancel=明确取消（不去了/算了/改主意）；"
            "done=已经做完/刚做完/已经去过。\n"
            "4) 如果能对上“已有清单”里的某条，带上它的 target_id；不确定就不要带。\n"
            "5) confidence 0~1：犹豫、玩笑、别人替他说的、只说“可能”都要低分。\n"
            "6) 犹犹豫豫、商量中、最后没定的事不要输出；玩笑不要输出。\n"
            "只输出 JSON 数组，每项："
            '{"user_id":"发言里的QQ号","person_name":"昵称","content":"一句话安排",'
            '"time_kind":"exact/relative/abstract/none","time_hint":"原文时间说法，没有就空",'
            '"due_iso":"能确定就写ISO，否则空","action":"plan/update/cancel/done",'
            '"target_id":"对应已有条目id或空","confidence":0.0,"evidence":"原话片段"}\n\n'
            "已有清单：\n" + ("\n".join(known_lines) if known_lines else "（空）") + "\n\n"
            "群聊片段：\n" + "\n".join(lines[-120:])
        )
        try:
            raw = await self._llm(prompt, task_name=cfg.llm_task, max_tokens=cfg.max_tokens, temperature=cfg.temperature)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"抽取日程失败: {exc}")
            result["summary"] = "抽取失败"
            return result
        items = _parse_json_array(raw) or []
        for item in items:
            if not isinstance(item, dict):
                continue
            content = _text(item.get("content"))[:120]
            uid = _text(item.get("user_id"))
            if not content or not uid:
                continue
            result["extracted"] += 1
            person_id = await self._person_id(uid)
            if not person_id:
                continue
            action = _text(item.get("action")).lower() or "plan"
            if action not in ("plan", "update", "cancel", "done"):
                action = "plan"
            confidence = _as_float(item.get("confidence"))
            if confidence <= 0:
                confidence = 0.6
            confidence = min(1.0, max(0.0, confidence))
            time_hint = _text(item.get("time_hint"))[:24]
            rows = msg_by_uid.get(uid) or []
            base_ts = _as_float(rows[-1].get("timestamp")) if rows else now
            chat_id = _text(rows[-1].get("_chat_id")) if rows else ""
            chat_name = _text(rows[-1].get("_chat_name")) if rows else ""
            due_ts = _parse_iso_ts(item.get("due_iso"))
            kind = _text(item.get("time_kind")).lower()
            if not due_ts:
                parsed_due, parsed_kind = self._agenda_parse_time(time_hint, content, base_ts)
                due_ts = parsed_due
                if kind not in ("exact", "relative", "abstract", "none") or kind == "none":
                    kind = parsed_kind
            if kind not in ("exact", "relative", "abstract", "none"):
                kind = "none"
            target = None
            target_id = _text(item.get("target_id"))
            if target_id:
                target = next((event for event in known if _text(event.get("id")) == target_id), None)
            if target is None:
                for event in known:
                    if _text(event.get("person_id")) != person_id:
                        continue
                    if self._agenda_similar(_text(event.get("content")), content):
                        target = event
                        break
            if action == "cancel":
                if target is None:
                    result["dropped"] += 1
                    continue
                target["status"] = "cancelled"
                target["closed_ts"] = now
                target["close_reason"] = content[:80]
                result["cancelled"] += 1
                continue
            if action == "done":
                if target is None:
                    if confidence < 0.7:
                        result["dropped"] += 1
                        continue
                    target = self._agenda_new_event(
                        item, uid, person_id, content, time_hint, kind, due_ts, base_ts, now, chat_id, chat_name
                    )
                    events.append(target)
                    known.append(target)
                target["status"] = "confirmed"
                target["confirmed_ts"] = now
                if await try_archive(target, "已完成"):
                    result["archived"] += 1
                result["done"] += 1
                continue
            if target is not None:
                target["mentions"] = _as_int(target.get("mentions")) + 1
                target["last_mentioned_ts"] = now
                if due_ts:
                    target["due_ts"] = due_ts
                if time_hint:
                    target["time_hint"] = time_hint
                if len(content) > len(_text(target.get("content"))):
                    target["content"] = content
                target["confidence"] = max(_as_float(target.get("confidence")), confidence)
                evidence = target.get("evidence") if isinstance(target.get("evidence"), list) else []
                evidence.append(f"{time.strftime('%m-%d %H:%M', time.localtime(base_ts))} 原话：{content[:60]}")
                target["evidence"] = evidence[-6:]
                if not _text(target.get("person_name")):
                    target["person_name"] = _text(item.get("person_name")) or uid
                result["pending_update"] += 1
            else:
                target = self._agenda_new_event(
                    item, uid, person_id, content, time_hint, kind, due_ts, base_ts, now, chat_id, chat_name
                )
                events.append(target)
                known.append(target)
                result["pending_new"] += 1
            hits = _as_int(target.get("mentions"))
            if target.get("status") == "pending":
                clear_time = kind in ("exact", "relative") and bool(due_ts)
                if hits >= max(1, int(cfg.confirm_min_hits)) or (
                    clear_time and confidence >= float(cfg.exact_confirm_confidence)
                ):
                    target["status"] = "confirmed"
                    target["confirmed_ts"] = now
                    result["confirmed"] += 1
        # 观察窗结算：到期仍没确认的静默丢弃；已确认的到期归档
        for event in events:
            if not isinstance(event, dict):
                continue
            status = _text(event.get("status"))
            due = _as_float(event.get("due_ts"))
            last = _as_float(event.get("last_mentioned_ts") or event.get("created_ts"))
            if status == "confirmed":
                if due and now > due + float(cfg.expire_hours) * 3600:
                    if await try_archive(event, "已结束"):
                        result["archived"] += 1
                elif now - last > float(cfg.stale_days) * 86400:
                    if await try_archive(event, "已结束"):
                        result["archived"] += 1
            elif status == "pending":
                resolve_by = _as_float(event.get("resolve_by"))
                created = _as_float(event.get("created_ts"))
                if (resolve_by and now > resolve_by) or (
                    created and now - created > float(cfg.candidate_ttl_hours) * 3600
                ):
                    event["status"] = "dropped"
                    event["closed_ts"] = now
                    result["dropped"] += 1
        result["purged"] = self._agenda_purge(events, now)
        if not dry_run:
            self._save_agenda()
        result["summary"] = (
            f"扫描 {result['scanned']} 条，抽取 {result['extracted']}；"
            f"待办 +{result['pending_new']} / 更新 {result['pending_update']}；"
            f"确认 {result['confirmed']}，完成 {result['done']}，取消 {result['cancelled']}，"
            f"丢弃 {result['dropped']}，归档 {result['archived']}，清理 {result['purged']}"
            f"（{'只报告，未写人物卡' if dry_run else '已写回'}）"
        )
        if result["deferred"]:
            result["summary"] += (
                f"（归档上限 {archive_cap} 条/轮，{result['deferred']} 条顺延到下一轮）"
            )
        result["events"] = [
            {
                "who": _text(event.get("person_name")),
                "content": _text(event.get("content"))[:60],
                "status": _text(event.get("status")),
                "time": _text(event.get("time_hint")),
            }
            for event in events
            if isinstance(event, dict) and _text(event.get("status")) in ("pending", "confirmed")
        ][:12]
        return result

    async def _build_injection_block(self, kwargs: Dict[str, Any]) -> str:
        """组装「最近的安排」一段背景信息；没有内容就返回空串。"""
        cfg = self.config.inject
        if not cfg.enabled:
            return ""
        session_id = _text(kwargs.get("session_id"))
        session = await self._session_row(session_id)
        platform = _text(session.get("platform")) or "qq"
        group_id = _text(session.get("group_id"))
        private_uid = "" if group_id else _text(session.get("user_id"))
        person_ids: List[str] = []
        if private_uid:
            person_id = await self._person_id(private_uid, platform)
            if person_id:
                person_ids.append(person_id)
        elif session_id:
            end_ts = time.time()
            await self._bot_account_ids()
            try:
                raw = await self.ctx.call_capability(
                    "message.get_by_time_in_chat",
                    chat_id=session_id,
                    start_time=end_ts - 7200,
                    end_time=end_ts,
                    limit=8,
                )
            except Exception:  # noqa: BLE001
                raw = None
            if isinstance(raw, dict):
                messages = raw.get("messages")
            elif isinstance(raw, list):
                messages = raw
            else:
                messages = None
            messages = [
                self._flatten_message(item, session_id, "")
                for item in (messages or [])
                if isinstance(item, dict)
            ]
            messages = [item for item in messages if item and not self._is_bot_message(item)]
            seen: set = set()
            for msg in reversed(messages):
                if not isinstance(msg, dict):
                    continue
                uid, _nick = self._message_speaker(msg)
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                person_id = await self._person_id(uid, platform)
                if person_id:
                    person_ids.append(person_id)
                if len(person_ids) >= 2:
                    break
        parts: List[str] = []
        if cfg.agenda:
            now_ts = time.time()
            expire_hours = float(self.config.agenda.expire_hours)
            active = [
                event for event in self._agenda_events()
                if isinstance(event, dict)
                and event.get("status") == "confirmed"
                and (
                    not _as_float(event.get("due_ts"))
                    or now_ts <= _as_float(event.get("due_ts")) + expire_hours * 3600
                )
            ]
            mine = [
                event for event in active
                if session_id and _text(event.get("chat_id")) == session_id
            ]
            related = [
                event for event in active
                if event not in mine and _text(event.get("person_id")) in person_ids
            ]
            chosen = (mine + related)[: max(1, int(cfg.max_events))]
            lines = []
            for event in chosen:
                who = _text(event.get("person_name"))
                hint = _text(event.get("time_hint"))
                content = _text(event.get("content"))
                lines.append(f"- {who}：{(hint + ' ' + content).strip()}".strip())
            if lines:
                parts.append("【最近的安排】\n" + "\n".join(lines))
        if not parts:
            return ""
        block = (
            "以下是背景信息，帮你接话更自然；只在相关时用一句，不要复述、不要说“我记得”。\n"
            + "\n".join(parts)
        )
        return block[: max(120, int(cfg.max_chars))]

    async def _inject_context(self, kwargs: Dict[str, Any], flag: str) -> Dict[str, Any]:
        """把背景信息插入到最后一条 SystemMessageItem 之后（planner / replyer 共用）。"""
        cfg = self.config.inject
        if not cfg.enabled or not bool(getattr(cfg, flag, False)):
            return {"action": "continue"}
        block = await self._build_injection_block(kwargs)
        if not block:
            return {"action": "continue"}
        items = kwargs.get("items")
        use_context_items = (
            isinstance(items, list)
            and kwargs.get("item_schema_version") == CONTEXT_ITEM_SCHEMA_VERSION
        )
        messages = kwargs.get("messages")
        if use_context_items:
            new_items = list(items)
            insert_at = 0
            for index, item in enumerate(new_items):
                if isinstance(item, dict) and item.get("item_type") == "SystemMessageItem":
                    insert_at = index + 1
            new_items.insert(insert_at, _new_user_context_item(block))
            kwargs["items"] = new_items
        elif isinstance(messages, list):
            insert_at = 0
            for index, item in enumerate(messages):
                if isinstance(item, dict) and item.get("role") == "system":
                    insert_at = index + 1
            messages.insert(insert_at, {"role": "user", "content": block})
            kwargs["messages"] = messages
        else:
            return {"action": "continue"}
        return {"action": "continue", "modified_kwargs": kwargs}

    @HookHandler(
        "maisaka.planner.before_request",
        name="inject_agenda_planner",
        description="在 planner 请求前注入群友日程（按需、限字）。",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=15000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def inject_agenda_planner(self, **kwargs: Any) -> Dict[str, Any]:
        try:
            return await self._inject_context(kwargs, "in_planner")
        except Exception as exc:  # noqa: BLE001 - 注入失败不中断聊天
            self.ctx.logger.warning("[maibot-curator] planner 注入失败，已跳过: %s", exc)
            return {"action": "continue"}

    @HookHandler(
        "maisaka.replyer.before_model_request",
        name="inject_agenda_replyer",
        description="在 replyer 请求前注入群友日程（按需、限字）。",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=15000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def inject_agenda_replyer(self, **kwargs: Any) -> Dict[str, Any]:
        try:
            return await self._inject_context(kwargs, "in_replyer")
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("[maibot-curator] replyer 注入失败，已跳过: %s", exc)
            return {"action": "continue"}

def create_plugin() -> LearningKeeperPlugin:
    """插件入口：Runner 通过这个工厂函数拿到插件实例。"""
    return LearningKeeperPlugin()
