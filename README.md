# 麦麦很好奇（Memory Keeper）

> *“你们在聊什么呀，麦麦我很好奇！”*

让麦麦自己整理自己的「记忆库」：黑话、表达方式、表情包三套内置数据，加上一份群友日程待办。

MaiBot 内置的学习功能会不断往库里写东西，但写进来的条目往往缺少解释、彼此重复，或者只是一句话碎片。这个插件在后台定期巡检这些库，结合**群内真实用法 + 联网检索多轮自证**，把「不敢确定」的东西留在待办池里观察，确认后才写回、注入上下文或归档。所有写回（含人物卡归档）都可以先 dry-run。

## 环境要求

- MaiBot `>= 1.3.0`（开发/测试环境：MaiBot 1.3.15、SDK 2.16.0）
- Plugin SDK `>= 2.8.0`
- 不新增第三方依赖：联网复用宿主自带的 `httpx`（缺失时自动只使用群内证据），LLM/embedding 走宿主能力

## 功能

| 模块 | 做什么 | 默认频率 |
|---|---|---|
| 黑话（jargon） | 从上下文里取证据 → 首轮判定 → 群内用法 + 联网 2 轮多源自证 → 高置信才写 `meaning`；碎片/常见词删除 | 每 6 小时 |
| 表达（expression） | embedding 聚类合并近似条目，LLM 判定质量/改写场景，删除无意义表达（默认不自动勾选） | 每周 |
| 表情（emoji） | 为表情生成「画面描述｜标签｜适用场景」，近似重复保留常用的、其余取消注册 | 每月 |
| 日程（agenda） | 抽取群聊里的短期安排，`pending → confirmed → done/cancelled`，确认后才允许注入上下文；结束归档到人物卡并定时清理 | 每小时 |

多轮多源自证：每条候选同时看「群内用法（真实消息证据）」和「联网检索（多种问法）」，两边一致才锁定；联网检索通过 DeepSeek 的 `/v1/tools/search` 完成（可选 `fetch_top_page`，用 `/v1/tools/fetch` 抓首条正文），也可只用群内证据。

日程不会瞎猜：一会儿/待会/之后这类抽象时间进入观察窗（默认 24h），由后续聊天证据决定是否确认；明确的日期/时刻才直接确认。只有 `confirmed` 的事件会出现在上下文里，避免把「没定的事」说成事实。

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
| `config_version` | 配置版本（内部记录用，随插件升级自动更新，默认与插件版本一致；一般无需修改） | `0.1.4` |

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
| `max_writes_per_run` | 单次最多写回多少条黑话（默认 40；范围 0–1000；0=不写回） | `40` |
| `group_usage_hours` | 自证时回看群内用法的时间窗口（小时，默认 336=14 天；范围 1–8760） | `336` |
| `dedupe_similar` | 收录前是否先整理：同一词的不同写法归一化去重、合并词频（true/false，默认 true） | `true` |
| `recheck_complete` | 是否定期复核已完成的词条（看是否仍在使用、含义是否准确；true/false，默认 true） | `true` |
| `recheck_days` | 已完成词条多少天未复核才重新判定（默认 30；范围 1–365） | `30` |
| `context_lines` | 判定时参考的群聊上下文条数（默认 6；范围 0–30；0=只使用该词自身证据） | `6` |

### 联网检索

