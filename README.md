# Dota2 选人助手（dota2-assistant）

对局 BP（Ban/Pick）阶段的选人决策助手：在你**会玩的英雄**和**这局该选的英雄**之间算出一个可解释的最优解。

> 📖 **只想用、不想读设计文档？** 直接看 [用户操作手册](docs/用户操作手册.md)——
> 安装、建英雄池、录 BP、看推荐、调参、故障排查都在那里，按操作顺序写的。

- **语音录入**：说「斧王」「剑圣」「jugg」就能录入 BP。用 Windows 自带识别，**离线、不联网、不需要 API Key**。
- **别名认得全**：527 条别名，覆盖中文名（剑圣/敌法/火猫）、英文绰号（jugg/wisp/abba/naix）、缩写（am/sf/qop）。
- **分位置熟练度**：算的是「你打 3 号位的斧王」，不是「你玩斧王」——两个位置表现相反时会给出完全不同的推荐。
- **高分段切片**：可切到 Divine / Ancient 等档位看版本答案。实测全体前 10 与 Divine 前 10 **只重合 5 个**。
- **赛后复盘闭环**：一条命令把真实对局结果（含位置）增量回写英雄池，熟练度自动校准。
- **叠加显示**：Windows 置顶半透明悬浮小窗，可以贴在游戏画面上；也可以切换成浏览器面板。
- **只用公开数据**：不注入、不读内存、不抓包、不改游戏文件、不做画面识别。输入来自你自己的键盘或麦克风。
- **离线可用**：英雄库、别名表、克制矩阵内置；联网时可以一键用 OpenDota 的公开数据刷新版本胜率与真实对位。

```
┌─ 悬浮窗 / 浏览器面板 ────────────────────┐
│  我 斧王③、冰女⑤      敌 剑圣、宙斯      │   ← 你录入的 BP（手打或语音）
│  缺口 先手、清线                          │   ← 阵容诊断
│  1. Spirit Breaker ★★★★☆  71.2          │   ← 推荐（可解释）
│     你打 4 号位 32 局 59%（贡献 24.3 分）  │   ← 每条理由带加权贡献，按影响力排序
│     Divine 段胜率 52.1%（3.5 万场）        │   ← 版本强度标明用的哪个切片
└──────────────────────────────────────────┘
```

---

## 1. 30 秒上手

```bash
cd dota2-assistant

python -m d2a --demo                      # 0. 先看效果：跑一个仿真对局
python -m d2a                             # 1. 启动悬浮窗（默认）
python -m d2a --voice                     #    启动并立刻开启语音录入
python -m d2a --voice-probe               #    只测一次语音识别，看麦克风行不行
python -m d2a --web                       #    或启动浏览器面板
python -m d2a --steam 12345678            # 2. 用公开战绩导入你的英雄池
python -m d2a --cli                       # 3. 命令行交互模式（最轻量）
```

要求：Python 3.9+，**零第三方依赖**（悬浮窗用到标准库的 tkinter，Windows 官方 Python 自带）。
本机已实测：Python 3.10.11。

第一件该做的事是**把英雄池填上**（这是推荐质量的 70%）：

```bash
# 方式 A：从公开战绩导入（需要联网，读 OpenDota 公开接口）
python -m d2a --steam <你的 Steam32 位 ID>

# 方式 B：手工填 config.json（复制 config.example.json）
#   "pool": { "Axe": [60, 37], "Crystal Maiden": [88, 50] }   ← [局数, 胜场]

# 方式 C：命令行/Web 面板里逐个加
python -m d2a --cli
> pool add 斧王 60 37          # 支持中文名
# 或 python -m d2a --web 后在右侧「我的英雄池」里填（同样支持中文名）
```

### 1.1 语音录入

**快捷键**：`Ctrl+Alt+V` 开/关语音；悬浮窗标题栏的 🎤 按钮、Web 面板的「🎤 语音」按钮等效。

**先跑自检**（不依赖游戏，最快确认环境）：

```bash
python -m d2a --voice-probe --voice-seconds 6
# 语音自检：监听 6 秒，模式 hero。请对着麦克风念英雄名（如「斧王」「剑圣」）…
#   [就绪] zh-CN hero
#   [听到] 斧王  (0.91)
```

**识别不出来时按顺序检查**：

