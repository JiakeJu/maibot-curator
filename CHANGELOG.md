# 修改日志（CHANGELOG）

本文件记录「麦麦很好奇（Memory Keeper）」每次发布的完整改动；README 里的「版本记录」是摘要，这里保留细节（含市场复审反馈、自查项与代码清理），便于回看与升级对照。

## v0.1.7（2026-11-02）

### 黑话「新词通道」
- 背景：宿主把黑话学习提示词放宽后，近两天新增 159 条新词，但其中 149 条 `count=1`，被 `min_count=2` 挡在候选之外，插件只处理反复出现的旧词。
- 新增 `[jargon] new_word_*` 一组配置：`new_word_enabled`（总开关）、`new_word_days`（只看近 N 天）、`new_word_min_age_hours`（满 24h 才判）、`new_word_min_evidence`（证据 ≥2 条）、`new_word_min_confidence`（门槛 0.8）、`new_word_max_per_run`（每轮上限 12，不占主候选名额）、`new_word_mark_unconfirmed`（证据不足时标注「（群内说法，待确认）」）。
- 判定收紧：新词必须 `keep=true`、有含义、置信度 ≥ 门槛才写回；不通过只跳过、不删除宿主条目。
- 实现：`_is_new_word_candidate()` / `_filter_new_word_decisions()`；`_run_jargon` 主候选与新词合并判定、分别限流；报告新增 `new_word_candidates` / `new_word_accepted` / `new_word_skipped`。

### 配置与代码清理
- 删除遗留的 `[expression] auto_check` 字段及其旧版自动勾选分支：自动采纳统一由 `auto_check_mode`（off/report/on）控制，避免两个开关语义冲突。
- `auto_check_mode`、`persona_mode` 等固定取值字段改为 `Literal` 类型，WebUI 配置页渲染为下拉选项而不是输入框，减少手填错误。

### 缺陷修复与性能优化（v0.1.7 内追加，审计驱动）

对插件做了一次全面缺陷审查（三路只读审查 + 逐条读码核实），修复了以下问题：

- 表情去重：只有「有描述」的表情参与 embedding 聚类，避免未描述表情因占位文本同向量而被批量取消注册；向量数量不一致时跳过去重并记录错误。
- 常见词过滤：`_COMMON_WORDS_EXCLUDED` 由拼接长字符串改为词表集合，修正「整串相等比较导致过滤完全失效」的问题。
- 人物卡记忆：`memory_points` 解析失败时直接放弃写入，不再以空列表覆盖宿主人物记忆。
- 写回额度：`max_writes_per_run=0` 现在真正表示不写回；黑话写回额度统一计算（含整理/结算），表达式合并、判定、自证三处共用同一额度；表情补充/取消注册也有独立上限。
- 只读观察：`dry_run` 时自证结果不再落盘为「已锁定」，否则之后开启写回会永远跳过这些条目。
- 自证推进：模型未返回某个词时也累计 attempts，`verify_max_attempts`/`give_up` 能正常收敛，避免个别词每轮重复抓取与联网。
- 联网预算：按「检索次数」而不是「词数」计，`verify_terms_per_run=0` 表示本轮不联网自证。
- 黑话整理：先取 keeper 含义再合并计数，删除成功后才累加 count；删除失败不再造成下轮重复累加与含义丢失。
- 嵌入调用：校验返回条数与维度，避免 `zip` 静默丢行。
- 日程：`dry_run` 在事件副本上演练，不再把「已归档但未写人物卡」的状态留在内存；`confirmed` 且缺少具体时间的事件按 `expire_hours` 结算，不再长期挂在【最近的安排】。
- 注入块：超长时优先压缩【最近的安排】，优先保住【群友近况】/【关心提示】。
- 布尔归一：模型返回的 `"false"` 字符串不再被当成 true。
- 可观测性：自动采纳被确定性过滤拦下的原因写入报告（`auto_skipped`），不再静默跳过。
- 性能：黑话证据消息、表达判定上下文在同一轮内复用查询结果，减少重复 RPC；循环异常按退避重启而不退出；`_write_json` 改为原子写。

