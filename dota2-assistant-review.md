# dota2-assistant 现状评审报告

> **⚠ 本文是评审时的快照（时间点报告），不是当前状态的描述。**
> 文中 §4 列出的 8 项缺陷（D1~D8）**均已在后续提交中修复**，
> §2 关于「读屏/G.SI 自动感知 BP」的结论至今仍然有效（并已据此明确不做该功能）。
> 想知道当前状态与完整时间线，请看 [docs/开发进度.md](docs/开发进度.md)。

> 评审对象：`dota2-assistant/`（已实现提交 `23ff100`、`2f5461c`）
> 评审基准：《Dota 2 助手类软件生态调研报告》
> （外部调研材料，**未随本仓库发布**）§3.1 GSI、§3.3 红线、§6 参考架构
> 评审方式：全部结论来自本次实际跑测与源码通读；外部结论逐条附 URL 与证据强度
> 评审日期：2026-09-30
> 范围声明：**本次只评审，未修改任何代码**

---

## 0. 结论摘要

**一句话**：这个实现的**架构与合规框架是对的、工程完成度高、可运行**；但它存在**一个结构性缺口**（无法自动感知 BP）和**八个已实测确认的缺陷（D1–D8）**。你提的四项诉求里，**③④ 中有一项（推荐原因）已经实现且超预期**，①（分位置熟练度）②（STRATZ 切片）③（复盘闭环）是真正的价值增量；而你补充的「读屏幕头像判断 BP」在技术上此路不通、合规上有明确风险，**建议不做进本项目**（见 §2）。

**最需要立刻修的两条**：D1（对位矩阵被截断，让推荐系统性偏向热门英雄）与 D2（第一手推荐不含位置信息）——它们直接损害「推荐我擅长且**好发挥**的英雄」这个核心目标。

| 维度 | 评价 | 依据 |
|---|---|---|
| 可运行性 | ✅ 好 | 37 项单测通过；`tools/selftest.py` 全绿 |
| 合规框架 | ✅ 好，且**必须保住** | 无注入/读内存/OCR/模拟输入，只读公开 HTTP |
| 分层与依赖方向 | ✅ 好 | UI → Assistant → Engine → HeroBook 单向无环 |
| 可解释性（诉求④） | ✅ **已实现**（超预期） | `Candidate.reasons/risks/breakdown` 三层 UI 全量展示 |
| 领域模型覆盖（诉求①） | ❌ 有缺口 | 熟练度**不含位置维度** |
| 数据层 | ⚠️ 有系统性偏差 | 对位矩阵被 `top_k=14` 截断、4 个英雄零对位、协同仅 35 英雄 |
| 算法正确性 | ⚠️ 8 处已确认缺陷 | 见 §4，其中 D1/D2/D8 影响实际排序 |
| 对外数据源（诉求②） | ⚠️ 单一 | 只有 OpenDota，未用 STRATZ |
| 复盘闭环（诉求③） | ❌ 未实现 | 英雄池只能手工填/手工刷新 |
| 自动感知 BP | ❌ 未实现，且**技术上此路不通** | 见 §2，这是本次最重要的结论 |

---

## 1. 与报告的对照：框架遵守情况

### 1.1 生态分层定位

报告把 Dota 助手分成 5 层（L1 数据门户 / L2 对局内 overlay / L3 开发者基础设施 / L4 AI 助手 / L5 社区分发）。

本项目定位为 **L2 的合规子集 + L1 的轻量本地化**：

- 借用了 L2 的形态（置顶悬浮窗），但**主动放弃了 L2 的核心能力**（自动读取对局状态）；
- 数据来源按报告 §6「最小可行版本」第 1 条执行：直接用现成 API，不自建解析；
- 呈现方式按 §6 第 3 条执行：Windows 无边框置顶窗口。

**评价**：这个定位是自洽的。它实际上是一个「**BP 阶段决策计算器**」而不是「对局内助手」，README 也诚实地承认了这一点。

### 1.2 报告 §6 四条建议的落实

| 报告建议 | 落实情况 | 备注 |
|---|---|---|
| ① 历史数据用现成 API，别自建解析 | ✅ 完全遵守 | OpenDota REST + 磁盘缓存 + 限流 |
| ② 实时数据用 GSI + `gamestate_integration_*.cfg` | ❌ **未做** | 这是最常被误认为「缺口」的地方，但见 §2——在 Ranked All Pick 里 GSI 给不了 draft，**所以不做是对的** |
| ③ 呈现用无边框置顶 overlay | ✅ 已实现 | tkinter + Win32 置顶/鼠标穿透/全局热键 |
| ④ 智能用现成 MCP，LLM 只做解释 | ⚪ 未做，但不必要 | 当前是确定性规则引擎，无幻觉风险；引入 LLM 属可选增强 |

