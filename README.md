# 麦麦很好奇（Memory Keeper）

> *“你们在聊什么呀，麦麦我很好奇！”*

面向 MaiBot 的后台记忆插件，职责分为两段：

- **整理（curation）**：周期性巡检宿主学习产生的黑话、表达方式、表情包三套数据，依据群内真实用法与联网检索判定条目质量——不确定的留在观察池，确认后才写回。
- **供给（supply）**：从群聊中抽取群友的短期安排与近况，在请求模型前以克制的形式注入上下文，让接话更准确。

两段共用同一套调度、判定、报告与容错设施；供给段可整体关闭（`[agenda].enabled=false`），关闭后不读取群消息、不注入任何内容，整理段不受影响。

## 环境要求

- MaiBot `>= 1.3.0`（开发/测试环境：MaiBot 1.3.15、SDK 2.16.0）
- Plugin SDK `>= 2.8.0`
- 不新增第三方依赖：联网默认使用宿主的统一联网服务（`web.search` / `web.fetch` 能力），无需自备 Key；也可以在 `[web] api_key` 填写自己的 DeepSeek Key，让插件直连 `/v1/tools/search`（复用宿主自带的 `httpx`，可选 `fetch_top_page` 用 `/v1/tools/fetch` 抓取首条正文）。两种方式都不可用时自动退化为只使用群内证据。LLM 与 embedding 均走宿主能力。

## 设计原则

1. **证据优先**：写入或删除前必须有证据（群内真实用法或联网检索结果）；置信度不足的条目进入待观察池，而不是直接生效。
2. **渐进可逆**：支持 dry-run（含人物卡归档，dry-run 时不写库、不写人物卡）；日程采用 `pending → confirmed` 两阶段确认；近况带 TTL 自动淡忘；所有写回都有单轮上限。
3. **最小权限**：只调用 manifest 声明的宿主能力；不直接读取宿主安装目录下的文件；状态与报告只写到插件数据目录 `data/plugins/github.jiakeju.maibot-curator/`。
4. **注入克制**：只注入已确认且未过期的条目，有条数与字数上限，并提示模型按自己的人格口吻转述。

## 功能

| 段 | 模块 | 做什么 | 默认频率 |
|---|---|---|---|
| 整理 | 黑话（jargon） | 从上下文取证据 → 首轮判定 → 群内用法 + 联网 2 轮多源自证 → 高置信才写 `meaning`；碎片/常见词删除 | 每 6 小时 |
| 整理 | 表达（expression） | embedding 聚类合并近似条目 → 质量与场景判定 → 人格适配严判 → 满足门槛才自动勾选 | 每 12 小时 |
| 整理 | 表情（emoji） | 为表情生成「画面描述｜标签｜适用场景」，近似重复保留常用的、其余取消注册 | 每月 |
| 供给 | 群友近况 / 日程（agenda） | 抽取群聊里的短期安排与群友短时状态，`pending → confirmed → done/cancelled`；确认后才允许注入上下文；日程结束归档到人物卡，近况到期自动淡忘 | 每小时 |

多轮多源自证：每条候选同时看「群内用法（真实消息证据）」和「联网检索（多种问法）」，两边一致才锁定。

日程不会瞎猜：一会儿/待会/之后这类抽象时间进入观察窗（默认 24 小时），由后续聊天证据决定是否确认；明确的日期/时刻才直接确认。只有 `confirmed` 的事件会进入上下文，避免把「没定的事」说成事实。

群友近况是「短时印象」：只记几个小时到一两天内与这个人有关的状态（在加班、感冒、考试周、出差、心情不好、刚搬完家等），不要求具体时间；默认保留 48 小时，无人再提即淡忘，不写入人物卡。注入时会标注「约几小时前提过」；最近状态偏负面时附一句关心提示；注入内容会提醒模型「用你自己的口吻来接」，减少生硬复述。

## 安装

- 插件市场：搜索「麦麦很好奇」安装，安装后到「管理插件」启用
- 或从 Git 安装：`https://github.com/JiaKeJu/maibot-curator`
- 或下载 Release 里的 ZIP，用插件扩展页「从 ZIP 安装插件」上传

## 配置

配置由 Runner 在首次加载时按 `config_model` 自动生成到插件目录 `config.toml`（全部字段带中文注释；**仓库不包含该文件**，升级不会覆盖/冲突）。

在 WebUI 的插件配置页里，未填写的输入框会直接显示默认值，输入框下方的说明文字就是下表的「说明」列（含取值范围与示例）——不用翻文档也能看懂每个参数控制什么。下面按面板顺序逐项列出：