| 参数 | 说明 | 默认 |
|---|---|---|
| `enabled` | 联网检索总开关（true/false，默认 true；未填写 api_key 时即使开启也只使用群内证据） | `true` |
| `api_key` | DeepSeek API Key，联网检索自证用（示例：sk-xxxx；在 platform.deepseek.com 自行申请；留空=不联网，只用群内证据） | `""` |
| `base_url` | DeepSeek API 地址（留空=默认 https://api.deepseek.com；仅建议填写可信的 API 地址） | `""` |
| `timeout_seconds` | 单次联网请求超时（秒，默认 25；范围 1–120） | `25` |
| `per_term_results` | 每个词最多取几条搜索结果（默认 3；范围 1–10） | `3` |
| `max_searches_per_run` | 单次巡检最多联网检索多少个词（默认 6；范围 0–50） | `6` |
| `fetch_top_page` | 是否抓取排名第一结果的正文字摘要（true/false，默认 true） | `true` |
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
| `auto_check` | 旧版自动勾选开关（true/false，默认 false；已由 auto_check_mode 取代，仅为兼容保留） | `false` |
| `verification_rounds` | 多轮自证轮数（默认 2；范围 1–5；群内用法 + 联网检索） | `2` |
| `verify_terms_per_run` | 单次最多对多少条表达做多轮自证（默认 8；范围 0–50） | `8` |
| `group_usage_hours` | 自证时回看群内用法的时间窗口（小时，默认 336=14 天；范围 1–8760） | `336` |
| `persona_filter` | 是否对将被采用的表达做「人格适配严判」（true/false，默认 true；按宿主人格设定删掉攻击性强、适用面窄、不符合角色的表达） | `true` |
| `persona_delete` | 人格严判不通过时是否直接删除（true/false，默认 true；false=只写进报告不删，建议先观察一轮） | `true` |
| `persona_max_rows` | 单次最多严判多少条表达（默认 80；范围 1–300；只判第一轮认定有意义的条目） | `80` |
| `persona_max_chars` | 读入宿主人格设定的字数上限（默认 1200；范围 200–4000；太小判断不准，太大费 token） | `1200` |
| `persona_traits` | 目标角色性格关键词（默认「友善、温和，偶尔毒舌和俏皮；不刻薄、不贬低他人、不油腻」；用顿号或分号分隔） | `友善、温和，偶尔毒舌和俏皮；不刻薄、不贬低他人、不油腻` |
| `auto_check_mode` | 自动采纳模式（off=不自动；report=只生成候选清单不勾选；on=合格即自动勾选；默认 report，建议先跑一轮 report 观察） | `report` |
| `auto_check_min_confidence` | 自动采纳所需的判定置信度（默认 0.9；范围 0–1） | `0.9` |
| `auto_check_double_check` | 自动采纳前是否二次复核（true/false，默认 true；多一次 LLM 调用换更高准确率） | `true` |
| `auto_check_max_per_run` | 单次最多自动勾选多少条（默认 10；范围 0–500） | `10` |
| `auto_check_log` | 是否记录自动勾选清单到 auto_checked.json（true/false，默认 true；便于检查与回滚） | `true` |
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
| `inject` | 是否在聊天请求前注入最近安排（true/false，默认 true） | `true` |

### 上下文注入

| 参数 | 说明 | 默认 |
|---|---|---|
| `enabled` | 注入总开关（true/false，默认 true） | `true` |
| `in_planner` | planner 请求前注入（true/false，默认 true） | `true` |
| `in_replyer` | replyer 请求前注入（true/false，默认 true） | `true` |
| `agenda` | 注入最近安排（true/false，默认 true） | `true` |
| `max_events` | 注入的日程条数上限（默认 5；范围 1–20） | `5` |
| `max_chars` | 注入块总字数上限，超了截断（默认 420；范围 80–2000） | `420` |

**注意**：在 WebUI 插件配置页点「保存」会按 schema 重写 `config.toml`，可能丢掉注释。想保留注释请直接编辑插件目录里的 `config.toml`（插件会自动热加载）；每次保存前 Runner 都会在插件目录的 `config_back/` 留备份，可从中找回。

## 工作原理

- 通过 `maisaka.planner.before_request` / `maisaka.replyer.before_model_request` Hook，把【最近的安排】按需注入上下文（只注入已确认、且未过期的事件）。
- 数据库读写、LLM、embedding、消息检索全部走宿主官方 capability（`database.query`、`llm.generate`、`llm.embed`、`message.get_by_time_in_chat`、`chat.get_group_streams`、`config.get`、`person.get_id` 等），不改宿主源码、不直接读取宿主安装目录下的文件（包括 `model_config.toml`）；宿主配置值（如 `bot.nickname`）与数据表只通过官方 capability 只读获取。
- 运行报告写入 `data/plugins/github.jiakeju.maibot-curator/last_report*.json`；自证台账写入 `*_verified.json`；日程/黑话等状态文件也在这个目录。

