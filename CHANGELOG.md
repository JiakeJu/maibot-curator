# 修改日志（CHANGELOG）

本文件记录「麦麦很好奇（Memory Keeper）」每次发布的完整改动；README 里的「版本记录」是摘要，这里保留细节（含市场复审反馈、自查项与代码清理），便于回看与升级对照。

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
