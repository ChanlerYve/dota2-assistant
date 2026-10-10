# Dota 2 助手类软件生态调研报告

> 调研范围：公开平台可检索到的 Dota 2「助手/数据工具」类软件。
> 重点：Dotabuff、OpenDota、STRATZ（用户所述 "dotastartz"），并覆盖 Dota2ProTracker、Spectral.gg、Windrun.io、Datdota、Liquipedia、中文平台（MAX+/Dotamax、完美世界电竞、刀塔小站锐评器）、Overwolf 对局内助手，以及新出现的 LLM/MCP 助手。
> 调研时间：本报告全部结论来自公开网页与公开仓库，来源 URL 逐条附在文末与各节内。
> 说明：dotabuff.com / windrun.io / spectral.gg / datdota.com / liquipedia.net / developer.valvesoftware.com / dota2.com 均有 Cloudflare 或 Anubis 反爬，正文无法直接抓取；凡涉及这些站点的结论，均标注了证据强度（已验证 / 站点结构可验证 / 推断）。

---

## 一、先给结论：Dota 2 助手可以分成 5 层

| 层 | 代表 | 解决的问题 | 核心思路 |
|---|---|---|---|
| L1 数据门户（赛后/赛前查询） | Dotabuff、OpenDota、STRATZ、Dota2ProTracker、Datdota、Spectral.gg、Liquipedia、Windrun.io | 「这个英雄/出装/对手是什么水平」 | 全量比赛入库 → 聚合统计 → 网页呈现 |
| L2 对局内实时助手（Overlay） | Overwolf DotaPlus、Dota Coach、Dota2 Tracker、LaneMind、Valve 官方 Dota Plus（旧版 STRATZ+、已停运的 Gosu.ai） | 「这一局我现在该干什么」 | 只读 GSI/OCR 拿状态 → 本地 overlay 展示建议与计时 |
| L3 开发者基础设施（API / 开源） | Steam Web API、OpenDota REST API（MIT 可自托管）、STRATZ GraphQL API、manta/clarity 回放解析器、dotaconstants | 「我要自己做一个助手」 | 把 Valve 原始数据变成可编程接口，他人复用 |
| L4 AI 助手（新形态） | opendota-mcp、mcp-stratz、mcp-replay-dota2、dota-ai-coach、LaneMind、Questie AI | 「用自然语言问数据/要复盘」 | MCP 把 API 变成工具 → LLM+RAG 做解释器 |
| L5 社区/内容型 | Discord/Twitch Bot（dotabod）、微信小程序（刀塔小站锐评器）、移动 App（MAX+/Dotamax、完美世界电竞） | 「聊天里查/用手机查」 | 复用 L1/L3 的 API，做分发渠道 |

一句话总结生态分工：**Dotabuff 与 STRATZ 是「重解析 + 重指标」的商业数据站；OpenDota 是「开源 + 开放 API」的公共基础设施；Overwolf 系是「只读 GSI + Overlay」的对局内提示层；LLM/MCP 是刚出现、把前两者当数据源的解释层。**

---

## 二、重点平台逐个分析

### 2.1 Dotabuff（dotabuff.com）

**能力（可验证的部分）**
- 站点板块：Heroes（英雄/物品/组合统计、meta 趋势）、Players（玩家档案、常用英雄、分路）、Matches（比赛库、比赛详情）、Esports、Rankings、TrueSight。TrueSight 与 Plus 均有独立落地页（`/truesight`、`/plus`），可证明这两个是长期产品线，而非营销词。
- **Dotabuff Plus**（订阅）：深度筛选/趋势/玩家进阶数据。2013 年就有玩家讨论「$6/月太贵」，说明订阅模式已运行十年以上。
- **Dotabuff App**（Windows 桌面客户端）：官方博客时间线可作为功能证据——2021-03-18《Announcing the Dotabuff App》、2021-06-23《…new Adaptive Items module》（自适应出装模块）、2021-11-24《Hero Mastery》、《2022-04-12 Dotabuff App Update: Inside Your Game》。因 Cloudflare 拦截无法抓正文，具体模块清单属**推断**：选人期信息、自适应出装、游戏内数据面板。
  - 该 App 与 Overwolf 的关系尚存疑：官方论坛在 2024-01 有《problem in dota buff app #2》、2024-03 有《Dotabuff app, Overwolf》两个讨论帖，说明至少到 2024 年产品仍在被使用/讨论，但**它究竟是自己注入 overlay 还是走 Overwolf 分发，未能验证**。