### 插件

| 参数 | 说明 | 默认 |
|---|---|---|
| `enabled` | 插件总开关（true/false，默认 true；关闭后不再巡检，也不再注入上下文） | `true` |
| `config_version` | 配置版本（内部记录用，随插件升级自动更新，默认与插件版本一致；一般无需修改） | `0.1.6` |

### 调度

| 参数 | 说明 | 默认 |
|---|---|---|
| `tick_minutes` | 调度器多久检查一次到期模块（分钟，默认 30；范围 1–1440；例：30=每半小时检查一次） | `30` |
| `run_on_load` | 插件加载后是否立刻执行一轮到期模块（true/false，默认 true） | `true` |
| `startup_delay_seconds` | 加载后等待多少秒再开始首轮巡检（秒，默认 90；范围 0–600） | `90` |

### 黑话学习

| 参数 | 说明 | 默认 |
|---|---|---|
| `enabled` | 是否启用黑话学习模块（true/false，默认 true） | `true` |
| `interval_minutes` | 黑话模块运行间隔（分钟，默认 240=4 小时；范围 0–43200；0=不自动运行） | `240` |
| `batch_size` | 每次交给 LLM 判定的候选词数量（默认 6；范围 1–50） | `6` |
| `max_candidates` | 单次最多处理多少个候选词（默认 30；范围 1–500） | `30` |
| `min_count` | 一个词至少出现过多少次才进入候选（默认 2；范围 1–100） | `2` |
| `evidence_lines` | 每个候选词最多带多少条上下文证据（默认 12；范围 1–50） | `12` |
| `llm_task` | 调用宿主哪个 LLM 任务（默认 utils；示例：utils / chat，需与宿主模型配置里的任务名一致） | `utils` |
| `max_tokens` | 单次 LLM 输出 token 上限（默认 4000；范围 256–32768） | `4000` |
| `temperature` | LLM 采样温度（默认 0.1；范围 0–2；越低越保守） | `0.1` |
| `writeback` | 关闭时只出报告；打开后才会更新/删除黑话库（true/false，默认 true；首次建议先 false 观察报告） | `true` |
| `min_confidence` | 低于该置信度的判定不写回（默认 0.6；范围 0–1；例：0.6=置信度 60% 以上才写） | `0.6` |
| `delete_rejected` | 把判定为常见词/无意义的候选从黑话库删除（true/false，默认 true） | `true` |
| `clear_high_frequency_terms` | 写回成功时清空高频词表（整表清空、不可恢复；只影响黑话匹配打分，一般保持关闭；true/false，默认 false） | `false` |
| `max_writes_per_run` | 单次最多写回多少条黑话（默认 120；范围 0–1000；0=不写回） | `120` |
| `group_usage_hours` | 自证时回看群内用法的时间窗口（小时，默认 336=14 天；范围 1–8760） | `336` |
| `dedupe_similar` | 收录前是否先整理：同一词的不同写法归一化去重、合并词频（true/false，默认 true） | `true` |
| `recheck_complete` | 是否定期复核已完成的词条（看是否仍在使用、含义是否准确；true/false，默认 true） | `true` |
| `recheck_days` | 已完成词条多少天未复核才重新判定（默认 30；范围 1–365） | `30` |
| `context_lines` | 判定时参考的群聊上下文条数（默认 6；范围 0–30；0=只使用该词自身证据） | `6` |
| `new_word_enabled` | 是否开辟「新词通道」：对刚出现、词频还没到 `min_count` 的新词单独判定（true/false，默认 true） | `true` |
| `new_word_days` | 新词通道只处理创建多少天内的词（默认 14；范围 1–90） | `14` |
| `new_word_min_age_hours` | 新词至少出现多少小时后才判定（小时，默认 0=出现即学；范围 0–240；调大可避免一时玩梗被误收录） | `0` |
| `new_word_min_evidence` | 新词至少要有几条聊天证据才判定（默认 1；范围 1–20；证据不足跳过） | `1` |
| `new_word_max_per_run` | 每轮最多处理多少个新词（默认 60；范围 0–300；0=关闭新词通道；不占用 `max_candidates` 名额） | `60` |
| `new_word_batch_threshold` | 新词累计到多少条后才统一批量识别（默认 10；范围 1–300；未达阈值先积攒，减少零散调用） | `10` |
| `new_word_batch_size` | 新词批量识别时每次交给 LLM 的条数（默认 6；范围 1–50；与普通候选批次分开，避免大批量输出截断） | `6` |
| `new_word_rejudge_days` | 新词判过且证据没变化时，多少天内不重复判定（证据变多会自动重判），范围 1–90，默认 7 天 | `7` |
| `new_word_mark_unconfirmed` | 证据不足以自证时，是否仍收录并标注「（群内说法，待确认）」（true/false，默认 true） | `true` |
| `watch_enabled` | 是否启用「收录即学」：宿主一收录新词就单独跑一轮判定，不再等下一轮巡检（true/false，默认 true） | `true` |
| `watch_interval_seconds` | 「收录即学」多久检查一次新收录（秒，默认 60；范围 15–600；越短越及时、调用略多） | `60` |
| `watch_batch_size` | 每次触发最多判定几个新词（默认 6；范围 1–20；二次出现的巩固也占这个名额） | `6` |
| `watch_max_rounds_per_hour` | 「收录即学」每小时最多触发多少轮，防止 LLM 被刷爆（默认 20；范围 1–120） | `20` |