1. Windows 设置 → 隐私和安全性 → 麦克风 → 允许桌面应用访问（**最常见的原因**）
2. 设置 → 系统 → 声音 → 默认输入设备是否选对
3. 设置 → 时间和语言 → 语言 → 中文(简体) → 语言选项 → 语音包是否已安装
   （没有语音包时脚本会明确报「系统没有安装任何语音识别引擎」）

**工作原理与为什么这么做**：`tools/voice_listen.ps1` 调 Windows 自带的
`System.Speech`，默认把识别范围**约束成英雄名 + 别名的语法**（而不是自由听写），
所以准确率显著更高。识别出的文本走**与手打完全相同**的解析管线
（`HeroBook.resolve`），别名/中文名/缩写全部生效。想自由说话就切
`mode: "dictation"`（准确率会明显下降）。

### 1.2 赛后复盘闭环（分位置熟练度靠它）

熟练度用的是贝叶斯收缩，本身就会随真实战绩自我校准；缺的只是**把结果灌回去的管道**。

```bash
# 先看会同步到什么，不写文件
python tools/sync_results.py --account-id <你的32位ID> --limit 20 --probe

# 确认无误后正式同步（每次打完几局跑一次）
python tools/sync_results.py --steam <64位ID或主页链接> --limit 20
```

它会：拉最近对局列表 → 按 `match_id` 水位筛出**新**对局 → 逐场取详情拿到
`position_est`（1~5 号位）→ 按位置聚合 → 增量回写 `config.json` 的 `pool`，
并保留水位，所以**重复运行不会重复计数**。

实测输出：

```
英雄池: 5 个英雄（来自 config.json）
水位: 9022301512；列表返回 6 局
没有新对局需要同步（跳过 6 局已处理的）。
```

回写后的记录长这样（`by_lane` 就是分位置战绩）：

```json
"pool": {
  "Keeper of the Light": {"hero":"Keeper of the Light","games":2,"wins":1,
                          "by_lane":{"2":[2,1]},"last_match_id":9021987352}
}
```

**两个已知限制**（诚实说明）：

1. 位置来自单场详情，**每场一次请求**（免费额度 60/分钟、3000/天），所以默认只回溯 20 局；
   已缓存的比赛不会重复请求。
2. **旧对局常未被解析**，没有 `position_est`。这种局只计入总体战绩（会打印「N 局没有位置数据」），
   不会编一个位置出来。
3. 需要账号开启 Dota 2 的 **Expose Public Match Data**，否则拉不到公开对局。

### 1.3 高分段切片

```bash
python -m d2a --cli
> bracket            # 看当前切片 + 各档位对比
> bracket divine     # 切到 Divine（也可用 7 / "超凡" / immortal）
> bracket all        # 回全体平均
```

浏览器面板右上角有同样的下拉框；切片偏好会存进 `config.json` 的 `bracket` 字段。

为什么值得切：全体平均会**掩盖版本答案**。实测当前数据里
全体胜率前 10 与 Divine 段前 10 **只重合 5 个**，而且 Axe 这类英雄
在低分段偏强（Crusader 51.6%）、在高分段偏弱（Divine 48.6%）。

数据来自 OpenDota `heroStats` 自带的 `1_pick..8_pick`（Herald→Immortal），
**不需要额外的数据源或 token**。注意 8(Immortal) 在公开数据里恒为 0，
所以 UI 里最高给到 7(Divine)。

---

## 2. 设计文档

> 按架构设计规范输出：领域模型 → 模块划分 → 接口契约 → 数据流 → 风险评估。
> 但本仓库同时给了**可运行实现**，不是只有方案。

### 2.1 领域模型