**技术思路（证据较强）**
- Dotabuff 自建 **回放解析（replay parser）** 能力：它维护并赞助开源 Go 解析器 **manta**（MIT，690 stars，2015 年至今仍在更新，`dotabuff/manta` README 明确写 "maintained and development is sponsored by Dotabuff"），更早还有 Source 1 解析器 **yasha**。
- 数据链路因此是：Steam Web API 拿基础比赛数据 + 下载 `.dem` 回放自行解析出战斗日志级细节（分路、补刀、物品时序、视野/真视）→ 入库聚合成统计。
- 公开可检索范围内**未见官方 API 文档页**（Dotabuff 长期只提供站点，不提供公开 API），所以第三方想用它的数据只能爬取，而站点主体又有 Cloudflare 反爬——这是它与 OpenDota/STRATZ 最大的分野。（旁证：第三方服务在描述中明确写 "Dotabuff does not publish an official public developer API"，属二手来源，未验证。）
- 站点结构可从官方 `robots.txt` 直接读到（该文件未被拦截，可验证）：`/esports/events/*`、`/esports/leagues/*`、`/esports/teams/*`（drafts/heroes/matches/picks/players/scores/series/teams）、`/players/*/heroes|items|matches|records|scenarios|trends|activity|achievements`、`/matches/*`、`/search`、`/signin`——说明它同时有「玩家维度下钻」与「电竞/联赛维度」的完整路由树。
- 时效与 Plus 权益的**二手描述（未验证）**：赛后约 1 小时内入库、玩家档案约每 15 局批量刷新，且依赖账号开启 "Expose Public Match Data"；Plus 提供详细 build、击杀/死亡经济曲线、英雄对位、物品时间线、farm 曲线、视野与战斗日志，俄区售价约 200₽/月。

**为什么值得关注**：它是「自建解析 + 完全自持数据资产 + 订阅变现」这条最重路线的代表，也是 manta 这个被整个生态复用的解析器的孵化者。

来源：<https://github.com/dotabuff/manta>、<https://pkg.go.dev/github.com/dotabuff/manta>、<https://github.com/dotabuff/manta/blob/master/README.md>、<https://www.dotabuff.com/blog/2021-03-18-announcing-the-dotabuff-app>、<https://uk.dotabuff.com/blog/2021-06-23-announcing-the-dotabuff-apps-new-adaptive-items-module>、<https://es.dotabuff.com/blog/2022-04-12-dotabuff-app-update-inside-your-game>、<https://bg.dotabuff.com/blog/2021-11-24-introducing-hero-mastery-and-a-holiday-sale>、<https://ru.dotabuff.com/plus>、<https://pl.dotabuff.com/truesight>

### 2.2 OpenDota（opendota.com）——生态的「公共水电」

**能力**
- 公开 REST API（`docs.opendota.com`），覆盖 matches / players / heroes / heroStats / benchmarks / distributions / rankings / proMatches / publicMatches / live / explorer（SQL 查询接口）等大类；并把「请求解析某场比赛（parse request）」做成一等公民。
- 开源 UI（`odota/ui`）+ 开源后端（`odota/core`，MIT，1631 stars）+ 公开常量库 `odota/dotaconstants`。

**技术架构（README + 官方架构博客，已完整抓取）**
- 微服务，Node.js（现在是 TypeScript）；数据库 **PostgreSQL / Redis / Cassandra**；解析器 **Java**（基于 `skadistats/clarity`）。
- `docker-compose.yml` 里的组件即为最小生产形态：`postgres`、`cassandra`、`redis`、`minio`（S3 归档回放）、`parser`（镜像 `odota/parser`）、`core`（API + worker）。
- PM2 管理的进程（官方架构文）分工清晰：
  - `web`：全部查询/过滤/聚合/缓存；
  - `retriever`：对接 Steam GC，取回放盐值/replay URL（**需要一个小号 Steam 账号**，不能用主号，且不能开 Steam Guard）；
  - `parser`：下载 `.dem.bz2` → bunzip2 → Java 解析器流式吐出 newline-delimited JSON → 合成 `parsed_data` 大对象；
  - `scanner`：读 Steam 的 sequential API 扫新比赛入库，打 `parse_status=0` 入解析队列；
  - `requests` / `mmr` / `fullhistory` / `skill` / `profiler` / `proxy` / `cacher` / `monitor`：分别负责解析请求排队、MMR 抓取、拉取玩家全部历史（按英雄逐个查最近 500 场）、分技能等级采样、批量刷新玩家档案、代理 Steam API、Cache 更新、健康监控。
  - 性能设计：比赛 JSON 缓存在 Redis；玩家-比赛关系写入 Cassandra（首次落库后，档案页后续加载走 Cassandra 而不是 Postgres）。