### 学习节奏优化（v0.1.7 内追加）
- 修复复核时间判断：宿主 `updated_timestamp` 是日期字符串，旧的 `_as_float()` 会解析成 0，导致所有已完成词每轮都被当成「逾期复核」重复写回、吃满写回额度；改用 `_parse_ts_seconds()` 后按 `recheck_days` 正常跳过。
- `max_writes_per_run` 默认 40 → 120，加快积压词条的收尾速度。
- 新词通道改为**累计批量识别**：`new_word_batch_threshold`（默认 10）条起才批量送去判定，`new_word_batch_size`（默认 6）条一批、与普通候选分开；`new_word_max_per_run` 12 → 60。未达阈值时报告里显示「新词累计 N 个（未达批量阈值，先积攒）」。

### 实机验证修复
- 宿主 `created_timestamp` / `updated_timestamp` 是日期字符串（`YYYY-MM-DD HH:MM:SS.ffffff`），`evidence_messages` 是嵌套 JSON 字符串：新增 `_parse_ts_seconds()` 与 `_count_message_evidence()`，并按消息条数（而非数组层数）判断证据是否足够。
- 新词通道优先占用写回额度：主候选按词频先写会占满 `max_writes_per_run`，导致本轮判定通过的新词落不了库；`_jargon_apply` 改为先写新词、再写主候选。
- 实机结果：一轮候选 64、新词候选 6、收录 2（春竹 / npy），`jargons` 完成数 55→57，含义已加「（群内说法，待确认）」前缀；用时约 243s、0 报错。

### 默认值调优（来自实机日志）
- `batch_size` 6→3、`max_tokens` 4000→6000：消除 `max_output tokens` 截断重试。
- `max_candidates` 30→60、`recheck_days` 30→90：让 `count>=2` 的候选取满、少被复核挤占。
- `[web] verify_terms_per_run` 4→2：自证由 8 轮降为 4 轮，实测单轮 259s、0 报错。

## v0.1.6（2026-11-01）

### 表达自动采纳可见性
- 自动采纳时写回 `checked=true`，并默认同时把 `modified_by` 标记为 `USER`，使这些条目出现在 WebUI 资源的「仅精选」筛选里（宿主该筛选的条件是 checked 且来源为人工）。
- 真实来源与每条采纳记录仍完整保存在数据目录的 `auto_checked.json`（含 `adopted`/`ui_mark`/置信度/时间）；新增 `[expression] auto_check_ui_mark` 开关，`false` 时只勾选不改来源。

### 自证拆小批次，降低超时
- `_verify_targets` 多轮自证由「全部候选一次 LLM 请求」改为「每批最多 2 条分别请求」，单次提示体积显著变小，降低触发 120s 硬超时导致整批自证失败的概率；失败批次会单独跳过并重试。
- 适用于黑话/表达/表情三类自证；联网检索次数与总 token 基本不变。

### 自查
- 版本 0.1.6；静态 109 项、运行时 144 项、洁净检出 33 项、冗余 0 候选，全部通过。

## v0.1.5（2026-10-31）

### 联网检索：默认走宿主「联网服务」

- 默认使用宿主「联网服务」提供的 `web.search` / `web.fetch` 能力（与宿主统一联网工具共用同一套服务与 Key），插件不再要求自备 DeepSeek API Key。
- `[web] api_key` 改为可选——留空=走宿主联网服务（推荐，零配置）；填写=插件直连 DeepSeek `/v1/tools/search`（`base_url` 仅在直连时生效）。两种方式都不可用时自动跳过联网、只使用群内证据，并且只警告一次。
- manifest：新增 `web.search` / `web.fetch` 能力声明；配置字段说明与 README 同步更新。