```
实体 / 聚合根
  Draft（BP 聚合根）
    allies:  List<Pick>          我方已选
    enemies: List<Pick>          敌方已选
    bans:    List<Hero>          Ban 位
    my_lane: Optional[Lane]      我这一手要打的位置
    ally_lanes / enemy_lanes     位置归属
    行为: add / remove / set_lane / open_lanes / next_lane /
          guess_enemy_lanes / lane_matchups / to_dict / from_dict

  Pick（值对象）
    hero: Hero, side: ally|enemy, order: int, lane: Optional[Lane]

  Hero（实体，来自静态库）
    name, attr(str|agi|int|all), lanes[1-5], damage, tags
    traits: {lane, init, counter, teamfight, control, save,
             frontline, push, sustain, scaling, escape} ∈ [0,3]

  PlayerHeroStat（实体）
    hero, games, wins          → winrate（派生）

  Candidate（推荐结果值对象）
    hero, score(0-100), breakdown{5 因子}, penalties[], reasons[], risks[],
    position, games, winrate   → stars(1-5)（派生）

  Assistant（会话门面 / 应用服务根）
    book, engine, draft, weights
    行为: create / set_weights / pool_from_records / set_pool_simple

领域事件（工程上以方法调用体现，未引入事件总线——避免过度设计）
  Hero picked        → 从候选池剔除、重算阵容缺口、刷新 UI
  Lane changed       → 重算对位权重（同路敌人权重 ×5）
  Pool imported      → 重算熟练度因子的收缩值
  Data refreshed     → 重载 HeroBook（真实对位覆盖人工种子）
```

### 2.2 模块划分

```
d2a/
├─ data_loader.py   数据层：HeroBook 统一入口，别名/中文名/缩写/模糊匹配 + 出场率消歧
│                   职责：加载 heroes/aliases/matchups/synergies/meta，提供 matchup()/synergy()/
│                         winrate()/bracket_winrate()/candidates()/validate()
│                         PlayerHeroStat 支持 by_lane（分位置战绩）
├─ draft.py         BP 状态机：纯逻辑，无 IO，可单测；位置推断用全局最佳优先分配
├─ engine.py        推荐引擎：5 因子加权 + 逐候选权重归一化 + 分位置熟练度 + 档位切片 + 可解释输出
├─ voice.py         语音录入：管理 voice_listen.ps1 子进程，解析协议，派发事件
├─ config.py        本地配置：英雄池（简写 + 完整记录）、权重、档位、悬浮窗、语音
├─ steam_id.py      账号标识解析（纯字符串计算，无网络）
├─ steam_api.py     公开 API 客户端：磁盘缓存 + 限流 + 明确错误（含单场详情）
├─ cli.py           终端界面（标准库排版，兼容中文宽度）
├─ overlay.py       Windows 悬浮窗（tkinter + 可选 Win32 置顶/鼠标穿透/全局热键 + 语音按钮）
├─ webui/           浏览器面板（http.server + 单页 HTML/JS）
└─ __main__.py      统一入口（--web / --cli / --demo / --steam / --voice / --voice-probe）
data/               离线数据（heroes / aliases / matchups / synergies / meta / matchups_live）
tools/              数据生成、在线刷新、对位/版本重建、赛后同步、自检、语音脚本
tests/              单元测试（105 项）
```

依赖方向是单向的，没有环：

```
UI(cli/overlay/webui) ──> Assistant ──> RecommendationEngine ──> HeroBook
                              │                    │
                              └──> Draft <─────────┘
```

**为什么拆这么细**：核心价值（评分逻辑）必须能脱离 UI 单独测试与调参；
悬浮窗和 Web 面板共享同一个 `Assistant`，所以两边的推荐结果**保证一致**。

### 2.3 接口契约

```
HeroBook.load(data_dir) -> HeroBook
  前置: data/heroes.json 存在（tools/gen_hero_data.py 生成）
  后置: heroes 非空；_index 支持中英文与缩写
  异常: FileNotFoundError（缺数据，附带修复命令）

HeroBook.resolve(text) -> Hero
  后置: 命中英文名/中文名/缩写/前缀/编辑距离（cutoff 0.75）
  异常: HeroNotFound

Draft.add(hero, side, lane=None) -> Pick
  前置: side ∈ {ally, enemy}；hero 未被选走
  异常: ValueError（非法 side / 重复选人）

Draft.guess_enemy_lanes(book) -> {hero: lane}
  后置: 每个推断位置都在该英雄的合法 lanes 内（有测试保证）

RecommendationEngine.recommend(draft, top_n, lane=None, pool_only=True,
                                min_games=0) -> List[Candidate]
  前置: pool_only 时 pool 非空，否则返回空列表并提示
  后置: 结果按 score 降序；不含已被选/被 ban 的英雄；同输入必同输出

RecommendationEngine.explain(hero, draft, lane=None) -> Optional[Candidate]
  后置: 即使英雄不在池里也返回候选（用于「为什么该选它」），
        不在池里时熟练度记 0 并给出文字提示

Assistant.set_weights(**kwargs) -> None
  后置: engine.w 自动归一化，权重和恒为 1

接口层（本地 HTTP，仅监听 127.0.0.1）
  GET  /api/state                 -> {draft, pool(含 by_lane), data}
  GET  /api/recommend?top&pool_only&lane&strict_lane -> {candidates, draft, position}
  GET  /api/counter?hero=          -> {candidates}      克制位
  GET  /api/explain?hero=          -> {candidate, hero} 单英雄解释（含 contribution）
  GET  /api/search?q=&limit=       -> {candidates}      别名感知补全（剑圣/jugg/ls 都行）
  GET  /api/voice                  -> {supported, listening, events}
  POST /api/add | /api/remove | /api/lane | /api/reset | /api/pool | /api/weights
  POST /api/voice {mode,seconds}   -> 切换语音监听
  POST /api/bracket {bracket}      -> 切换天梯档位切片（"" = 全体）
```