**配额与开放度（关键落地信息，已亲自实测）**
- 直接请求 `https://api.opendota.com/api/metadata` 返回：`freeCallLimit: 3000`、`freeRateLimit: 60`、`premRateLimit: 3000`、`isSubscriber: false`。即：**免费 60 次/分钟、3,000 次/日；付费 key 3,000 次/分钟**（付费按量计费，社区口径约 $0.01/100 次调用）。无 key 也能用，只是额度低。
- **解析请求很贵**：`POST /request/{match_id}` 一次按 **10 次调用**扣额——如果你的产品依赖「逐场解析详情」，额度消耗速度是普通查询的 10 倍。
- 订阅（$5/月）：获得自动解析你自己的比赛 + 订阅者标识（源码文案 `subscriptions_*` 可查）。Patreon 是否仍是主渠道未验证。
- 完全可自托管：这是 OpenDota 与商业站的根本差别——**你可以在自己的服务器上跑一份，把「配额」和「数据归属」问题一次性解决**（代价是自备 Steam API Key + 一个用于取回放的 Steam 小号 + 存储）。
- 一个容易被忽略的限制：**未被「登录/订阅/捐赠」覆盖的玩家，其比赛不会被自动解析**，因此 OpenDota 的 parsed 数据覆盖度与 Dotabuff 这类全量解析站存在差异（量化差异未验证）。

**使用思路**：OpenDota 的定位不是「更好的 Dotabuff 网页」，而是「把 Dota 数据变成可编程接口」，让所有上层助手（Discord bot、MCP server、桌面 overlay、学术分析）都能白嫖基础设施。它的商业模式天然轻（捐赠/Patreon），因为价值主要在开放数据与 API 生态。

来源：<https://github.com/odota/core>、<https://api.github.com/repos/odota/core>、<https://cdn.jsdelivr.net/gh/odota/core@master/README.md>、<https://cdn.jsdelivr.net/gh/odota/core@master/docker-compose.yml>、<https://blog.opendota.com/2016/05/15/architecture/>、<https://api.github.com/repos/odota/web/issues/3247>

### 2.3 STRATZ（stratz.com）——把数据站做成「AI 指标工厂」

**能力（站点导航 + 官方 FAQ 仓库可验证）**
- 站点模块：Welcome / **R.O.S.H.** / Heroes / Players / Matches / Leagues / Battle Pass / **Road to Grandmaster** / Guilds / Leaderboards / Knowledge Base / Special Events / API / Sitemap，另有 **STRATZ+** 订阅与 **GraphQL API** 入口。
- 比赛分析：分钟级时序（每分钟补刀/击杀等）、浏览器内回放、**IMP**（个人表现分）、**TMP**（团队表现分）、**胜率预测（神经网络）**、**LaneScore**（分路优劣）、毒性评分、**小号检测**、活动等级、stomp/close/comeback 分类、职业选手对局记录。
- 玩家档案：段位/赛季进度、最擅长与最差英雄、对位优劣。
- Hero meta：按段位/位置/版本/地区/时间切片（例：`/heroes/meta/trends?bracketIds=IMMORTAL&time=Day`）。
- 自研 replay parser 支撑技能加点与物品时序，因此「数据站」与「解析器」是同一家公司。
- **STRATZ+ 的历史非常重要**：2020 年的官方 FAQ 说明它用 **OCR** 读取选人界面（不是读内存），对外提供实时 BP 建议、阵容优劣势、胜率与分路预测；2023 年 2 月 **因 Valve 的一次更新而停运**（官方 FAQ 原文引用了 Valve 的公告链接）。现「STRATZ+」已转为订阅制深度分析。

**API（对开发者最有价值的一节）**
- 端点：`https://api.stratz.com/graphql`（GraphiQL 在线调试），鉴权 `Authorization: Bearer <STRATZ_TOKEN>`，token 在 `stratz.com/api` 用 Steam 登录免费领取。
- 限流（官方 KB 明确数字）：
  - Default Token：20 次/秒、250/分钟、2,000/小时、10,000/天；
  - Individual Token（需申请）：20/秒、250/分钟、4,000/小时、20,000/天；
  - Multi-Token（可给每个终端用户单独发 token）：20/秒/用户、20/分钟、50/小时、100/天。
- 官方自述相对其他 API 的差异：**唯一提供 GraphQL 的公开 Dota API**（一次请求取回多类数据）、唯一提供分钟级统计、以及独占的自研 AI 指标（IMP/TMP 等）。
- 生态位：Dota2ProTracker、Overwolf 上的 Dota2 Tracker、LaneMind、dotabod、第三方 Discord bot、中文「锐评器」都在不同程度上消费 STRATZ 数据。

**使用思路**：STRATZ 是「增值指标」路线——同样的 Valve 原始数据，它把价值放在自研算法（LaneScore/IMP/TMP/毒性/小号识别）和 GraphQL 交付效率上；API 免费放量、网站订阅变现。

