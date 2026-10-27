# 麦麦很好奇（MaiBot Curator）

让麦麦自己整理自己的「记忆库」：黑话、表达方式、表情包三套内置数据，加上一份群友日程待办。

MaiBot 内置的学习功能会不断往库里写东西，但写进来的条目往往缺少解释、彼此重复，或者只是一句话碎片。这个插件在后台定期巡检这些库，结合**群内真实用法 + 联网检索多轮自证**，把「不敢确定」的东西留在待办池里观察，确认后才写回、注入上下文或归档。所有写回都可以先 dry-run。

## 环境要求

- MaiBot `>= 1.3.0`（开发/测试环境：MaiBot 1.3.15、SDK 2.16.0）
- Plugin SDK `>= 2.8.0`
- 无额外 Python 依赖（联网走插件自身 HTTP，LLM/embedding 走宿主能力）

## 功能

| 模块 | 做什么 | 默认频率 |
|---|---|---|
| 黑话（jargon） | 从上下文里取证据 → 首轮判定 → 群内用法 + 联网 2 轮多源自证 → 高置信才写 `meaning`；碎片/常见词删除 | 每 6 小时 |
| 表达（expression） | embedding 聚类合并近似条目，LLM 判定质量/改写场景，删除无意义表达（默认不自动勾选） | 每周 |
| 表情（emoji） | 为表情生成「画面描述｜标签｜适用场景」，近似重复保留常用的、其余取消注册 | 每月 |
| 日程（agenda） | 抽取群聊里的短期安排，`pending → confirmed → done/cancelled`，确认后才允许注入上下文；结束归档到人物卡并定时清理 | 每小时 |

多轮多源自证：每条候选同时看「群内用法（真实消息证据）」和「联网检索（多种问法）」，两边一致才锁定；联网检索通过 DeepSeek 的 `/v1/tools/search` 完成，也可只用群内证据。

日程不会瞎猜：一会儿/待会/之后这类抽象时间进入观察窗（默认 24h），由后续聊天证据决定是否确认；明确的日期/时刻才直接确认。只有 `confirmed` 的事件会出现在上下文里，避免把「没定的事」说成事实。

## 安装

- 插件市场：搜索「麦麦很好奇」安装，安装后到「管理插件」启用
- 或从 Git 安装：`https://github.com/JiaKeJu/maibot-curator`
- 或下载 Release 里的 ZIP，用插件扩展页「从 ZIP 安装插件」上传

## 配置

配置由 Runner 在首次加载时按 `config_model` 自动生成到插件目录 `config.toml`（全部字段带中文注释；**仓库不包含该文件**，升级不会覆盖/冲突）。几个常用开关：

| 配置项 | 说明 | 默认 |
|---|---|---|
| `[plugin] enabled` | 总开关 | `true` |
| `[jargon] writeback` | 是否写回黑话库；`false` 时只出报告 | `true` |
| `[agenda] writeback` | 是否写回日程；`false` 时 dry-run | `true` |
| `[agenda] confirm_min_hits` | 同一件事被提到几次才算确认 | `2` |
| `[agenda] abstract_window_hours` | 「一会儿/之后」类安排的观察窗 | `24` |
| `[inject] max_chars` | 注入上下文的最大字数 | `420` |
| `[web] api_key` | 联网检索用的 DeepSeek API Key（[platform.deepseek.com](https://platform.deepseek.com) 自行申请）；留空则**只使用群内证据**，不联网 | `""` |

**注意**：在 WebUI 插件配置页点「保存」会按 schema 重写 `config.toml`，可能丢掉注释。想保留注释请直接编辑插件目录里的 `config.toml`（插件会自动热加载）；每次保存前 Runner 都会在插件目录的 `config_back/` 留备份，可从中找回。

## 工作原理

- 通过 `maisaka.planner.before_request` / `maisaka.replyer.before_model_request` Hook，把【最近的安排】按需注入上下文（只注入已确认、且未过期的事件）。
- 数据库读写、LLM、embedding、消息检索全部走宿主官方 capability（`database.query`、`llm.generate`、`llm.embed`、`message.get_by_time_in_chat`、`chat.get_group_streams`、`config.get`、`person.get_id` 等），不改宿主源码、不读取宿主安装目录下的任何配置文件（包括 `model_config.toml`）。
- 运行报告写入 `data/plugins/github.jiakeju.maibot-curator/last_report*.json`；自证台账写入 `*_verified.json`；日程/黑话等状态文件也在这个目录。

## 成本

闲时成本很低：黑话每 6 小时一次（常规 8–15k token，候选少时更少）；日程每小时一次（1.5–2.6k token，有候选才多）；表达/表情是周/月级。相比节省（去碎片、避免重复学习），整体是净收益；所有模块可单独关闭。

## 版本记录

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
- **数据风险**：插件会按配置**修改或删除** `jargons` / `expressions` / `images` 表中的条目，并向 `PersonInfo.memory_points` 写入日程归档。使用前请备份 `data/MaiBot.db`，首次使用建议 `writeback = false` 以 dry-run 模式观察报告。
- **费用**：插件通过宿主 LLM / embedding 能力产生 token 消耗，相关费用由使用者自行承担。

### 使用风险

- LLM 判断存在误差：插件通过多轮证据与置信度门槛降低误判，但无法完全避免；被删除的条目在一轮内不可恢复。
- 插件依赖 MaiBot 宿主能力与内部数据结构（`database.query` 等），宿主升级后若接口变化可能需要适配。
- 本插件按 **MIT 协议「按原样」（AS IS）提供**，作者与所使用的 AI 工具均不对因使用本插件造成的任何直接或间接损失负责。