## 成本

闲时成本很低：黑话每 6 小时一次（常规 8–15k token，候选少时更少）；日程每小时一次（1.5–2.6k token，有候选才多）；表达/表情是周/月级。相比节省（去碎片、避免重复学习），整体是净收益；所有模块可单独关闭。

## 版本记录

- `v0.1.4`（2026-10-29）：表达方式新增「人格适配严判」：对会被采用的表达，按宿主人格设定再判一轮，删掉攻击性强、阴阳怪气、适用面窄、与角色不符的条目；新增自动精选模式 `[expression] auto_check_mode`（off/report/on，默认 report 只出候选清单）、置信度门槛、二次复核、单轮勾选上限与 `auto_checked.json` 候选日志；表达判定结合群聊上下文（`context_lines`）；人格判定新增取向开关 `[expression] persona_mode`（conservative=保守学习，优先角色一致性；adapt=主动向群内交流风格靠拢，允许群内流行、干净、不伤人的说法），默认 conservative。，默认每 12 小时跑一次、单轮扫 300 条。黑话改为「先整理再判断」：收录前归一化去重、合并词频、剔除无意义条目，已完成的词条定期复核（`recheck_complete`/`recheck_days`），判定结合群内真实用法与上下文；默认每 4 小时跑一次。表情补描述时也会参考群聊上下文。manifest 简介精简为两句话，技术细节保留在 README。
完整修改日志（含复审反馈、自查项与代码清理细节）见 [CHANGELOG.md](CHANGELOG.md)。

- `v0.1.3`（2026-10-27）：按第 5 轮复审意见补强——`[agenda] max_archive_per_run` 真正生效（每轮最多写多少条人物卡归档，超出部分顺延到下一轮，0=不限制；dry-run 不写人物卡、不受此限），`[web] base_url` 字段描述注明仅建议填写可信的 API 地址；展示名英文副名由 MaiBot Curator 调整为 Memory Keeper（中文名与插件 id 不变）。README 开头新增一句自述；配置说明按 WebUI 面板顺序重写，逐项列出每个参数的含义与取值范围，所有配置字段描述都补上默认值/范围/示例，WebUI 未填写时会直接显示提示。另：配置 Schema 生成时会自动把字段 `description` 回填为 WebUI 的 `hint`，输入框下方直接显示灰色说明（此前只有 description，WebUI 不渲染）；清理旧版遗留的死代码（3 个未调用函数、2 个未使用常量，`plugin.py` 2,974→2,877 行），新增本仓库的 `CHANGELOG.md`。
- `v0.1.2`（2026-10-27）：按复审建议收尾——manifest 移除未使用的 `message.get_recent` 能力声明；修正 docstring 里“联网走宿主同一把 Key”的旧描述；修复开关「关闭再打开」后后台巡检不会自动重启的问题；单轮巡检异常不再终止整个循环。另外按自查清理：去掉文件头 BOM、移除 `urllib` 兜底（联网只用宿主自带的 `httpx`，缺失时优雅跳过并只警告一次）。第 3 轮复审补充：`[agenda] writeback = false`（dry-run）时不再写入人物卡（`archive_to_person` 保留为独立开关），使 dry-run 承诺与实现一致；同时校正 README 里联网依赖、宿主交互与配置表的描述。
- `v0.1.1`（2026-10-27）：按市场审核意见修复——仓库不再包含 `config.toml`（改由 Runner 生成、加入 `.gitignore`）；移除对宿主 `config/model_config.toml` 的读取，联网检索改为只使用插件配置里的 `[web] api_key`；消息读取改用 SDK 官方 `message.get_by_time_in_chat`（不再依赖私有参数），并用 `BotPlatformAccount` 过滤 Bot 自己的发言；`clear_high_frequency_terms` 注明整表清空。
- `v0.1.0`（2026-10-27）：首个公开版本。黑话/表达/表情三库清洗 + 群聊日程待办；多轮多源自证；全中文注释配置。

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