### 联网检索

| 参数 | 说明 | 默认 |
|---|---|---|
| `enabled` | 联网检索总开关（true/false，默认 true；关闭后三个模块的自证都只使用群内证据） | `true` |
| `api_key` | 可选的 DeepSeek API Key（示例：sk-xxxx，在 platform.deepseek.com 自行申请）；留空=使用宿主「联网服务」配置的联网工具，推荐 | `""` |
| `base_url` | 仅在填写 api_key 时生效：直连的 DeepSeek API 地址（留空=默认 https://api.deepseek.com；仅建议填写可信的 API 地址） | `""` |
| `timeout_seconds` | 单次联网请求超时（秒，默认 25；范围 1–120） | `25` |
| `per_term_results` | 每个词最多取几条搜索结果（默认 3；范围 1–10） | `3` |
| `max_searches_per_run` | 单次巡检最多联网检索多少个词（默认 6；范围 0–50） | `6` |
| `fetch_top_page` | 是否抓取排名第一结果的正文摘要（true/false，默认 true；宿主模式用 web.fetch，直连模式用 /v1/tools/fetch） | `true` |
| `cache_ttl_hours` | 检索结果缓存时长（小时，默认 720=30 天；范围 1–8760；避免重复花费） | `720` |
| `verification_rounds` | 多轮自证轮数（默认 2；范围 1–5；各轮结果一致才固定含义） | `2` |
| `verify_terms_per_run` | 单次最多对多少个词做多轮自证（默认 4；范围 0–50） | `4` |
| `verify_max_attempts` | 单个词最多自证几次，超过就放弃（默认 3；范围 1–10；避免无限花 token） | `3` |

### 表达方式