来源：<https://stratz.com/api>、<https://api.stratz.com/graphiql>、<https://stratz.com/plus>、<https://stratz.com/rosh>、<https://github.com/STRATZ-eSports/knowledge-base>、KB issue #15/#16/#17/#18/#19/#20/#34/#35/#37/#39/#40（<https://github.com/STRATZ-Esports/knowledge-base/issues/15> 等）、<https://pipeworx.io/docs/reference/stratz/>

### 2.4 Dota2ProTracker（dota2protracker.com）

- 定位：**只切 7000+ MMR 天梯与职业比赛**这一人群，输出「当前版本真正强势的英雄/出装/加点」。首页实测口径：「Analyzed 49.7K matches · last 8 days」，按位置给出 D2PT Rating。
- 功能：Meta（分位置）、Tier Lists、Pubs vs Pro（路人 vs 职业差异）、Radiant/Dire、Combos、Items、Patches、Drafter（选人器）、Hero Grids、Matchup Search、Replay 上传、Tournaments、Live。
- 数据来源：自家抓取 + 第三方增强，页脚明示 **"Data enriched by Imprint & Stratz"**；核心免费。
- 思路：**人群切片 = 领先指标**。普通数据站的英雄胜率是全体平均，高分/职业切片能提前反映版本答案，所以「抄 build」的用户粘性极高。

来源：<https://dota2protracker.com/>

### 2.5 Spectral.gg / Datdota / Liquipedia / Windrun.io（垂直与职业向）

- **Spectral.gg**：面向解说、分析师、职业队的赛事数据中枢。League Report Generator(LRG) 报告、按选人阶段（stage 1–3）的 pick/ban 胜率与排名、战队/选手档案、英雄组合、经济占比、Roshan/塔时序、**眼位与开雾热力图（128×128 地图分区）**。自建 WebAPI（`stats./builds./tools.spectral.gg` + `/docs`）与 **fetcher 解析队列**（按 match ID 入队自解析），即它自己有解析能力而不只是读别家 API。免费 + 捐赠。
- **Datdota**：2011 年至今的职业比赛数据库，按 tier（1/2/3）、patch、league、LAN/online 过滤；`/ratings` 用 **Glicko-2** 给战队与选手评级，被转播与分析师引用。无官方 API 文档、有反爬、免费。
- **Liquipedia**：社区维基（赛事/战队/选手/转会/排名），CC-BY-SA，MediaWiki 风格 API 但 ToS 极严（限速、要求自定义 UA）。
- **Windrun.io**：**Ability Draft（技能征召）**垂直站——技能胜率/选取、高分局技能数据、技能二连/三连组合、按版本切换、玩家档案；提供公开 REST `https://api.windrun.io/api/v2`（static/abilities、ability-pairs、ability-triplets、ability-shifts、players/{steamID32}/stats 等）。
- 思路总结：**当一个玩法/人群足够特殊（职业、AD、赛事），通用数据站就吃不透，垂直站靠「更细的维度」建立不可替代性**。

来源：<https://spectral.gg/docs/guame/02-webapi>、<https://stats.spectral.gg/lrg2/>、<https://datdota.com/about>、<https://www.cybersport.ru/tags/dota-2/datdota-nazvala-evil-geniuses-luchshey-komandoy-mira>、<https://liquipedia.net/dota2/Portal:Rankings>、<https://raw.githubusercontent.com/Tiarin-Hino/ability-draft-plus/main/README.md>

### 2.6 中文平台

| 平台 | 形态 | 能力 | 思路 |
|---|---|---|---|
| **MAX+ / Dotamax**（`com.dotamax.app`，iOS/Android） | 移动 App（DOTA2 + CS:GO 双游戏） | 英雄使用率、物品搭配胜率、个人战绩趋势、伤害/经济数据、英雄攻略与资讯、赛事资讯；商店页描述「所有数据均根据官方 API 实时更新」 | 用官方 API 做「中文数据门户 + 社区」，把 PC 站点的查询能力搬到手机 |
| **完美世界电竞 App**（`com.pwrd.steam.esports`） | 国服官方生态 App | 赛事/资讯/账号与国服相关服务 | 发行商侧的官方入口 |
| **刀塔小站「Dota2 锐评器」** | 微信小程序 / 社群工具 | 用 **STRATZ 数据**做玩家与阵容「锐评」，服务赛前资料准备 | 不自己建数据管道，直接消费 STRATZ API 做本地化包装（成本极低、见效快） |

来源：<https://m.gameyj.com/down/21062.html>、<https://apps.apple.com/il/app/max-for-dota2-cs-go/id990892583>、<https://play.google.com/store/apps/details?id=com.pwrd.steam.esports>、<https://www.bilibili.com/opus/1202585663167266824>、<https://bbs.nga.cn/read.php?tid=46596023>

---

## 三、对局内实时助手（Overwolf 生态 + GSI）

### 3.1 官方机制：GSI（Game State Integration）