### 2.4 数据流

```
① 录入：你敲英雄名
   UI → HeroBook.resolve（中文/缩写归一化） → Draft.add
   → 重算 team_needs（阵容缺口）→ 重算对位权重 → 重新排序

② 推荐：
   Draft + PlayerPool
     → proficiency()  贝叶斯收缩后的胜率 + 局数熟练加成
     → matchup()      敌方逐个克制值 × 同路权重 → 归一
     → team_fit()     阵容缺口填补度 + 位置适配度
     → meta_score()   当前版本胜率（无数据=中性 0.5）
     → synergy()      与队友的组合技
     → penalties()    缺先手 / 缺控 / 缺前排 / 魔法过载 / 熟练度低 / 位置排不下
   → Candidate(score, breakdown, reasons, risks) → UI 渲染

③ 刷新：
   tools/refresh_data.py → OpenDota 公开接口 → data/meta.json、matchups_live.json
   → HeroBook.load()（真实对位覆盖人工种子，其余保留兜底）→ 引擎立即生效
```

### 2.5 评分算法（可解释的核心）

```
总分 = 100 × ( w_p·熟练度 + w_m·对位 + w_t·阵容 + w_g·版本 + w_s·配合 ) × 风险系数
                                                       ↑ 权重可在 UI 里实时拖
```

| 因子 | 含义 | 归一化 |
|---|---|---|
| 熟练度 | 你会不会玩 | 贝叶斯收缩胜率（8 局先验拉回基准）× 0.82 + 局数熟练度 × 0.18；**有分位置数据时按位置算** |
| 对位 | 打不打得过 | 敌方逐个克制值按权重求和：同路 ×1.0、邻路 ×0.45、其余 ×0.2 |
| 阵容 | 补不补得上 | 0.6 × 缺口填补度 + 0.4 × 位置适配度；标签总和用递减收益 (1, .6, .35, .2, .12) |
| 版本 | 版本强不强 | 胜率 ±8pp 映射到 0~1；**切了档位就用该档位胜率** |
| 配合 | 和队友合计 | 最优组合 ×0.7 + 平均 ×0.3 |

**分位置熟练度**（``proficiency(hero, lane)``）：如果该英雄在你选定的位置上
有独立战绩，就用**那个位置**的胜率，并按 ``lane_prior_games`` 向该英雄的总体表现收缩
（避免「1 局 1 胜 = 100%」）。位置局数还会单独作为「机制熟练度」的分子——因为
「你在这个位置打了 20 局的斧王」和「你总共玩了 200 局的斧王」不是一回事。

实测：同一英雄 3 号位 20 局全胜、4 号位 20 局全败、总体 62% 时，
熟练度分别是 **0.91 / 0.09 / 0.78**。

**权重逐候选归一化**：当某个因子「无信息」时（没有敌人 → 对位无意义；该英雄没有任何
协同数据 → 配合无意义），它那份权重会按比例**让给其他因子**，而不是塞一个常数
0.5 进去。否则每加一个无效因子，就等于给所有候选同时加分、白白压缩区分度。

**每条理由带加权贡献**：`Candidate.contribution` 给出每个因子实际贡献了多少分，
理由按贡献降序排列（而不是代码书写顺序），低于 0.02 分的会被标注「低置信」。
各因子贡献之和恒等于加权总分，所以你看到的「为什么推它」是可对账的。