| 参数 | 说明 | 默认 |
|---|---|---|
| `enabled` | 是否启用表达方式优化模块（true/false，默认 true） | `true` |
| `interval_minutes` | 表达模块运行间隔（分钟，默认 720=12 小时；范围 0–525600；0=不自动运行） | `720` |
| `batch_size` | 每次交给 LLM 处理的表达条数（默认 10；范围 1–40；太大易触发输出截断） | `10` |
| `max_rows` | 单次最多扫描多少条 AI 学习表达（默认 300；范围 1–1000） | `300` |
| `llm_task` | 调用宿主哪个 LLM 任务（默认 utils；需与宿主模型配置里的任务名一致） | `utils` |
| `max_tokens` | 单次 LLM 输出 token 上限（默认 4000；范围 256–32768） | `4000` |
| `similarity_threshold` | embedding 相似度达到多少视为重复并合并（默认 0.92；范围 0–1） | `0.92` |
| `writeback` | 关闭时只出报告；打开后才会合并/改写/删除表达（true/false，默认 true） | `true` |
| `delete_meaningless` | 是否删除判定为无意义的表达（true/false，默认 true） | `true` |
| `min_confidence` | 低于该置信度的判定不写回（默认 0.65；范围 0–1） | `0.65` |
| `max_writes_per_run` | 单次最多写回多少条表达（默认 200；范围 0–2000） | `200` |
| `verification_rounds` | 多轮自证轮数（默认 2；范围 1–5；群内用法 + 联网检索） | `2` |
| `verify_terms_per_run` | 单次最多对多少条表达做多轮自证（默认 8；范围 0–50） | `8` |
| `group_usage_hours` | 自证时回看群内用法的时间窗口（小时，默认 336=14 天；范围 1–8760） | `336` |
| `persona_filter` | 是否对将被采用的表达做「人格适配严判」（true/false，默认 true；按宿主人格设定删掉攻击性强、适用面窄、不符合角色的表达） | `true` |
| `persona_delete` | 人格严判不通过时是否直接删除（true/false，默认 true；false=只写进报告不删，建议先观察一轮） | `true` |
| `persona_max_rows` | 单次最多严判多少条表达（默认 80；范围 1–300；只判第一轮认定有意义的条目） | `80` |
| `persona_max_chars` | 读入宿主人格设定的字数上限（默认 1200；范围 200–4000；太小判断不准，太大费 token） | `1200` |
| `persona_traits` | 目标角色性格关键词（默认「友善、温和，偶尔毒舌和俏皮；不刻薄、不贬低他人、不油腻」；用顿号或分号分隔） | `友善、温和，偶尔毒舌和俏皮；不刻薄、不贬低他人、不油腻` |
| `auto_check_mode` | 自动采纳模式（下拉选择：off=不自动；report=只生成候选清单不勾选；on=合格即自动勾选；默认 report，建议先跑一轮 report 观察） | `report` |
| `auto_check_min_confidence` | 自动采纳所需的判定置信度（默认 0.9；范围 0–1） | `0.9` |
| `auto_check_double_check` | 自动采纳前是否二次复核（true/false，默认 true；多一次 LLM 调用换更高准确率） | `true` |
| `auto_check_max_per_run` | 单次最多自动勾选多少条（默认 10；范围 0–500） | `10` |
| `auto_check_log` | 是否记录自动勾选清单到 auto_checked.json（true/false，默认 true；便于检查与回滚） | `true` |
| `auto_check_ui_mark` | 自动采纳时同时把来源标记为 USER，使其出现在 WebUI「仅精选」筛选里（true/false，默认 true；真实来源始终记录在 auto_checked.json；false=只勾选 checked，不显示在仅精选） | `true` |
| `context_lines` | 判定时参考的群聊上下文条数（默认 6；范围 0–30；0=不参考上下文） | `6` |
| `persona_mode` | 人格判定取向：conservative=保守学习，优先角色一致性，群内流行但与角色口吻冲突的表达也删；adapt=主动向本群交流风格靠拢，允许群内流行、干净、不伤人的说法（与角色口吻略偏也可采用）；默认 conservative，建议先保守观察一轮再切 adapt | `conservative` |

### 表情包

| 参数 | 说明 | 默认 |
|---|---|---|
| `enabled` | 是否启用表情包库优化模块（true/false，默认 true） | `true` |
| `interval_minutes` | 表情模块运行间隔（分钟，默认 43200=30 天；范围 0–525600；0=不自动运行） | `43200` |
| `batch_size` | 每次交给 LLM 处理的表情数量（默认 8；范围 1–40） | `8` |
| `max_emoji` | 单次最多处理多少张已注册表情（默认 300；范围 1–2000） | `300` |
| `llm_task` | 调用宿主哪个 LLM 任务（默认 utils；需与宿主模型配置里的任务名一致） | `utils` |
| `max_tokens` | 单次 LLM 输出 token 上限（默认 4000；范围 256–32768） | `4000` |
| `writeback` | 关闭时只出报告；打开后才会改写描述/取消注册重复表情（true/false，默认 true） | `true` |
| `enrich_description` | 是否给表情补「语气标签 + 适用场景」（true/false，默认 true） | `true` |
| `similarity_threshold` | 描述相似度达到多少视为近似重复（默认 0.95；范围 0–1） | `0.95` |
| `dedupe_unregister` | 近似重复表情是否取消注册（true/false，默认 true；保留更常用的那张） | `true` |
| `max_unregister_per_run` | 单次最多取消注册多少张表情（默认 20；范围 0–500） | `20` |
| `max_enrich_per_run` | 单次最多补写多少条表情描述（默认 300；范围 0–2000） | `300` |
| `verification_rounds` | 多轮自证轮数（默认 2；范围 1–5；群内用法 + 联网检索） | `2` |
| `verify_terms_per_run` | 单次最多对多少张表情做多轮自证（默认 15；范围 0–50） | `15` |
| `group_usage_hours` | 自证时回看群内用法的时间窗口（小时，默认 720=30 天；范围 1–8760） | `720` |
| `context_lines` | 补写描述时参考的群聊上下文条数（默认 4；范围 0–30） | `4` |

### 群友日程