### 群友近况 / 短时状态

- `[agenda]` 模块从「日程待办」扩展为「群友近况 / 日程」：抽取 prompt 同时识别 `kind=plan`（日程）与 `kind=state`（短时状态，如加班、感冒、考试周、出差、心情不好、刚搬完家）。
- 近况字段：`polarity`（positive/neutral/negative）、`topic`（关键词）、`content`、`last_mentioned_ts`；默认保留 `state_ttl_hours=48`，期间没人再提就标记 `expired` 淡忘，不进人物卡。
- 同一人的同类近况会合并更新（提及次数、最后提及时间、权重更高的 polarity、更完整的 content），证据保留最近 6 条。
- 新增配置：`state_enabled`、`state_ttl_hours`、`state_max_events`、`state_confirm_confidence`、`care_enabled`；报告新增 `state_new` / `state_update` / `state_expired` 计数。

### 注入改进

- 注入块新增【群友近况】（带「约几小时前提过」）与【关心提示】（负面近况时建议关心一句），并附一句「用你自己的口吻（昵称：人格摘要）来接」。
- `[inject]` 新增 `states`、`care`、`persona_style`、`persona_max_chars`；总字数上限默认 420→600。
- 日程/近况仍只注入 `confirmed` 且未过期的条目；dry-run 时只出报告。

### 自查

- 静态 106 项、运行时 134 项、洁净检出 33 项、冗余 0 候选；实例未部署（用户要求先在插件文件夹改动，等明确迭代再发）。

## v0.1.4（2026-10-29）

- 表达方式：新增人格适配严判（默认开启）：按宿主人格设定对「会被采用」的表达再判一轮，删除攻击性强、阴阳怪气、低俗擦边、适用面窄、与角色不符、纯碎片的条目；dry-run 只报告不删。
- 表达方式：新增自动精选模式 `auto_check_mode`（off/report/on，默认 report）、`auto_check_min_confidence`（默认 0.9）、`auto_check_double_check`（默认开）、`auto_check_max_per_run`（默认 10）、`auto_check_log`（记录 auto_checked.json）。
- 表达方式：新增人格判定取向开关 `persona_mode`（conservative=保守学习、优先角色一致性；adapt=主动向群内交流风格靠拢、允许群内流行但干净的说法；默认 conservative）。
- 表达方式：判定时结合群聊上下文（`context_lines`，默认 6 条）；默认间隔 12 小时、单轮 300 条、批量 10。
- 黑话：先整理再判断——归一化去重、合并词频、剔除无意义条目；已完成的词条定期复核（`recheck_complete`/`recheck_days`）；默认间隔 4 小时。
- 表情：补写描述时参考群聊上下文。
- manifest 简介精简为两句话，技术细节保留在 README。

## v0.1.3（2026-10-27）

### 配置说明与 WebUI 可读性

- 90 个配置字段的 `description` 全部改写为「含义 + 默认值 + 取值范围/示例」格式，例如 `tick_minutes`：「调度器多久检查一次到期模块（分钟，默认 30；范围 1–1440；例：30=每半小时检查一次）」。
- README「配置」章节重写：按插件 WebUI 面板顺序分 8 个小节（插件 / 调度 / 黑话学习 / 联网检索 / 表达方式 / 表情包 / 群友日程 / 上下文注入），共 90 行参数表，逐项列出含义与范围。
- 插件重写 `build_config_schema()`：生成 WebUI 配置 Schema 时，把字段 `description` 自动回填为 `hint`。MaiBot WebUI 只用 `hint` 渲染输入框下方的灰色说明（`description` 本身不渲染），这样 90 个字段不必维护两份文案。
- README 开头新增自述行：`> *“你们在聊什么呀，麦麦我很好奇！”*`。

### 市场复审补强（第 5 轮反馈）