- Dota 2 客户端会以 **HTTP POST（JSON）**向本地地址推送游戏状态，前提是两份配置：
  1. 游戏目录 `…/dota 2 beta/game/dota/cfg/gamestate_integration/gamestate_integration_<名字>.cfg`，声明 `uri`、`timeout`、`buffer`、`throttle`、`heartbeat` 以及 `data` 开关（auth、provider、map、player、hero、abilities、items、events、buildings、league、draft、wearables、minimap、roshan、couriers、neutralitems）；
  2. 启动项加 `-gamestateintegration`（Overwolf 官方支持文档明确要求，且说明可用 `getGameInfo().ProcessCommandLine` 检测用户是否已开启）。
- 能拿到什么（以成熟库的字段结构为准）：比赛 ID/时长/游戏状态、本地玩家的 KDA/补刀/经济/GPM/XPM/插眼/堆野、英雄血量蓝量与状态、技能与物品冷却、天赋、建筑血量、Roshan、信使、中立物品、**draft（BP 结果）**、观战时全 10 人数据。
- **硬限制**：GSI 只暴露 Valve 决定暴露的字段；自己打天梯时**只有本地玩家**的完整信息，观战/教练位才有全员。

### 3.2 生态与产品

| 产品 | 状态 | 能力 | 思路 |
|---|---|---|---|
| **Overwolf DotaPlus**（Overwolf 自家） | 活跃（1.57M 下载，v6.0.55） | ban/pick 建议、**小号/代练/买号检测**、永久玩家笔记、比赛与 MMR 追踪（含私密资料）、组队检测、敌我近期战绩、每局表现分；`Alt+~` 呼出 | 把「选人期的信息优势 + 长期玩家画像」商品化，靠广告变现 |
| **Dota Coach**（dota-coach.com） | 活跃（78.1K 下载） | 选人期敌我统计、敌方近期出装、克制装备推荐、Roshan/Aegis/Glyph/买活/大招计时、赏金符/水符/能量符/中立物品/昼夜提醒、**语音教练**、战术投票 | 「新手减负 + 事件计时」：把需要背的常识自动化，免费+订阅 |
| **Dota2 Tracker**（第三方个人开发者） | 活跃（1.06K 下载） | 赛后一键给队友/对手打分写笔记、重逢时自动弹出历史评价、STRATZ 能力雷达、**GSI 实时净资产浮窗**、私人 Tier List；数据仅存本地 | 「主观记忆 + 客观数据」结合，强调隐私与合规（只在选人后展示） |
| **LaneMind** | 活跃（新） | GSI 实时 overlay、出装时机分析、敌方威胁读取、赛后 AI 复盘报告、OpenDota+STRATZ 统计面板、**BYOK**（自带 OpenAI/Anthropic/xAI/Google Key）或订阅托管额度（9€/月 Pro） | 「实时 overlay + LLM 复盘」，用 BYOK 把模型成本转给用户 |
| **Valve 官方 Dota Plus** | 活跃（2018-03-13 上线，$3.99/月） | 英雄等级/经验与徽章、Reward Shards（每周额外 1,000）、英雄挑战（两周刷新、三星难度）、Relics（每英雄 14 槽 = 10 普通 + 4 稀有，赛后面板显示进度）、Hero Chat Wheel、**Plus Assistant**（按已选分路与双方阵容给出装/加点/英雄建议、分路策略、**死亡回顾**）、赛后分析 | 官方把「统计 + 引导」内建到客户端，是第三方助手的功能天花板参照 |
| **STRATZ+** | **2023-02 停运（原版）** | OCR 读 BP → 实时选人建议、阵容优劣、胜率与分路预测 | 不读内存、只 OCR，仍在 Valve 更新后主动下架——合规风向标 |
| **Gosu.ai** | 已停运（2022-03） | AI 教练/训练 | 纯 AI 教练在 Dota 的商业化未能跑通 |

### 3.3 技术路线与红线

- **合法路线只有一条：只读 + 不改游戏**。GSI 是 Valve 官方设计的数据出口；Overwolf 是被发行商认可的分发平台（且 Dota 2 侧最终仍走 `-gamestateintegration`）。
- **红线**：Valve 在 2023-02 的官方声明（40,000 账号永封）原文为——"If you are running any application that reads data from the Dota client as you're playing games, your account can be permanently banned from playing Dota. This includes professional players…"。这句话比「禁止读内存」更宽：**只要你从客户端进程里读数据，就可能被永久封禁**，「客户端本来能看到」不构成豁免。
- **灰色地带**：OCR 屏幕识别（STRATZ+ 的原始做法）不读内存，Valve 未点名，但表述宽泛；且 STRATZ+ 仍在 2023-02 主动停运，说明这条路风险不可忽略。
- **未证实传闻**：GitHub 上有玩家举报 "Overwolf/Umbrella 在封号潮后仍可用"（issue #21103，8 条评论，Valve 从未回复，最终因 stale 被机器人关闭）；另有开发者询问能否读内存拿 BP 数据（issue #15007，同样被 stale 关闭）。**这两条只能算社区说法，不能当作事实**。
- 结论：做对局内助手，**把 GSI 当作唯一实时数据源**，把历史数据交给公开 API——这也是所有活着的产品（Dota Coach、LaneMind、DotaPlus）的共同选择。