| 参数 | 说明 | 默认 |
|---|---|---|
| `enabled` | 是否启用群友日程（true/false，默认 true） | `true` |
| `interval_minutes` | 扫描间隔（分钟，默认 60=每小时；范围 1–1440） | `60` |
| `lookback_hours` | 每次回看最近多少小时的消息（小时，默认 3；范围 1–72） | `3` |
| `max_streams` | 每轮最多扫几个群/私聊会话（默认 3；范围 1–20） | `3` |
| `scan_private` | 私聊也扫描（true/false，默认 false=只扫群） | `false` |
| `max_messages_per_stream` | 每个会话最多取多少条消息（默认 200；范围 20–2000） | `200` |
| `max_events` | 注入上下文的日程条数上限（默认 5；范围 1–20） | `5` |
| `llm_task` | 调用宿主哪个 LLM 任务（默认 utils；需与宿主模型配置里的任务名一致） | `utils` |
| `max_tokens` | 单次调用最大输出 token（默认 4000；范围 256–32768） | `4000` |
| `temperature` | 采样温度（默认 0.2；范围 0–2） | `0.2` |
| `writeback` | 是否写回（true/false，默认 true；false=只出报告，dry-run，人物卡归档也会跳过） | `true` |
| `archive_to_person` | 是否把结束的日程归档到宿主人物卡 PersonInfo.memory_points（true/false，默认 true；dry-run 时不写） | `true` |
| `expire_hours` | 已确认事件过了时间多久视为结束（小时，默认 48；范围 1–720） | `48` |
| `stale_days` | 已确认事件多少天没人再提就归档（天，默认 14；范围 1–365） | `14` |
| `max_archive_per_run` | 每轮最多归档多少条（默认 10；范围 0–1000；0=不限制，超出顺延到下一轮；dry-run 不写人物卡、不受此限） | `10` |
| `candidate_ttl_hours` | 待办候选最多保留多少小时，未确认就丢弃（小时，默认 72；范围 1–720） | `72` |
| `confirm_min_hits` | 同一件事被提到几次才确认（默认 2；范围 1–20；模糊时间用） | `2` |
| `exact_confirm_confidence` | 时间明确时，置信度达到多少可直接确认（默认 0.75；范围 0–1） | `0.75` |
| `abstract_window_hours` | 「一会儿/之后」这类模糊时间的观察窗（小时，默认 24；范围 1–168） | `24` |
| `relative_window_hours` | 「明天/周末」这类只有日期的观察窗（小时，默认 36；范围 1–336） | `36` |
| `purge_closed_days` | 取消/丢弃的记录保留几天后删除（天，默认 3；范围 1–90） | `3` |
| `purge_archived_days` | 归档记录保留几天后删除（天，默认 7；范围 1–365；人物卡记忆保留） | `7` |
| `state_enabled` | 是否抽取并记录群友的短时状态/近况（在加班、感冒、考试周、出差、心情不好等），用于接话更准（true/false，默认 true） | `true` |
| `state_ttl_hours` | 短时近况保留多久（小时，默认 48；范围 2–168；期间没人再提就淡忘，不进人物卡） | `48` |
| `state_max_events` | 注入时最多带几条群友近况（默认 6；范围 1–20） | `6` |
| `state_confirm_confidence` | 近况第一次被提到时，置信度达到多少就记下（默认 0.6；范围 0–1） | `0.6` |
| `care_enabled` | 近况偏负面（生病/累/难过）时，是否在注入里给一句关心提示（true/false，默认 true） | `true` |
| `inject` | 是否在聊天请求前注入最近安排（true/false，默认 true） | `true` |

### 上下文注入

| 参数 | 说明 | 默认 |
|---|---|---|
| `enabled` | 注入总开关（true/false，默认 true） | `true` |
| `in_planner` | planner 请求前注入（true/false，默认 true） | `true` |
| `in_replyer` | replyer 请求前注入（true/false，默认 true） | `true` |
| `agenda` | 注入最近安排（true/false，默认 true） | `true` |
| `max_events` | 注入的日程条数上限（默认 5；范围 1–20） | `5` |
| `max_chars` | 注入块总字数上限，超了截断（默认 600；范围 100–2000） | `600` |
| `states` | 注入群友近况/短时状态（true/false，默认 true） | `true` |
| `care` | 近况偏负面时注入一句关心提示（true/false，默认 true） | `true` |
| `persona_style` | 注入时提醒模型用 bot 自己的人格口吻接话（true/false，默认 true） | `true` |
| `persona_max_chars` | 注入时引用人格设定的字数上限（默认 260；范围 80–1000） | `260` |