- `[agenda] max_archive_per_run` 真正生效：每轮最多把多少条日程归档进宿主人物卡，超出部分顺延到下一轮（0=不限制；dry-run 不写人物卡、不受此限）。
- `[web] base_url` 字段描述注明「仅建议填写可信的 API 地址」。
- 展示名英文副名由 MaiBot Curator 调整为 Memory Keeper（中文名与插件 id 不变）。

### 代码清理（本次新增）

- 删除从未被调用的死代码：
  - `_parse_json_object()`（旧版解析单个 JSON 对象的辅助函数，已被 `_parse_json_array()` 取代）；
  - `_jargon_web_lookup()` 与 `_jargon_judge_with_web()`（早期「单轮联网复核」流程，已被 `_jargon_verify_rounds()` 多轮自证取代）。
- 删除未使用的模块级常量：`MAX_EVIDENCE_LINES`、`_NET_SLANG_RE`。
- `plugin.py` 由 2,974 行减到 2,877 行（-97 行），原有功能、审计项不变。
- 新增冗余自查脚本 `audit_redundancy.py`（开发期工具，不随插件发布）：检查未使用 import / 模块常量 / 函数 / 局部变量、不可达语句、注释代码、未读取配置字段、历史遗留关键词。

### 新增文件

- `CHANGELOG.md`：本文件，随插件仓库一起发布。

### 自查

- 静态审计 97 项、运行时仿真审计 91 项、洁净检出审计 33 项、冗余自查 8 类 0 候选，四套全部通过。
- 本地实例已部署 v0.1.3 并通过一致性校验：仓库与实例的 `plugin.py` / `README.md` / `CHANGELOG.md` / `assets/icon.png` 逐字节一致，`_manifest.json` 仅 id 按本机环境不同（`local.jargon-keeper`）；实例 `config.toml` 与 data 目录未被覆盖。
- 发布前版本仍为 v0.1.3，暂未推送。

## v0.1.2（2026-10-27）

- 按市场复审建议收尾：manifest 移除未使用的 `message.get_recent` 能力声明；修正 docstring 里「联网走宿主同一把 Key」的旧描述；修复「关闭再打开」后后台巡检不会自动重启的问题；单轮巡检异常不再终止整个循环。
- 自查清理：去掉 `plugin.py` 文件头 BOM；移除 `urllib` 兜底（联网只用宿主自带的 `httpx`，缺失时优雅跳过并只警告一次）；仓库不再包含 `config.toml`。
- 第 3 轮复审：`[agenda] writeback = false`（dry-run）时不再写入人物卡（`archive_to_person` 保留为独立开关），使 dry-run 承诺与实现一致；同时校正 README 里联网依赖、宿主交互与配置表的描述。
- 第 5 轮复审：`max_archive_per_run` 生效、`base_url` 描述补强、英文副名调整为 Memory Keeper（详细见 v0.1.3 条目）。

## v0.1.1（2026-10-27）

- 按市场审核意见修复：仓库不再包含 `config.toml`（改由 Runner 生成、加入 `.gitignore`）。
- 移除对宿主 `config/model_config.toml` 的读取，联网检索只使用插件配置里的 `[web] api_key`（留空=只用群内证据）。
- 消息读取改用 SDK 官方能力 `message.get_by_time_in_chat`（不再依赖私有参数），并用 `BotPlatformAccount` 过滤 Bot 自己的发言。
- `clear_high_frequency_terms` 字段说明注明「整表清空、不可恢复」。

## v0.1.0（2026-10-27）

- 首个公开版本。
- 黑话 / 表达方式 / 表情包三库定期巡检：补全、去重、清洗，结合群内真实用法 + 联网检索多轮自证后写回。
- 群友日程待办：抽取、确认、到期归档宿主人物卡（受 `writeback` / `max_archive_per_run` 控制）。
- planner / replyer 请求前注入相关日程背景（可通过 `[inject]` 关闭）。
- 全中文注释配置，所有写回均支持 dry-run。