来源：<https://github.com/antonpup/Dota2GSI>、<https://cdn.jsdelivr.net/gh/tomasfarias/dota-gsi@master/README.md>、<https://dev.overwolf.com/ow-electron/live-game-data-gep/supported-games/dota-2/>、<https://support.overwolf.com/en/support/solutions/articles/9000212745-how-to-enable-game-state-integration-for-dota-2>、<https://www.overwolf.com/app/overwolf-dotaplus>、<https://www.overwolf.com/app/dota-coach.com-dota_coach>、<https://overwolf.com/app/catcherinsky-dota2_tracker>、<https://www.lanemind.com/zh/faq>、<https://www.siliconrepublic.com/business/valve-dota-2-bans-cheaters-trap>、<https://github.com/ValveSoftware/Dota2-Gameplay/issues/21103>、<https://github.com/ValveSoftware/Dota2-Gameplay/issues/15007>

---

## 四、AI / MCP 助手：2024–2026 的新增量

| 项目 | 形态 | 做法 |
|---|---|---|
| **opendota-mcp-server** | MCP server（Python） | 把 OpenDota API 封装成 20+ 工具（玩家统计、英雄、比赛分析），让 Claude/ChatGPT 等自然语言查询；同类 `@pipeworx/mcp-opendota` |
| **mcp-stratz**（Pipeworx） | MCP server | 12 个工具：`graphql` 透传 + hero/hero_stats/match/player/player_matches/live_matches/tournament 等命名工具；用 STRATZ token 鉴权 |
| **mcp-replay-dota2** | MCP server | 直接解析本地 `.dem` 回放，把「回放解析」变成 LLM 可调用的工具 |
| **dota-ai-coach**（BrightGir） | 桌面辅助（Go + RayLib overlay） | GSI 收实时状态 → BERT + Chroma **RAG** 检索英雄/技能/物品知识 → 调 Gemini/OpenRouter 生成建议 → overlay 显示，支持自动 N 秒建议与热键提问 |
| **LaneMind** | 桌面教练 | GSI 实时 + 托管/自带 LLM 生成结构化赛后复盘与「下一局唯一重点」 |
| **dotabod**（开源） | Twitch/Discord 机器人 | 主播向：GSI 事件 + STRATZ 胜率数据 + Twitch chat 互动（`!wp` 等） |
| **Questie AI** | 通用游戏 AI 伴侣 | 屏幕理解 + 语音聊天，Dota 2 是其中一个游戏 |

**这类产品的共同思路**：**数据源复用（OpenDota/STRATZ）+ 实时上下文（GSI）+ LLM 做「解释器」而不是「数据库」**。工程难点不在模型，而在：
1. 上下文裁剪（GSI 每秒都在推数据，必须只挑有信息量的差异）；
2. 幻觉抑制（用 RAG 把 dotaconstants/物品/技能描述喂进 prompt）；
3. 成本控制（BYOK / 按 credit 计费 / 缓存）。

来源：<https://github.com/hkaanengin/opendota-mcp-server>、<https://api.github.com/repos/hkaanengin/opendota-mcp-server>、<https://pipeworx.io/docs/reference/stratz/>、<https://github.com/DeepBlueCoding/mcp-replay-dota2>、<https://cdn.jsdelivr.net/gh/BrightGir/dota-ai-coach@main/README.md>、<https://cdn.jsdelivr.net/gh/dotabod/backend@master/README.md>、<https://www.questie.ai/zh/gaming-ai/dota-2>

---

## 五、横向对比