**注意**：在 WebUI 插件配置页点「保存」会按 schema 重写 `config.toml`，可能丢掉注释。想保留注释请直接编辑插件目录里的 `config.toml`（插件会自动热加载）；每次保存前 Runner 都会在插件目录的 `config_back/` 留备份，可从中找回。

## 工作原理

- **数据流**：整理段只写三类学习数据（`jargons` / `expressions` / `images`）；供给段只读消息（`message.get_by_time_in_chat`）、写自己的状态文件，并通过 `maisaka.planner.before_request` / `maisaka.replyer.before_model_request` 两个 Hook 注入【最近的安排】/【群友近况】/【关心提示】（只注入已确认、未过期的条目），同时用 bot 自己的人格口吻提醒模型接话。
- **能力边界**：数据库读写、LLM、embedding、消息检索、联网全部走宿主官方 capability（`database.query`、`llm.generate`、`llm.embed`、`message.get_by_time_in_chat`、`chat.get_group_streams`、`config.get`、`person.get_id`、`web.search`、`web.fetch` 等），不改宿主源码、不直接读取宿主安装目录下的文件（包括 `model_config.toml`）；宿主配置值（如 `bot.nickname`）与数据表只通过官方 capability 只读获取。
- **调度与容错**：主循环每 `tick_minutes` 检查到期模块；单模块异常不影响其他模块，巡检循环异常后不终止，下一轮继续；配置热更新后按新配置运行。
- **产物**：运行报告写入 `data/plugins/github.jiakeju.maibot-curator/last_report*.json`；自证台账与状态文件（`agenda.json`、`state.json` 等）也在该目录。

## 安全与隐私

- **能力范围**：仅使用 manifest 声明的 10 个宿主能力（数据库读写、消息读取、会话列表、配置读取、LLM、embedding、`web.search` / `web.fetch`）。
- **消息读取的边界**：只有启用日程/近况模块时才读取群消息；关闭该模块后不扫描、不注入。读取仅用于抽取安排与近况，不落盘聊天原文。
- **网络访问**：只使用宿主统一联网服务或直连 DeepSeek；发送内容仅为待判定词条、表情标签与检索问法，不包含聊天原文、用户 ID、群 ID。
- **数据写回**：整理段仅改动三类学习数据；供给段写入自己的状态文件，并在日程结束且 `archive_to_person=true` 时向 `PersonInfo.memory_points` 追加一句摘要。所有删除操作受单轮上限与开关控制。
- **dry-run**：`writeback=false` 时不写数据库与状态文件、不写人物卡，只输出报告，可先观察一轮行为。

## 兼容性

- MaiBot `1.3.0 – 1.3.99`，Plugin SDK `2.8.0 – 2.16.99`；无额外 Python 依赖。
- 支持配置热更新与插件热重载；升级保留 `config.toml` 与 `data/` 数据，无需迁移。

## 验证

- 静态校验：manifest、能力调用、配置字段、文档一致性（106 项）。
- 运行时仿真：以伪宿主上下文驱动全部模块与边界路径，覆盖联网两种模式、近况生命周期、注入结构（134 项）。
- 洁净检出：仅取仓库跟踪文件重建环境后导入、实例化，并通过官方 `ManifestValidator` 校验（33 项）。

## 成本

闲时成本很低：黑话每 6 小时一次（常规 8–15k token，候选少时更少）；日程/近况每小时一次（1.5–2.6k token，有候选才多）；表达/表情是周/月级。相比节省（去碎片、避免重复学习），整体是净收益；所有模块可单独关闭。

## 版本记录