风险系数（基础 1.0，总扣减上限 30%，防止把候选一棒子打死）：

```
全队没先手 −10%       全队没硬控 −8%        缺前排且敌方物理多 −8%
敌方推进强而清线不足 −8%   伤害全魔法 −5%   没玩过该英雄 −12%   熟练度 <0.35 −6%
位置排不下 −12%      打不了目标位置 −15%
```

**位置适配在第一手就生效**。空 BP 时没有队友信息，此时 `team_fit` 直接等于位置适配
（而不是被一个常数稀释），并且 `lane` 未指定时会按**你的英雄池推断你常打几号位**
（辅助池 → 5 号位，核心池 → 1/2/3），而不是硬编码按 1 号位。

**关键设计取舍**

| 决策 | 放弃的好处 | 换来的好处 |
|---|---|---|
| 不做图像识别/不读游戏 | 无法自动感知 BP | 完全没有反作弊风险，且不存在识别错误 |
| 语音只做「输入法」，不做「读屏」 | 仍需你开口/动手 | 与手打走同一条解析管线，合规边界不变 |
| 熟练度用贝叶斯收缩 | 「3 局全胜」的英雄不会霸榜 | 推荐在统计上更稳，不会被噪声带偏 |
| 对位矩阵不做 top-k 截断 | 数据文件更大 | 冷门对手的克制关系不会丢，不偏袒热门英雄 |
| 标签值人工维护（0-3） | 不如数据驱动精确 | 可解释、可手改、离线可用；版本胜率/对位则走真实数据 |
| 惩罚项设总上限 | 极端烂阵容不会被强烈劝退 | 候选之间始终可比较，UI 不会出现「全军覆没」 |
| 纯函数、无随机 | 没有「探索性」推荐 | 可复现、可单测，同输入必同输出 |

---

## 3. 悬浮窗与合规说明（重要）

**技术行为边界** —— 悬浮窗在所有方面都等同于「一个置顶备忘录 + 计算器」：

- ❌ 不注入 DLL / 不 hook 游戏进程 / 不读写游戏内存
- ❌ 不抓包、不拦截网络、不调用游戏内部接口
- ❌ 不做屏幕 OCR / 图像识别 / 目标检测（**尤其不读游戏画面来判断 BP**）
- ❌ 不模拟键鼠（没有自动操作）
- ✅ 只读公开 HTTP 接口（OpenDota / Valve WebAPI）+ 本地静态数据
- ✅ 输入完全来自你自己的键盘或麦克风

**语音的边界**：语音只被当成一个**输入法**——它把你说的话变成文本，然后走与手打
完全相同的英雄名解析管线。麦克风只在按下 🎤 / `Ctrl+Alt+V` / 执行 `voice` 命令后
才打开；识别用 Windows 自带的 `System.Speech`，**离线、不上传音频、不联网**。
它**不读游戏画面、不感知 BP**，所以合规边界与手打录入没有任何区别。

因此它**不会触发 VAC**——VAC 关注的是对游戏进程与内存的干预。
但请注意：**任何第三方工具的使用后果由你自行承担**；如果某场比赛规则明确禁止外部辅助，请遵守赛事规则。

**显示模式**：Windows 全屏独占（Exclusive Fullscreen）下，系统不允许任何置顶窗口覆盖画面。
请在游戏设置里把显示模式改为 **无边框窗口（Borderless Windowed）**——这也是职业比赛与录制的常规设置，悬浮窗即可正常覆盖。

**快捷键**：`Ctrl+Alt+D` 显示/隐藏悬浮窗，`Ctrl+Alt+T` 切换鼠标穿透（穿透后只能用快捷键恢复），
`Ctrl+Alt+V` 开/关语音录入。

**隐私**：所有数据留在本机。`--steam <id>` 只查询该账号的**公开**比赛统计；语音不上传；
面板只监听 `127.0.0.1`，不对局域网/公网暴露。

---

## 4. 数据来源与刷新