| 平台 | 数据来源 | 回放解析 | 实时对局内 | 开放 API | 变现 | 独立自托管 |
|---|---|---|---|---|---|---|
| Dotabuff | Steam Web API + 自研解析（manta） | ✅（自有） | 桌面 App（2024 年仍有社区讨论，形态/现状未验证） | ❌ 无公开文档 | Plus 订阅 | ❌ |
| OpenDota | Steam Web API + 自动解析（clarity/Java） | ✅（开源） | ❌ | ✅ REST + SQL Explorer；免费 60/min·3000/天，付费 key 3000/min | 捐赠 + $5/月订阅 | ✅ MIT |
| STRATZ | Valve 数据 + 自研解析 | ✅（自有） | STRATZ+ 原为 OCR BP 助手（已停运），现为订阅分析 | ✅ GraphQL，默认 10k/天 | STRATZ+ 订阅 | ❌ |
| Dota2ProTracker | 高分局 + 职业比赛，Imprint & Stratz 增强 | 部分（依赖上游） | ❌ | ❌ | 免费+支持 | ❌ |
| Spectral.gg | 自建 fetcher 解析队列 | ✅ | ❌ | 自建 WebAPI（面向合作方） | 捐赠 | ❌ |
| Datdota | 职业比赛库（2011 起） | 依赖上游 | ❌ | ❌（社区逆向） | 免费 | ❌ |
| Windrun.io | 自建抓取 | 部分 | ❌ | ✅ `api.windrun.io/api/v2` | 免费 | ❌ |
| Overwolf DotaPlus / Dota Coach / LaneMind | GSI + 公开 API | ❌ | ✅ | ❌ | 广告 / 订阅 / BYOK | ❌ |

---

## 六、如果要自己做一个 Dota 2 助手：可落地的参考架构

**最小可行版本（一周级）**
1. **历史数据**：直接用 OpenDota REST（免费 60/min、3,000/天；付费 key 3,000/min；注意单次 parse 请求按 10 次扣额），或 STRATZ GraphQL（默认 10k/天）——先用现成 API，别一上来就自建解析。
2. **实时数据**：写一个本地 HTTP 服务 + `gamestate_integration_*.cfg`，只开需要的 `data` 开关（provider/map/player/hero/abilities/items），启动项 `-gamestateintegration`。
3. **呈现**：Windows 无边框窗口 overaly（Dota 必须运行在 Borderless Windowed）；或先做 Discord/Web 端，避开 overlay 工程复杂度。
4. **智能**：接现成 MCP server（opendota-mcp / mcp-stratz）而不是自己写 API 封装；LLM 只负责解释与建议，事实一律来自检索。

**进阶（要差异化时再投入）**
- 需要「分钟级时序 / 战斗日志级细节」→ 自建解析：Go 用 `dotabuff/manta`，Java 用 `clarity`；解析需要从 Steam GC 取回放盐值，**需单独的小号 Steam 账号且不开 Steam Guard**（OpenDota 的经验）。
- 需要「自研指标」→ 参考 STRATZ 的做法：LaneScore = 分路净经济 + 分路经验 + 分路补刀 + 分路 KDA/2，再按分路与段位做分位基准；胜率预测用神经网络 + 分钟级特征。
- 需要「大量终端用户」→ 用 STRATZ 的 **Multi-Token** 模式（每用户独立 token，20/s、20/min、50/h、100/天），避免单 token 被打爆。

**必须遵守的红线**
1. 不读内存、不注入、不修改游戏文件、不自动化输入（Valve 2023-02 声明）。
2. 尊重上游 API 限流与 ToS（OpenDota 免费 60/min·3,000/天、付费 3,000/min、parse=10 次；STRATZ 见上；Liquipedia 限速与 UA 要求；Windrun 要求标识参数）。
3. 数据缓存与署名（Dota2ProTracker 就公开标注数据由 Imprint 与 Stratz 增强）。
4. 若要在对局内展示「对手信息」，注意 STRATZ 的历史教训：即便只 OCR，也可能因 Valve 政策更新而下架。

---

## 七、检索受限说明（影响结论强度）

| 站点 | 拦截方式 | 受影响结论 |
|---|---|---|
| dotabuff.com 子域正文页 | Cloudflare（403 "Just a moment"） | Dotabuff 具体功能清单、Plus 权益细节、Dotabuff App 现状——**基于博客标题、`robots.txt` 路由与二手来源推断**（`https://www.dotabuff.com/robots.txt` 本身可正常读取） |
| windrun.io、spectral.gg、datdota.com | Cloudflare / 403 | 站点正文未能读取，功能信息来自官方 API 文档、集成方源码与第三方访谈 |
| liquipedia.net、developer.valvesoftware.com | Cloudflare / Anubis PoW | GSI 官方 wiki 正文未取到，改用成熟 GSI 库 README 与 Overwolf 官方文档交叉验证 |
| dota2.com、dota2.com.cn、theverge.com | 抓取失败 | Valve 2023-02 声明改用可抓取的等价来源（Silicon Republic 原文引用） |
| web.archive.org、raw.githubusercontent.com | 连接失败 | 回退到 jsDelivr CDN 读取 GitHub 原始文件（等价内容） |

---

## 八、参考来源（按平台归组）