- `v0.1.8`（2026-11-05）：新增黑话「收录即学」——后台每 60 秒检查宿主新收录的词，发现新词立即单独判一轮（不等巡检、每轮最多 `watch_batch_size` 个、每小时最多 `watch_max_rounds_per_hour` 轮）；新词默认「出现即学」（`new_word_min_age_hours=0`、证据 ≥1 条），判过且证据没变化的词走冷却不重复判；之前判过的词二次出现（证据变多）会自动再判一次做「巩固」，若已通过自证锁定则去掉「（群内说法，待确认）」标注。`last_report` API 增加 `watch` 字段，可查看最近一次「收录即学」结果。
- `v0.1.8`（2026-11-04）：修复黑话「新词通道」空转——每轮判 60 个新词却一条不写库、下一轮继续重判。现在每条新词判定结果会落盘到插件数据目录（`jargon_new_words.json`），并按「证据指纹」冷却：证据没变化时 `new_word_rejudge_days`（默认 7 天）内不重复判定，证据变多会自动重判；收录门槛改用 `min_confidence`（默认 0.6，仍标注「群内说法，待确认」）；新词判定提示词放宽为「群里固定使用即收录」；新增 `new_word_rejudge_days` 配置项，移除未再使用的 `new_word_min_confidence`。
- `v0.1.7`（2026-11-02）：黑话新增「新词通道」——宿主近几天大量只出现 1 次的新词，此前因 `min_count=2` 永远进不了候选；现在对创建满 24 小时、证据 ≥2 条、14 天内出现的新词单独判定（默认每轮最多 12 个，不占 `max_candidates` 名额），并要求更高置信度（默认 0.8）；联网/群内证据不足以自证时，仍会收录但含义前标注「（群内说法，待确认）」，不通过则跳过、不删除。新增 7 个 `[jargon] new_word_*` 配置项，可整体关闭。
另外按实测调优默认值：`batch_size` 6→3、`max_tokens` 4000→6000、`max_candidates` 30→60、`recheck_days` 30→90；`[web] verify_terms_per_run` 4→2，自证从 8 轮降到 4 轮，实测超时/截断报错消失。
- `v0.1.6`（2026-11-01）：表达自动采纳会同时把来源标记为 USER，因此在 WebUI「仅精选」筛选里可见（真实来源仍记录在 `auto_checked.json`，`auto_check_ui_mark=false` 可关闭该显示）；多轮自证改为按小批次（每批最多 2 条）分别请求 LLM，减少单次提示体积与 120s 硬超时风险；新增 `[expression] auto_check_ui_mark` 配置项。
- `v0.1.5`（2026-10-31）：联网检索改为默认使用宿主「联网服务」提供的 `web.search` / `web.fetch` 能力（与宿主自己的联网工具共用同一套服务与 Key），插件不再要求自备 DeepSeek API Key；`[web] api_key` 变为可选——留空走宿主、填写则插件直连 DeepSeek；两种方式都不可用时自动跳过联网、只用群内证据。同时「日程」扩展为「群友近况 / 日程」——除日程待办外，还抽取群友的短时状态（在加班、感冒、考试周、出差、心情不好等），默认保留 48 小时、没人再提自动淡忘、不进人物卡；注入新增【群友近况】与【关心提示】（近况偏负面时给一句关心建议），并按 bot 人格设定提醒模型「用你自己的口吻来接」；`[agenda]` 新增 `state_enabled`/`state_ttl_hours`/`state_max_events`/`state_confirm_confidence`/`care_enabled`，`[inject]` 新增 `states`/`care`/`persona_style`/`persona_max_chars` 并把总字数上限默认调到 600。日程抽取 prompt 改为同时识别 `kind=plan/state`，近况带 `polarity`（正/中/负）与 `topic` 关键词；manifest 新增 `web.search` / `web.fetch` 能力声明。
- `v0.1.4`（2026-10-29）：表达方式新增「人格适配严判」：对会被采用的表达，按宿主人格设定再判一轮，删掉攻击性强、阴阳怪气、适用面窄、与角色不符的条目；新增自动精选模式 `[expression] auto_check_mode`（off/report/on，默认 report 只出候选清单）、置信度门槛、二次复核、单轮勾选上限与 `auto_checked.json` 候选日志；表达判定结合群聊上下文（`context_lines`）；人格判定新增取向开关 `[expression] persona_mode`（conservative=保守学习，优先角色一致性；adapt=主动向群内交流风格靠拢，允许群内流行、干净、不伤人的说法），默认 conservative；默认每 12 小时跑一次、单轮扫 300 条。黑话改为「先整理再判断」：收录前归一化去重、合并词频、剔除无意义条目，已完成的词条定期复核（`recheck_complete`/`recheck_days`），判定结合群内真实用法与上下文；默认每 4 小时跑一次。表情补描述时也会参考群聊上下文。manifest 简介精简为两句话，技术细节保留在 README。
- `v0.1.3`（2026-10-27）：按第 5 轮复审意见补强——`[agenda] max_archive_per_run` 真正生效（每轮最多写多少条人物卡归档，超出部分顺延到下一轮，0=不限制；dry-run 不写人物卡、不受此限），`[web] base_url` 字段描述注明仅建议填写可信的 API 地址；展示名英文副名由 MaiBot Curator 调整为 Memory Keeper（中文名与插件 id 不变）。README 开头新增一句自述；配置说明按 WebUI 面板顺序重写，逐项列出每个参数的含义与取值范围，所有配置字段描述都补上默认值/范围/示例，WebUI 未填写时会直接显示提示。另：配置 Schema 生成时会自动把字段 `description` 回填为 WebUI 的 `hint`，输入框下方直接显示灰色说明（此前只有 description，WebUI 不渲染）；清理旧版遗留的死代码（3 个未调用函数、2 个未使用常量，`plugin.py` 2,974→2,877 行），新增本仓库的 `CHANGELOG.md`。
- `v0.1.2`（2026-10-27）：按复审建议收尾——manifest 移除未使用的 `message.get_recent` 能力声明；修正 docstring 里“联网走宿主同一把 Key”的旧描述；修复开关「关闭再打开」后后台巡检不会自动重启的问题；单轮巡检异常不再终止整个循环。另外按自查清理：去掉文件头 BOM、移除 `urllib` 兜底（联网只用宿主自带的 `httpx`，缺失时优雅跳过并只警告一次）。第 3 轮复审补充：`[agenda] writeback = false`（dry-run）时不再写入人物卡（`archive_to_person` 保留为独立开关），使 dry-run 承诺与实现一致；同时校正 README 里联网依赖、宿主交互与配置表的描述。
- `v0.1.1`（2026-10-27）：按市场审核意见修复——仓库不再包含 `config.toml`（改由 Runner 生成、加入 `.gitignore`）；移除对宿主 `config/model_config.toml` 的读取，联网检索改为只使用插件配置里的 `[web] api_key`；消息读取改用 SDK 官方 `message.get_by_time_in_chat`（不再依赖私有参数），并用 `BotPlatformAccount` 过滤 Bot 自己的发言；`clear_high_frequency_terms` 注明整表清空。
- `v0.1.0`（2026-10-27）：首个公开版本。黑话/表达/表情三库清洗 + 群聊日程待办；多轮多源自证；全中文注释配置。