**一个重要观察**：报告 §6 第 2 条建议「实时数据用 GSI」，但报告 §3.1 同时也写了 GSI 的 draft 字段存在。**本次调研证明后者在 Ranked All Pick 下不成立**——也就是说报告 §6 的这条建议对「BP 助手」这个具体场景是有误导性的（报告 §3.1 只是罗列了 `draft` 开关，没有验证它在天梯模式下会不会真的填充）。本项目「不做 GSI」的选择，事后看反而是正确的。

### 1.3 报告 §3.3 红线的遵守情况（逐条核验源码）

| 红线 | 是否违反 | 源码证据 |
|---|---|---|
| 不读内存 | ✅ 未违反 | 无任何 `ReadProcessMemory` / `OpenProcess`；`overlay.py` 仅用 `GetWindowLongW`/`RegisterHotKey` 等窗口 API |
| 不注入 DLL / 不 hook | ✅ 未违反 | 无 `SetWindowsHookEx`、无 DLL 注入 |
| 不修改游戏文件 | ✅ 未违反 | 只写 `config.json` 与 `data/` |
| 不自动化输入 | ✅ 未违反 | 无 `SendInput`/`keybd_event`/`mouse_event`（只注册全局热键**接收**自己的按键） |
| 不做 OCR / 图像识别 | ✅ 未违反 | 无任何截图 API（无 `BitBlt`/`PrintWindow`/Desktop Duplication） |
| 尊重上游限流 | ✅ 基本遵守 | `_throttle` 默认 1.1s；`refresh_data.py` 对位 min_interval=1.2s；实测 127 英雄约 2.5 分钟 |
| 数据署名 | ⚠️ 部分 | 数据文件内记 `source` 字段，但 UI 未署名 OpenDota |

**这一节是本项目最值得保留的资产**：合规不是「少做了功能」，而是它的**核心设计约束**，且约束被贯彻到了代码层。

---

## 2. 你提出的「屏幕顶栏头像判断 BP」——必须单独说清楚

这是本次评审**最重要的结论**，因为它决定了一个功能该不该做。

### 2.1 技术上：不存在合规的自动 BP 通路