| 数据 | 来源 | 是否离线可用 |
|---|---|---|
| 英雄属性、分路、能力标签 | 本仓库人工维护（`tools/gen_hero_data.py`） | ✅ 内置 |
| 英雄别名（527 条） | 本仓库人工维护（`data/aliases.json`），可自行增补 | ✅ 内置 |
| 克制 / 协同矩阵（种子） | 本仓库人工维护（`tools/seed_matchups.py`） | ✅ 内置 356 条 |
| 真实对位胜率 | OpenDota `heroes/{id}/matchups` | ⚠️ 刷新后写入本地 |
| 版本胜率 + **各天梯档位胜率** | OpenDota `heroStats`（`1_pick..8_pick`）+ `constants/patch` | ⚠️ 刷新后写入本地 |
| 你的英雄池（含分位置战绩） | 手工填写 / `tools/sync_results.py`（OpenDota 对局详情 `position_est`） | ✅ 手工填写完全离线 |

```bash
python tools/refresh_data.py --meta                 # 更新版本胜率 + 档位数据 + 版本号（快）
python tools/refresh_data.py --meta --matchups      # 同时拉真实对位（127 次请求，约 3 分钟）
python tools/refresh_data.py --heroes               # 校对英雄名单，发现新英雄
python tools/refresh_data.py --offline              # 只用缓存
python tools/rebuild_matchups.py                    # 从本地缓存重建对位矩阵（不联网）
python tools/rebuild_meta.py                        # 从本地缓存重建版本强度+档位（不联网）
python tools/sync_results.py --account-id <id>      # 赛后复盘：回写真实战绩（含位置）
```

**对位矩阵的构建口径**（`tools/rebuild_matchups.py`）：早期版本按 `abs(差值)` 只保留
每个英雄的前 14 条对位，并且用**绝对**样本阈值过滤。这两个口径都依赖英雄热度——
热门英雄留下几十条、冷门英雄（如 Elder Titan，最热的一个对位也只有 21 场）整表为空，
导致对位因子永久中性。现在改为：

- **相对阈值**：按该英雄自己的对位样本分布取分位（默认前 60%），另加一个很小的绝对地板；
- **样本收缩**：`adv = (wins/games − 0.5) × g/(g+k)`，小样本向 0 收缩而不是被删掉；
- **不做 top-k 截断**。

实测：对位边从 1015 条扩到 **4856 条，覆盖全部 127 个英雄（零对位英雄 0 个）**。

可选的限流额度提升（都不是必需）：`OPENDOTA_API_KEY`、`STEAM_API_KEY` 环境变量。

---

## 5. 工具链

```bash
python tools/gen_hero_data.py            # 重新生成 data/heroes.json
python tools/gen_hero_data.py --diff     # 只对比英雄名单增删
python tools/gen_matchup_data.py         # 编译人工克制种子（带英雄名校验）
python tools/refresh_data.py --meta      # 在线刷新（含真实版本号与档位数据）
python tools/rebuild_matchups.py         # 从本地缓存重建对位矩阵（不联网）
python tools/rebuild_meta.py             # 从本地缓存重建版本强度+档位（不联网）
python tools/sync_results.py --account-id <id> --probe   # 赛后复盘（先看后写）
python tools/voice_listen.ps1            # 语音识别后端（被 d2a/voice.py 调用）
python tools/selftest.py                 # 全链路自检（数据/引擎/Web API/语音/悬浮窗）
python tools/selftest.py --overlay       # 自检并打开悬浮窗
python -m d2a --voice-probe              # 单独测语音与麦克风
python -m unittest discover -s tests -v  # 105 项单元测试
```

自检输出示例（本机实测通过）：

```
== 数据层 ==   [OK] 英雄库加载 127 个英雄   [OK] 克制矩阵 4856 条
              [OK] 别名表加载 aliases.json(527)   [OK] ls -> Lifestealer
              [OK] 无零对位英雄   [OK] 版本号真实 patch=7.41
              [OK] 分档位数据 127 个英雄带 1~8 档位样本
== 引擎   ==   [OK] 英雄池推荐 Spirit Breaker(70.7)、Sand King(68.6)
              [OK] 加权贡献可对账   [OK] 分位置熟练度 3号位 0.91 vs 4号位 0.09
              [OK] 档位切片生效 全体 0.538 vs Divine 0.409
== Web API ==  [OK] GET /api/state  [OK] GET /api/search 别名检索
              [OK] GET /api/voice 状态  [OK] POST /api/bracket  切片 -> Divine
== 语音   ==   [OK] 语音环境 就绪（powershell.exe）  [OK] 语音监听管线
== 悬浮窗 ==   [OK] tkinter 可用   [OK] Win32 置顶/穿透 API
自检全部通过 ✅
```