完整修改日志（含复审反馈、自查项与代码清理细节）见 [CHANGELOG.md](CHANGELOG.md)。

## 许可

MIT，见 [LICENSE](LICENSE)。

## 免责与开发声明

### 代码由 AI 编程助手生成

本插件**绝大部分代码由 AI 编程助手生成**，而非传统人工逐行编写，特此明示：

- **开发工具与环境**：DeepSeek Harness（DSH）桌面端 Agent 工作区；生成模型为 **deepseek-v4.1**（DeepSeek 官方 API，`deepseek-flash` 通道，Responses 接口）。开发机为 Windows 11 + Git for Windows 2.55。
- **运行与测试环境**：MaiBot 1.3.15（内核 8.15.47）、maibot-plugin-sdk 2.16.0、MaiBot 内置 Python 3.12.10（`python-env`）；目标实例为 MaiBot OneKey 桌面版。
- **开发方式**：需求由人类提出并逐轮验收，代码由 AI 编写与修改；每轮通过 `py_compile`、`tomllib` 与官方 `ManifestValidator` 静态校验，并在真实 MaiBot 实例中加载运行、观察 Hook 注入与数据库写入结果。
- **已知局限**：AI 生成代码可能存在未知缺陷、边界条件遗漏或安全风险。尽管经过人工审阅与真实环境验证，本项目**不构成任何可用性、稳定性或安全性保证**；使用前请自行审查源码，尤其是数据库写入（`[jargon] writeback`、`[agenda] writeback`）与联网检索部分。
- **数据风险**：插件会按配置**修改或删除** `jargons` / `expressions` / `images` 表中的条目，并向 `PersonInfo.memory_points` 写入日程归档（`[agenda] archive_to_person` 可关）。使用前请备份 `data/MaiBot.db`，首次使用建议 `writeback = false` 以 dry-run 模式观察报告（此时不会写库，也不会写人物卡）。
- **费用**：插件通过宿主 LLM / embedding 能力产生 token 消耗，相关费用由使用者自行承担。

### 使用风险

- LLM 判断存在误差：插件通过多轮证据与置信度门槛降低误判，但无法完全避免；被删除的条目在一轮内不可恢复。
- 插件依赖 MaiBot 宿主能力与内部数据结构（`database.query` 等），宿主升级后若接口变化可能需要适配。
- 本插件按 **MIT 协议「按原样」（AS IS）提供**，作者与所使用的 AI 工具均不对因使用本插件造成的任何直接或间接损失负责。