| 路径 | 可行性 | 证据 |
|---|---|---|
| **GSI 的 `draft` 字段** | ❌ **天梯不可用** | Valve 官方 issue [#9562](https://github.com/ValveSoftware/Dota2-Gameplay/issues/9562)（2023-06 提问「ranked game 能不能拿到 draft」）回答：「**Now I see only draft data in cm mode**」，社区回复「used to can be, but now valve disable it」；issue [#19408](https://github.com/ValveSoftware/Dota2-Gameplay/issues/19408)「I ask again: When will GSI show draft information for ranked games?」**至今 open**（2024-06 创建，2026-08 被 bot 标 stale），另一个开发者 2025-07 回复「I checked OpenDota API and Stratz, also visited dota logs file and GSI. **Found no solution**」 |
| **Overwolf GEP 事件** | ❌ **无 draft/pick 事件** | 官方事件清单（[dev.overwolf.com Dota 2](https://dev.overwolf.com/ow-native/live-game-data-gep/supported-games/dota-2/)）共 37 项：`kill`/`cs`/`gold`/`ability_used`/`roster`/`hero_pool`… **没有任何 draft / pick / ban / hero_selection 事件**；`match_detected`（唯一含 10 人阵容的事件）被明确标注「**This feature is not available at the moment**」 |
| **Overwolf 读内存** | ⚠️ 仅他们自己能做 | issue [#15007](https://github.com/ValveSoftware/Dota2-Gameplay/issues/15007) 社区回复：「Overwolf are doing it because they have **legal deal/agreement with valve**. They are indeed reading signatures/offsets from dll files and then memory-read the data.」——**这是白名单特权，不可复现** |
| **读日志文件** | ❌ | 同上 #19408：开发者试过 dota 日志文件，无解 |
| **OCR / 读屏幕像素** | ⚠️ 技术上可行，合规上未获批准 | 见 §2.2 |

### 2.2 合规上：Valve 的措辞很宽，且曾专门堵过 BP 阶段的读取

**Valve 2023-02 官方声明原文**（经 [Silicon Republic 转引](https://www.siliconrepublic.com/business/valve-dota-2-bans-cheaters-trap)，已抓取验证）：

> "If you are running any application that reads data from the Dota client as you're playing games, your account can be permanently banned from playing Dota. This includes professional players, who will be banned from all Valve competitive events."

三点关键解读：

1. **"reads data from the Dota client"** 是对象是「客户端数据」，不是「进程内存」。屏幕像素是客户端渲染输出的数据——**按字面属于这句话的覆盖范围**。
2. **Valve 的执法手法是蜜罐**：官方在同一次行动中往客户端里塞了一段「正常游戏不会读到的『秘密』数据」，谁读了就封谁。这说明他们愿意为「读客户端数据」这件事主动设陷阱，而不是只被动检测作弊。
3. **Valve 曾专门针对 BP 阶段下手**：2023-02-27 补丁的原文之一是「**It is no longer possible to access player profiles during the preparation phase in matchmaking games. They can still be accessed after the selection phase is over.**」（[Fragster 转引补丁说明](https://www.fragster.com/valve-puts-an-end-to-dota-2-overwolf-addon/)）。同一补丁还禁用了 `record` 命令与一批可读取内部客户端状态的 console 命令，理由是「information that was not intended to be visible to the client during a match」。

   换句话说：**Valve 已经明确表示过，他们不希望第三方在选人阶段拿到信息**，并且为此动过手。

**反向证据（对你有利的部分，必须一并给出）**：

- 直接问「用 OCR 读英雄 pick 算不算违规」的 issue [#15007](https://github.com/ValveSoftware/Dota2-Gameplay/issues/15007)（提问者自述「At the moment I'm using OCR for it and it's buggy/slow and not good」）**Valve 从未回复**，挂了 19 个月后被 bot 以 stale 关闭。**沉默不等于批准，但也不等于禁止。**
- 2024 年那条「Overwolf 有法律协议」是**社区二手说法**，未经 Valve 或 Overwolf 官方确认，**证据强度：推断**。
- 报告 §3.3 也已指出「OCR 屏幕识别不读内存，Valve 未点名」，但 STRATZ+ 仍在 2023-02 主动停运。

### 2.3 结论与建议

**风险等级：中高，不可忽视。**

- **不建议**把它做进现在的 `dota2-assistant`——这个项目目前最大的资产就是「零争议」，一旦引入读屏，整个项目的合规叙事就崩了，而且会把一个「计算器」变成一个「Valve 明确表达过不喜欢的选人期信息工具」。
- **如果一定要探索**，唯一可接受的做法是：**独立仓库、独立进程、默认关闭、用户每次手动触发截图、不做轮询/不自动识别、不做任何自动化操作、README 首行写明风险自担**。把它当成一个「可选的实验性输入辅助」，而不是产品主路径。
- **更值得投入的替代方案**（规避风险且体验不差）：
  1. **极速手工录入**：现有 overlay 已经支持中文名/缩写/模糊匹配 + 回车录入，实测「斧王」「am」「sf」都能一次命中。把它做到极致（首字母补全下拉、方向键选择、Esc 撤销）比读屏更稳。
  2. **拼音/首字母加速**：给 `HeroBook._build_index` 增加拼音首字母索引（如 `fw`→Axe 斧王），一次录入可压到 2~3 个字符。
  3. **语音或第二屏**：手机/平板打开 `--web` 面板，用键盘或点击录入，主屏不受影响。

> 关于「读屏识别准确率」还有一条硬伤：[#15007](https://github.com/ValveSoftware/Dota2-Gameplay/issues/15007) 的提问者自己用 OCR 做这件事，评价是「buggy/slow and not good」。也就是说，即使不看合规，这条路的产品体验也不好。**不建议把它当作「自动同步」的主方案**；如果要推进，§6 给了分三个阶段、风险递增的取舍路径。

---

## 3. 你的四项诉求：现状核对

### 诉求① 熟练度要分位置算 —— ❌ 未实现，且是**当前最大的模型缺口**

**现状**：`PlayerHeroStat` 只有 `hero/games/wins/last_played`，**没有位置字段**（`data_loader.py:274-286`）。

**影响**：`RecommendationEngine.proficiency()`（`engine.py:194-212`）返回的是「你玩这个英雄的整体胜率」，与位置无关。于是：

- 你打 1 号位的 Juggernaut 胜率 70%、打 4 号位胜率 40%，模型看到的是「58%」；
- 反过来，当 `my_lane=4` 时，它不会因为你「4 号位玩得烂」而降权。

**为什么难**：这不只是加字段。分位置熟练度需要**按位置切分的对局统计**，而：

- OpenDota 的 `players/{id}/heroes` **不返回位置信息**（只有 hero_id/games/win/last_played，且已按 `win` 聚合）；
- 要拿到位置，需要 `players/{id}/matches`（或 `matches/{id}`）逐场取 `lane_role`，再自己聚合 —— OpOpenDota 的 `lane_role` 字段是有的，但这意味着**一次导入要拉几十上百场对局详情**，配额消耗会明显上升（免费 3000 次/日，60 次/分钟）；
- 更麻烦的是 Dota 的 `lane_role` 与「1~5 号位」不是一一对应（`lane_role` 是 1=Safe Lane/2=Mid/3=Off/4=Jungle，代表**路**而不是**位**），要映射到 1-5 号位需要额外启发式。

**建议的落法**（改动可控）：
```
PlayerHeroStat 增加 by_lane: Dict[int, Tuple[int,int]]   # {位置: (局数, 胜场)}
proficiency(hero, lane) 优先用 by_lane[lane]，缺失则回退到总体值并降低置信度
导入时：拉 players/{id}/matches?limit=... → 按 lane_role 聚合 → 映射到 1-5
```
这是一个**纯增量**改动：现有总体值作为 fallback，向后兼容，不会让已有配置失效。

---

### 诉求② 接入 STRATZ 高分局/职业切片 —— ⚪ 未实现，**改动成本最低、收益明确**

**现状**：唯一数据源是 OpenDota（`meta.json.source = "opendota/heroStats"`、`matchups_live.json.source = "opendota/heroes/{id}/matchups"`）。`meta.json.patch = "current"` —— **没有真实版本号**。

**报告依据**：§2.4 指出 Dota2ProTracker「人群切片 = 领先指标」，全体平均胜率会掩盖版本答案；§2.3 给出 STRATZ 配额（默认 token 10,000/天，GraphQL 一次请求可取多类数据）。

**这个接入的性价比很高**，原因：

1. `HeroBook.meta` 已经是 `{hero: {winrate, pickrate, ...}}` 的字典结构，**换数据源不动接口**：`meta_score()`、`has_meta()`、`winrate()` 全都不用改。
2. STRATZ 是 GraphQL，**一次请求能同时拿到多个段位切片**（报告 §2.3 的 `bracketIds=IMMORTAL`），比 OpenDota 逐英雄请求省得多。
3. 立刻能修掉 `patch = "current"` 这个「不知道自己是不是过期」的问题。

**要提前想清楚的**：
- STRATZ 的 heroStats 是否支持按段位过滤，需要实测（报告只确认了 `/heroes/meta/trends` 这类页面接口存在）；
- 需要 token 管理（`stratz.com/api` Steam 登录免费领），以及**缓存策略**——别每次启动都打；
- 用它替换还是**并列**？建议**并列**：新增 `data/meta_stratz.json`，在 `HeroBook` 里加一个 `meta_tier` 概念，让引擎能选「全体平均」还是「高分切片」，为 UI 留一个开关。

---

### 诉求③ 赛后复盘闭环，自动校准英雄池 —— ❌ 未实现

**现状**：英雄池的写入只有三条路——`config.json` 手工填、CLI `pool add`（`cli.py:313 _persist_pool`）、Web `POST /api/pool`。**没有任何「打完一局自动回写」的机制。**

**已有的良好基础**：`proficiency()` 里的贝叶斯收缩（`engine.py:205`）本身就是「用真实战绩自我校准」的数学形式——只要往英雄池里持续写入，收缩值会自动收敛。**缺的只是数据回灌的管道。**

**建议的落法**：
```
新增 tools/sync_results.py：
  1. 记住上次同步的 match_id 水位（存 config.json 或 data/sync_state.json）
  2. 拉 players/{id}/matches?limit=... 取新对局
  3. 用 slot/account_id 判定「这局我玩了什么英雄、是否获胜、什么位置」
  4. 增量写入 pool（games+1 / wins+1 / by_lane[lane]+1）
  5. 打印本次变化，报告「熟练度因子从 X 变成 Y」
```
**注意**：这一步依赖账号开启「Expose Public Match Data」（报告 §2.1 提到 Dotabuff 也有同样依赖），否则拉不到数据。要在 README 里写清这个前置条件。

---

### 诉求④ 给出推荐时同步给出推荐原因 —— ✅ **已经实现了，而且是超预期的**

这一点要被明确表扬，因为它已经是三层 UI 的标准能力：

| 层 | 实现 | 位置 |
|---|---|---|
| 命令行 | 逐条打印 `reasons` 与 `risks` | `cli.py:83-85` |
| 悬浮窗 | 默认显示前 2 条理由；「详情」按钮展开完整 breakdown + 4 条理由 + 3 条风险 | `overlay.py:431-444` |
| Web 面板 | 完整渲染 `reasons` / `risks` / 五因子条形图，并可一键复制完整报告 | `webui/static/index.html:274-299` |

理由文本已经是**带数值的自然语言**，例如实测输出：
```
你擅长：你打了 60 局，胜率 62%
克制: Juggernaut(+4%)
阵容：补先手（全队此项缺口 60%）
版本胜率 50.6%
! 全队缺少先手，团战开不起来 (-10%)
```

**仍可改进的三处**（属打磨，不属缺陷）：

1. **不显示「每个理由对分数贡献了多少」**。现在能看因子分（`熟练0.76 对位0.50 …`）和理由文字，但看不出「这条理由值几分」。建议直接算 `w.proficiency * prof * 100` 这类**加权贡献**并标注，这会让「为什么它排第一」变成小学算术。
2. **理由没有按影响力排序**。现在顺序是「熟练 → 对位 → 阵容 → 配合 → 版本」，是**代码书写顺序**而不是影响大小顺序。建议按加权贡献降序排。
3. **空 BP 时理由过少**（实测只有 2 条），此时应补充「当前没有敌方信息，本推荐只反映你的熟练度与版本强度」这类**元说明**，避免用户误以为已经考虑了对位。

---

## 4. 已实测确认的缺陷清单

以下每一条都**实际跑过**，附复现方式。按影响排序。

### D1 —— 对位矩阵被 `top_k=14` 截断，产生**方向性偏差**（影响面最大）

**位置**：`tools/refresh_data.py:67-97`

```python
if row:
    best = sorted(row.items(), key=lambda kv: -abs(kv[1]))[:top_k]   # top_k 默认 14
    pairs[name] = dict(sorted(best))
```

**实测**：`live per-hero matchup count: min 1, median 14, max 14` —— **每个英雄最多只留 14 条对位**，按「绝对值最大」筛选。

**为什么是缺陷**：这不是随机抽样，而是**按显著性截断**。后果是：

- 一个英雄如果对某个**冷门对手**有极强克制（但该对手整体样本少 → 该条对位的 `games_played` 小 → 可能先被 `min_sample` 滤掉），这条克制关系就永远不会出现在推荐理由里；
- 推荐时说「克制: X(+5%)」的**召回率取决于对手有多热门**，热门对手(样本大、差值稳定)更容易进 top-14。这会让推荐**偏向热门英雄**，恰好与报告 §2.4「高分局切片」想解决的偏差同向叠加。

**影响评估**：中高。它不会让推荐「错」，但会让「好发挥」的判断**系统性偏向热门英雄**，而这个项目主打的就是「好发挥」。

**建议**：不要只留 top-14。改为「**保留全部满足 `min_sample` 的边**」（每条边只有两个 float，127×127 全量也就几万条，JSON 体积完全可接受），把「显著性筛选」留给引擎的 `min_sample` 过滤，而不是数据准备阶段。

---

### D2 —— 位置适配因子在**空 BP（第一手）时完全失效**

**位置**：`engine.py:266-269`（`_need_score` 的早退）+ `engine.py:326-330`（`team_fit` 的加权）

```python
def _need_score(self, hero, draft):
    mine = [...]
    needs = self.team_needs(draft)
    if not mine:
        return 0.6, []        # ← 空 BP 直接返回常数
```

**实测**：
```
Axe(合法位置只有 3)：
  lane=1 score=57.85  fit=0.420
  lane=5 score=57.85  fit=0.420     ← 5 号位非法，分数与 1 号位完全相同
  lane=3 score=...    fit=0.760
next_lane() on empty draft = 1
```

**后果**：**选人阶段第一手**（也就是最需要辅助决策的时刻）的推荐**不含任何位置信息**。模型会把 Axe 排在第一，即使你标了要打 5 号位。第一手之后 `mine` 非空，该因子恢复正常（实测 Axe 在 lane=3 时 fit 升到 0.76，排序随之改变）。

**建议**：`_need_score` 在 `mine` 为空时不要返回常数 0.6，而应返回一个**中性值**，让 `_position_fit` 主导；或更彻底地把 `team_fit` 改成
```python
if not mine:
    return pos_s, []      # 无队友信息时，位置适配就是唯一可用的阵容信号
```

---

### D3 —— 4 个英雄**零对位数据**，对位因子永久中性

**实测**：
```
完全没有对位数据的英雄: 4  ['Elder Titan', 'Lone Druid', 'Phantom Assassin', 'Visage']
heroes with NEITHER live NOR seed: 4
```

其中 **Phantom Assassin 是热门 carry**。这 4 个英雄在所有对局里 `matchup()` 都返回 `0.0` → `matchup 因子 = 0.5`（中性），也就是**对位维度上它们永远不加分也不减分**——推荐它们时你看不到「被谁克」，推荐别人时也看不到「能不能打过它」。

**根因**：`refresh_data.py:87` 的 `g < min_sample`（默认 300，实测数据里写的是 120）把这些英雄的**全部**对位边滤掉了；同时 `matchups.json` 的人工种子也没有覆盖它们。

**建议**：给这 4 个英雄补人工种子，或对它们单独放宽 `min_sample`。

---

### D4 —— 协同（synergy）因子几乎是**常数噪声**

**实测**：
```
synergy heroes: 35 | synergy edges: 81
```
127 个英雄里只有 35 个有任何协同边，共 81 条。`synergy()` 在无数据时返回 `0.5`。

**影响**：该因子权重 0.10，但**对绝大多数英雄恒为 0.5**，等于把 10% 的权重**固定贡献给每个候选**。它不会改变排序（因为大家一样），但会**压缩有效区分度**——5 个因子里有 1 个是常数，实际区分只能靠剩下 4 个。

**同时**：`reasons` 里会输出「和 X 有组合」，但只有 35 个英雄可能触发；其余英雄即使与队友有真实配合也**不会出现在理由里**——这与诉求④「给出推荐原因」的完整度直接相关。

**建议**：优先级中等。要么补数据（OpenDota 没有现成的 synergy 接口，需要自己算），要么**把该因子的权重临时并入其他因子**，并在 UI 上标注「协同数据覆盖不足，本局未计入」。

---

### D5 —— `min_games` 参数**实际上无效**；`pool_only=False` 时「没玩过」惩罚被放大

**位置**：`engine.py:475-492`

```python
def recommend(self, draft, top_n=8, lane=None, pool_only=True, min_games=0):
    if pool_only:
        names = [h for h, s in self.pool.items() if s.games >= max(1, min_games) and h in self.book.heroes]
    else:
        names = self.book.names()          # ← min_games 在这里完全没用到
    return self.rank(draft, names, lane=lane, require_pool=pool_only)[:top_n]
```

**实测**（`pool_only=False, min_games=50`）：全英雄都被送进 `rank()`，`min_games` 不产生任何过滤。这个参数在「全英雄视角」下是**死参数**。

**附带发现**：`penalties()` 里
```python
if games == 0 and draft.allies:
    out.append(("你没玩过这个英雄，纯靠版本/克制硬选", 0.12))
```
这个 0.12 的惩罚**只在 `draft.allies` 非空时生效**。而默认路径 `pool_only=True` 又已经把 `games=0` 的英雄全过滤掉了。结果是：这条为「全英雄视角」设计的惩罚，在 `allies` 为空时**完全失效**——也就是「第一手推荐一个你从没玩过的英雄」不会被扣分。

**建议**：`min_games` 应作用于 `rank()` 内部或两条分支都生效；「没玩过」的惩罚不应依赖 `draft.allies`。

---

### D6 —— 「版本强度」没有真实版本号，**无法判断数据是否过期**

**实测**：
```
meta.json:  source='opendota/heroStats', patch='current', fetched_at=2026-09-30
```

`patch` 字段恒为字符串 `"current"`（`refresh_data.py` 未写入真实版本）。README §7 第 6 条建议「每个大版本更新后跑一次 `refresh_data.py --meta`」，但**用户没有任何办法知道当前数据是不是新版本的**——只能靠 `fetched_at` 时间戳猜。

**建议**：从 Steam/Dota 公开接口取真实 patch 号（或用 OpenDota 的 `metadata`/`patches`），写入 `meta.json.patch`，并在 UI 上显示「数据基于 7.39c」。

---

### D7（次要）—— 对手位置推断是硬编码顺序，且 `_lane_deficit` 的兜底逻辑不可达

**位置**：`draft.py:125-152`、`engine.py:315-324`

- `guess_enemy_lanes` 用固定顺序 `[1,2,3,5,4]`：假定敌方第一个拿核心、最后两个拿辅助。对热门摇摆位（如 Juggernaut 可 1/4、Mirana 可 2/4/5）会误判，而对位因子对「同路敌人」给 1.0 权重（其余 0.2），所以**一次位置误判会让对位因子偏差很大**。
- `_lane_deficit` 只在结果 `>=1.0` 时被使用，而它只返回 `0.0` 或 `1.0`，写得像连续量但实际是布尔的。建议改为按「有几个可行分路被占」返回 0~1 连续值。

**影响**：低-中。UI 允许手动改位置（是个合理的降级方案），但默认路径下这个启发式会持续引入噪声。

---

### D8 —— 推荐列表**不按位置过滤**，非法位置英雄会占据榜首

**实测**（空 BP，`my_lane=5`，以及 `lane` 参数显式设为 5）：
```
lane=None:                          lane=5:
  Axe            57.8 能打5=False      Axe            57.8 能打5=False
  Magnus         57.2 能打5=False      Magnus         57.2 能打5=False
  Crystal Maiden 46.8 能打5=True       Crystal Maiden 49.5 能打5=True
```
（`Axe.lanes=(3,)`、`Magnus.lanes=(3,4)`、`Crystal Maiden.lanes=(5,)`）

**两个问题叠加**：

1. **不过滤**：`rank()` 把 `lane` 只用来算 `_position_fit`，**不排除**「打不了这个位置」的英雄。你标了 5 号位，推荐前三里有两个不能打 5。
2. **稀释导致区分度不足**：`team_fit = 0.6*need + 0.4*pos`（`engine.py:329`）。非法位置时 `_position_fit` 返回 0.15，但被加权后变成 `0.6*0.5 + 0.4*0.15 = 0.36`；而**合法**的 CM 在 5 号位拿到 0.573。差距只有 0.21 的 fit 差，折算到总分权重 0.22 后约 **4.1 分**——**不足以逆转熟练度带来的差距**（Axe 比 CM 高 8.3 分）。空 BP 时更糟：D2 让所有候选的 `need` 都等于常数 0.6，非法位置的 fit 变成 `0.6*0.6 + 0.4*0.15 = 0.42`，与**合法位置但无缺口可补**的情况同分（这正是实测里 `Axe lane=1`、`Axe lane=5`、`CM lane=1` 三者 fit 都是 0.420 的原因）。

**注意 `pos=` 字段具有误导性**：`Candidate.position` 只是把传入/推断的 `lane` 回填显示，它**不代表**「引擎认为这个英雄该打这个位置」。实测 `lane=None` 时 Axe 显示 `pos=1` 但 `Axe.lanes=(3,)` —— **UI 会显示「1号位 · Axe」，而 Axe 根本不能打 1 号位**。这是一个会直接误导用户的展示缺陷。

**建议**：
1. `rank()` 增加 `strict_lane` 选项（默认开），过滤掉 `not hero.can_lane(lane)` 的候选；至少要在 `_position_fit` 返回 0.15 时施加**显式惩罚**（进 `penalties`，可见、可解释），而不是软性拉低。
2. 修 `Candidate.position` 的语义：只在该英雄确实能打这个位置时才显示，否则显示「不可用」或回退到该英雄的合法位置。

---

## 5. 数据层健康度实测

| 指标 | 实测值 | 评价 |
|---|---|---|
| 英雄数 | 127 | ✅ 含 Kez / Ringmaster / Muerta / Dawnbreaker 等新英雄 |
| 英雄名单时效 | 新鲜 | ✅ 未发现遗漏近期英雄 |
| 对位边总数（合并后） | 1352 | ⚠️ 见 D1（被 top_k 截断） |
| ├ 人工种子 | 89 英雄 / 356 边 | 与 README 声称的 356 条**一致** ✅ |
| └ live 真实对位 | 104 英雄 / 1015 边 | 合并时 live 覆盖种子（19 个英雄的种子被整表替换），故总数 1352 而非 356+1015 |
| 有 live 对位的英雄 | 104 / 127 | ⚠️ 23 个英雄只有人工种子或没有 |
| 零对位英雄 | 4 | ❌ 见 D3 |
| 对位双向自洽性 | 696 对互指，28 对不自洽（最大 0.1） | ⚠️ 幅度小（多为 1pp 内的舍入差），但 `data_loader.py:408` 的注释写「对称回退」、`test_core.py:77` 的 docstring 写「必须反对称」，**两者都没真正做到** |
| 协同覆盖 | 35 英雄 / 81 条边 | ❌ 见 D4 |
| 版本数据新鲜度 | 今天（2026-09-30 15:20） | ✅ 新鲜 |
| 版本号标识 | `patch="current"` | ❌ 见 D6 |
| 单测 | 37 项全通过 | ✅ |
| 端到端自检 | 全绿 | ✅ |

**关于「不自洽 28 对」的说明**：我实测了幅度，`>0.005` 的有 28 条、`>0.02` 的 26 条、**最大 0.1**。这些是 OpenDota 分别以 A 和 B 为 hero 参数请求时返回的独立统计（各自 `wins/games - 0.5`），天然不会严格互反。**实际危害很小**，但它是数据质量的诚实记录，且暴露了测试断言与实际不变量的不一致。

---

## 6. 改进路线建议（按性价比排序）

### 第一梯队（低风险、高收益，建议先做）

| # | 改进 | 对应 | 工作量 |
|---|---|---|---|
| 1 | **取消对位矩阵的 top-14 截断**，保留全部过样本阈值的边 | D1 | 小（改 `refresh_data.py` 几行 + 重跑） |
| 2 | **修 `team_fit` 空 BP 早退**，让位置适配在第一手就生效 | D2 | 小（`engine.py` 一处） |
| 3 | **给推荐理由加「加权贡献」并按其降序排列** | 诉求④ | 小-中（纯展示层 + `engine.rank` 补字段） |
| 4 | **补 4 个零对位英雄的种子数据** | D3 | 小（数据） |
| 5 | **写入真实 patch 号并在 UI 显示** | D6 | 小-中 |

### 第二梯队（中等工作量，价值明确）

| # | 改进 | 对应 | 备注 |
|---|---|---|---|
| 6 | **接 STRATZ GraphQL，引入「高分段切片」**，与 OpenDota 并列而非替换 | 诉求② | 性价比最高的新数据源；`HeroBook.meta` 结构不用改 |
| 7 | **熟练度分位置**：`PlayerHeroStat.by_lane` + 导入时按 `lane_role` 聚合，缺失回退总体值 | 诉求① | 注意 `lane_role`(路) ≠ 位置(1-5)，需要映射启发式 |
| 8 | **赛后复盘闭环**：`tools/sync_results.py` 带 match_id 水位，增量回写英雄池 | 诉求③ | 依赖账号开启 Expose Public Match Data |
| 9 | **合法位置的显式惩罚 + 可选 strict_lane 过滤** | D8 | 与 #2 一起做更划算 |
| 10 | **修 `min_games` 死参数与「没玩过」惩罚条件** | D5 | 顺手 |

### 第三梯队（要谨慎评估）

| # | 改进 | 风险 |
|---|---|---|
| 11 | 协同数据扩充 | OpenDota 无现成接口，需自算；或先降低该因子权重并明确标注「未计入」 |
| 12 | 对手位置推断改进（用英雄的多位置概率而非硬编码顺序） | 需要额外的位置分布数据源 |
| 13 | **读屏幕识别 BP** | **见 §2：中高风险；建议独立仓库、默认关闭、手动触发，不与本项目混同** |

### 关于「快速录入」的合规替代方案（替代 #13）

如果「BP 录入太累」是核心痛点，这几项比读屏更值得做，且零合规风险：

1. **拼音首字母索引**：`fw`→斧王(Axe)、`bng`→白牛(Spirit Breaker)。现有 `normalize()` + ALIASES 机制可直接扩展。
2. **悬浮窗内补全下拉**：输入 2 个字符即弹出候选，方向键 + 回车选择。
3. **一次粘贴批量录入**：允许粘贴「斧王 剑圣 宙斯」这样的串一次性录入（现在必须逐个回车）。
4. **第二屏录入**：`--web` 面板已在，手机/平板打开即可，主屏专注游戏。

---

## 7. 结论

**这个项目不需要重做。** 它的架构选型、合规边界、可解释性设计都是正确的，工程质量（37 项单测 + 端到端自检全绿）也扎实。它的问题集中在**数据层的系统性偏差**（D1/D3/D4）和**引擎在边界条件下的失效**（D2/D5/D8），这些是**可以定点修复的 bug，不是设计错误**。

**最需要立刻修的是 D1 和 D2**：前者让推荐系统性偏向热门英雄，后者让你在最需要帮助的第一手拿到不含位置信息的推荐——这两条直接损害「推荐我擅长且**好发挥**的英雄」这个核心目标。

**关于「读屏幕判断 BP」**：技术上 GSI 在天梯给不了 draft（Valve issue 挂着两年没解决、Overwolf 自己也没有 draft 事件）；合规上 Valve 的措辞覆盖「读客户端数据」，并且他们曾专门用补丁 + 蜜罐打击这类行为。**建议不要把它做进这个项目**，改用 §6 末尾的四条快速录入方案。

---

*本报告只做评审，未修改任何代码。所有实测命令与输出均可复现；外部结论已逐条附 URL 与证据强度，未能验证的部分已明确标注。*