---

## 6. 命令行速查（`python -m d2a --cli`）

```
a <英雄> [位置]      我方选人       e <英雄> [位置]   敌方选人
b <英雄>             记录 ban       rm <英雄>         撤销
lane <1-5>           我打几号位     pool add/rm       英雄池增删
rec [n]              只在英雄池推荐 all [n]           全英雄视角
why <英雄>           解释单个英雄   counter [英雄]    找克制位
voice [秒数]         语音录入我方   voice 8 dictation 自由听写模式
bracket [档位]       切换版本切片   bracket divine    切到 Divine 段
w prof 0.4           调权重         data              看数据来源
refresh [--matchups] 在线刷新       demo / reset / q  仿真/清空/退出
```

---

## 7. 诚实的局限（请先读这一段）

1. **分值不是胜率**。`0-100` 是同一局内候选之间的**相对排序**，不是「选它就有 X% 胜率」。
2. **能力标签是我手工填的**（0-3 级），它有主观成分。觉得某个英雄定级不对，直接改
   `tools/gen_hero_data.py` 里的 traits，跑一次 `gen_hero_data.py` 即可，不需要改代码。
3. **对位数据是混合来源**：真实数据优先（按该英雄自己的样本分位取阈值 + 小样本收缩），
   缺失时才用人工种子。低出场率英雄的对位样本少，置信度天然更低（已由收缩体现）。
4. **不感知真实 BP**。你需要手动录入（手打或语音）；这是为合规付出的代价。
   顺带一提，这也是它比「自动读屏工具」更抗封的原因。
   关于为什么不做读屏/G.SI 自动感知，见 `dota2-assistant-review.md` §2 的实测结论。
5. **位置推断是启发式的**：用全局最佳优先分配（按英雄标签 + 先手顺序 + 阵容结构打分），
   比早先「第 i 手固定猜第几个位置」准得多，但仍然只是推断；UI 里允许手动改位置。
6. **版本强度会过期**。数据文件里记了真实版本号（面板上会显示，如 7.41），
   大版本更新后跑一次 `refresh_data.py --meta` 即可。
7. **语音依赖 Windows 与中文语音包**。只在 Windows 可用；未安装语音包时会明确报错。
   识别受限英雄名语法，**故意不支持「随便说一句话」**（那需要 dictation 模式，准确率会掉）。
8. **高分段切片最高只到 Divine**。OpenDota 公开数据里 Immortal(8) 档位恒为 0，
   所以 UI 只给到 7。想看真正的顶分局/职业切片需要接 STRATZ（见第 8 节）。
9. **分位置熟练度依赖对局被解析**。旧对局常没有 `position_est`，那些局只计总体战绩；
   而且位置来自单场详情，每场一次 API 请求，所以同步默认只回溯 20 局。
10. **悬浮窗暂不支持从剪贴板导入 BP**——保持「零外部交互」是有意为之。

---

## 8. 后续可以怎么扩展

- **顶分局/职业切片**：当前档位切片用的是 OpenDota 的 `1..8_pick`（最高 Divine）。
  要拿真正的 Immortal/职业数据可以接 STRATZ GraphQL（需在 `stratz.com/api` 用 Steam 登录领 token）。
  `HeroBook.meta` 已经是 `{英雄: {...}}` 结构，换数据源不动引擎接口。
- **分位置的期望值基准**：现在分位置熟练度只用你自己的战绩。更进一步可以按「该位置
  该英雄的平均经济/补刀」对比你的表现，判断你是「赢了但打得一般」还是「打得很好但队友崩了」。
- **BP 阶段化权重**：第一手偏摇摆与强度，最后一手偏克制（引擎已留 `flexibility_bonus` 钩子）。
- **队友视角**：同时给出「建议队友选什么」，而不只是我这一手。
- **协同数据扩充**：现在只有 35 个英雄有协同边；OpenDota 没有现成接口，需要自己从对局算。
- **目标用户直接看**：[用户操作手册](docs/用户操作手册.md) 已覆盖权重预设（绝活型 / 稳健型 / 阵容型）。

---

*本工具与 Valve 无关联。Dota 2 及相关素材版权归 Valve 所有；公开数据来自 OpenDota 社区接口，
使用时请遵守其接口条款与限流要求。*