**Dotabuff / 解析器**
- <https://github.com/dotabuff/manta> ｜ <https://cdn.jsdelivr.net/gh/dotabuff/manta@master/README.md> ｜ <https://pkg.go.dev/github.com/dotabuff/manta>
- <https://www.dotabuff.com/blog/2021-03-18-announcing-the-dotabuff-app> ｜ <https://uk.dotabuff.com/blog/2021-06-23-announcing-the-dotabuff-apps-new-adaptive-items-module> ｜ <https://es.dotabuff.com/blog/2022-04-12-dotabuff-app-update-inside-your-game> ｜ <https://bg.dotabuff.com/blog/2021-11-24-introducing-hero-mastery-and-a-holiday-sale>
- <https://ru.dotabuff.com/plus> ｜ <https://pl.dotabuff.com/truesight> ｜ <https://www.dotabuff.com/robots.txt>（路由结构）
- 二手来源（仅作旁证，未验证）：<https://parse.bot/marketplace/7f1a5ae0-1a48-4f7d-9e99-8b5ce1804c5b/dotabuff-com-api>（无公开 API）｜ <https://dota2-lega.ru/articles/dotabuff-plus-chto-eto-takoe-i-stoit-li-svoih-200-rubley-v-mesyats>（Plus 权益与价格）｜ <https://dota2-lega.ru/articles/proizvoditelnost-i-optimizatsiya/pochemu-dotabuff-pokazyvaet-staruyu-statistiku-i-kak-eto-ispravit>（数据时效）

**OpenDota**
- <https://github.com/odota/core> ｜ <https://cdn.jsdelivr.net/gh/odota/core@master/README.md> ｜ <https://cdn.jsdelivr.net/gh/odota/core@master/docker-compose.yml>
- <https://blog.opendota.com/2016/05/15/architecture/> ｜ <https://api.github.com/repos/odota/web/issues/3247> ｜ <https://docs.opendota.com/> ｜ <https://api.opendota.com/api/metadata>（配额实测）｜ <https://api.opendota.com/api/>

**STRATZ**
- <https://stratz.com/api> ｜ <https://api.stratz.com/graphiql> ｜ <https://stratz.com/plus> ｜ <https://stratz.com/rosh>
- <https://github.com/STRATZ-Esports/knowledge-base/issues/15>（限流）｜ /37（Token 类型）｜ /40（STRATZ+ 停运）｜ /19、/20（能力与 API 差异）｜ /16、/39（算法）｜ /34、/35（指标定义）
- <https://pipeworx.io/docs/reference/stratz/>

**职业/垂直数据站**
- <https://dota2protracker.com/> ｜ <https://spectral.gg/docs/guame/02-webapi> ｜ <https://stats.spectral.gg/lrg2/> ｜ <https://datdota.com/about> ｜ <https://liquipedia.net/dota2/Portal:Rankings> ｜ <https://raw.githubusercontent.com/Tiarin-Hino/ability-draft-plus/main/README.md>

**实时助手 / GSI / 合规**
- <https://github.com/antonpup/Dota2GSI> ｜ <https://cdn.jsdelivr.net/gh/tomasfarias/dota-gsi@master/README.md> ｜ <https://dev.overwolf.com/ow-electron/live-game-data-gep/supported-games/dota-2/> ｜ <https://support.overwolf.com/en/support/solutions/articles/9000212745-how-to-enable-game-state-integration-for-dota-2>
- <https://www.overwolf.com/app/overwolf-dotaplus> ｜ <https://www.overwolf.com/app/dota-coach.com-dota_coach> ｜ <https://overwolf.com/app/catcherinsky-dota2_tracker> ｜ <https://www.lanemind.com/zh/faq> ｜ <https://www.lanemind.com/zh/lanemind-vs-stratz>
- <https://www.siliconrepublic.com/business/valve-dota-2-bans-cheaters-trap> ｜ <https://github.com/ValveSoftware/Dota2-Gameplay/issues/21103> ｜ <https://github.com/ValveSoftware/Dota2-Gameplay/issues/15007> ｜ <https://www.dota2.com/plus> ｜ <https://www.gamereactor.eu/dota-2-gets-subscription-service-dota-plus/>（2018 首发时对官方功能描述的原文引用）

**AI / MCP**
- <https://github.com/hkaanengin/opendota-mcp-server> ｜ <https://github.com/DeepBlueCoding/mcp-replay-dota2> ｜ <https://cdn.jsdelivr.net/gh/BrightGir/dota-ai-coach@main/README.md> ｜ <https://cdn.jsdelivr.net/gh/dotabod/backend@master/README.md> ｜ <https://www.questie.ai/zh/gaming-ai/dota-2>

**中文平台**
- <https://m.gameyj.com/down/21062.html>（Dotamax 功能描述）｜ <https://apps.apple.com/il/app/max-for-dota2-cs-go/id990892583> ｜ <https://play.google.com/store/apps/details?id=com.pwrd.steam.esports> ｜ <https://www.bilibili.com/opus/1202585663167266824> ｜ <https://bbs.nga.cn/read.php?tid=46596023>
