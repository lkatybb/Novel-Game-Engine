# 小说互动游戏引擎

> 把一本小说变成可玩的文字冒险游戏。玩家用选项或自由输入推动剧情，AI 在**原著框架内**实时推演。
>
> 技术亮点在后端：**三层 RAG 记忆** + **LangGraph 多 Agent 编排** —— 解决"AI 玩到后面就忘了前面设定"这个工程痛点。

**English**: An engine that turns a novel into a playable text adventure, powered by a three-layer RAG memory pipeline and a LangGraph multi-agent orchestrator. FastAPI + SSE backend, dependency-free vanilla-JS front end.

---

## 这是什么

上传一本 TXT / MD 小说 → 按段落切片、向量化入库 → 生成开场场景 → 玩家输入动作 → AI 流式推演剧情、给出选项、更新角色状态。

关键区别：不是"让 LLM 随便编故事"，而是让 LLM 在**原著切片** + **已发生的关键事件** + **玩家当前状态**三重约束下推演。

---

## 核心设计

### 1. 三层 RAG 记忆

| 层 | 存储 | 作用 | 实现 |
|---|---|---|---|
| 短期 | 内存 `deque(maxlen=5)` | 最近 5 轮对话，保证走位连贯 | [`memory/short_term.py`](novel_game/memory/short_term.py) |
| 长期 | ChromaDB + `bge-base-zh-v1.5` | 按玩家动作**语义检索**最相关的原著段落 | [`memory/long_term.py`](novel_game/memory/long_term.py) |
| 全局 | 内存 `GameState` | 位置 / 物品 / flag / 状态值 / 已触发事件 | [`memory/global_state.py`](novel_game/memory/global_state.py) |

- 短期记忆用 `deque` 而非 list，溢出自动淘汰，不需要手动裁剪。
- 长期记忆用**余弦距离**检索 top-5（`RETRIEVAL_TOP_K`），结果拼成 `[原著片段N]` 注入 Prompt。
- 检索失败时返回空列表并记日志，不阻断本轮推演（[`long_term.py`](novel_game/memory/long_term.py) 的 `retrieve`）。
- **会话脉络**（[`memory/session_memory.py`](novel_game/memory/session_memory.py)）：短期记忆窗口只有 5 轮，再往前的历史会整段丢失，AI 到后期就忘了第 1 章做过什么。每轮结束后把该轮的「玩家动作 + 剧情摘录 + 本轮触发的关键事件」压成一条关键节点落盘到 `data/session_memory/`，组装 Prompt 时补在 `[短期记忆]` 之后，**只补已滑出窗口的轮次**，同一轮不会重复出现。有界性：滑出窗口的轮次最多注入 `EARLY_NODE_LIMIT`（10）条，更早的只保留触发过关键事件的节点 —— 普通对话随剧情淡出，主线关键节点永久保留。

### 2. 关键事件硬锁

这是项目里最"较真"的一环 —— 防止 LLM 提前剧透、或编造原著里没有的剧情。

小说入库时用 LLM 抽取**关键事件清单**（每条带 `order` 序号）。之后 DM 返回的 `triggered_events` 要过两道闸门才能写进状态：

1. **白名单**：事件名必须在原著预设清单内，LLM 编的名字直接丢弃
2. **顺序闸门**：只能触发 `order <= 当前最大 order + 1` 的事件，不允许跳序

取不到事件清单时放行，避免误杀。实现在 [`global_state.py`](novel_game/memory/global_state.py) 的 `_accept_triggered`。

**对外只暴露「已发生」和「下一个」**：`state` 帧下发 `triggered`（已触发事件名数组）、`next_event`（仅 `event_name` + `order`，无未触发事件时为 `null`）、`total`，前端据此渲染「✓ 已触发」+「灰·下一个」两类；未触发事件的全清单与 `trigger_condition` **一律不下发**。DM Prompt 也遵守同一口径，只注入**当前待触发的这一个**事件（`memory/global_state.py` 的 `get_next_event`）—— 一次性把未触发清单塞进 Prompt，等于把后文剧情提前交给模型。

### 3. LangGraph 多 Agent 编排

```
START ──► router ──┬── dialog 且指名 NPC ──► npc ──► dm ──► END
                   └── 其它 ───────────────────────► dm ──► END
```

| 节点 | 职责 | 代码 |
|---|---|---|
| `router` | 判断动作类型（`dialog` / `action` / `off_rail`）+ 识别对话目标 | [`agents/router.py`](novel_game/agents/router.py) |
| `npc` | 按人设档案生成 NPC 台词 | [`agents/npc.py`](novel_game/agents/npc.py) |
| `dm` | 主推演：结合三层记忆产出剧情正文 + 选项 + 状态变更 | [`agents/dm.py`](novel_game/agents/dm.py) |

两个设计取舍：

- **没有独立的 Rules 节点。** 初版架构里 Router 和 Rules 是两个节点，实测多一次 LLM 往返延迟明显，合并进 Router 的判定结果（见 [`PRD.md`](PRD.md) 第 8 节风险表）。
- **条件边省一次调用。** 只有"对话类且指名了 NPC"才绕道 `npc` 节点，其余动作直连 `dm`。

**流式与图不冲突**：`dm` 节点内部用 `get_stream_writer()` 把 LLM 逐 token 的产出实时推出图外，所以走 StateGraph 不会牺牲打字机效果。CLI 和 Web 共用同一张图，行为一致（公开入口 `stream_graph` / `run_graph`）。

### 4. SSE 事件协议

`POST /api/game/action` 返回 `text/event-stream`，事件类型：

| `type` | 载荷 | 用途 |
|---|---|---|
| `stage` | `text` | 进度提示（"正在理解你的行动…"） |
| `scene_change` | `name`, `desc` | 场景切换弹窗（同名场景不弹） |
| `scene` | `text` | 剧情正文，**逐块增量**送达 |
| `npc` | `speaker`, `text` | NPC 台词 |
| `choices` | `options` | 本轮选项 |
| `state` | `state` | 完整角色状态 + `_timeline` 时间线 |
| `error` | `message` | 异常信息 |
| `done` | — | 本轮结束 |

顺序被刻意约束：**场景弹窗 → NPC 台词 → 正文**。NPC 台词在 `graph` 层就先产出，但 SSE 层会暂存它，等场景弹窗发完再补发。

`choices` 为空数组时**如实下发**，由前端自由输入框兜住 —— 不伪造固定选项，避免把 DM 返回异常掩盖成"正常一轮"。

---

### 5. 角色私聊（只读旁路）

菜单里的「私聊角色」让玩家单独找某个角色问话，**不影响剧情推进**：

- **独立 REST 端点** `POST /api/game/chat`，不塞进 `/action` 的 SSE 流（SSE 事件类型一个都不改）。
- **只读**：不写 `GameState`、不写短期记忆、不触发关键事件、不落快照。私聊前后存档字节不变。
- **防剧透复用同一套 Mask 口径**：只给「三个自由文本容器（`player_location` / `flags` / `inventory`）+ 已触发事件 + 主角近 5 轮短期记忆」，未触发事件清单、`trigger_condition`、甚至 `next_event` 都**不注入** —— 角色不承担推进主线的责任，它没有理由知道后文。实现在 [`agents/npc.py`](novel_game/agents/npc.py) 的 `build_chat_prompt`。
- **对话历史不落盘**：由前端持有并随请求回传（最多 6 条 / 单条 ≤ 200 字），服务端不做会话存储，因此不产生新的孤儿数据、也不需要额外的清理链路。
- 角色没有人设档案时直接返回 404，**不硬编人设**（不兜底）。

---

### 6. 好感度与理智度

两项数值由 DM（不是单独的裁判 Agent）在推演剧情时顺手裁决，随每回合与 `story` 一起下发：

- **复用既有字段**：`GameState.val`（默认 50）与 `GameState.hp`（默认 100）本就是 state 模型里的字段，此前从未被填充；本次只把它们**定义为好感度 / 理智度**并在 Prompt 里激活，**没有新增任何 state 字段或 SSE 事件**（接口零变更）。
- **按人物特质裁决**：`DM_SYSTEM` 要求 DM 依据在场人物的性格、立场、忌讳与看重之处（来自原著片段与已有剧情）判断观感变化，并在 `state_changes` 中给出 `val` / `hp`：普通互动 ±1~3、关键抉择 ±4~8、重大转折 ±10，无变化填 0（防抖，不允许凭空涨落）。
- **口径**：好感度是**全局关系值**（在场人物对玩家的整体观感），理智度是主角自身的精神状态；两者都在 [`memory/global_state.py`](novel_game/memory/global_state.py) 的 `update_state` 里钳制在 0~100。
- **注入与展示**：`format_state` 以「好感度 / 理智度」注入 DM 与 NPC，NPC 台词会据此调整语气；顶栏 HUD 用两条窄条实时展示（好感＝青绿、理智＝赭黄）。
- **回归闸门**：`test_contract.py` 的 A12 离线断言锁死「Prompt 声明 → 数值钳制 → 注入标签」三层，防止 Prompt 与 HUD 之间静默脱钩成不会动的死表。

---

## 项目结构

```
.
├── AGENTS.md                   # 项目开发铁律（AI 协作约定）
├── PRD.md                      # 产品需求
├── ARCHITECTURE.md             # 架构方案
├── TECH_DEBT.md                 # 技术债登记（为什么先不修 + 修的时候要付什么）
├── README.md
├── trae/rules/AGENTS.md        # 铁律原文（Trae 版）
├── .github/agents/             # 5 个自定义 Agent 定义（规划/评审/实现/需求/故障）
└── novel_game/
    ├── config.py               # 配置中心：LLM / ChromaDB / 切片参数 / 共享单例
    ├── models.py               # GameState / PlayerAction / AgentState
    ├── utils.py                # 编码自适应读取（UTF-8 → GBK → GB2312 → UTF-16）
    ├── play.py                 # 命令行版（复用同一张 LangGraph 图）
    ├── requirements.txt
    ├── memory/                 # RAG 三层记忆
    │   ├── short_term.py
    │   ├── long_term.py
    │   ├── global_state.py     # 含关键事件硬锁
    │   ├── session_memory.py   # 会话脉络：早期关键节点缓存（派生物）
    │   └── session_store.py    # 存档：原子写入 + 会话快照
    ├── agents/                 # LangGraph 多 Agent
    │   ├── graph.py            # StateGraph 定义 + 条件边
    │   ├── router.py
    │   ├── npc.py
    │   └── dm.py               # 真 token 流式 + 增量 JSON 解析
    ├── pipeline/               # 小说处理管线
    │   ├── novel_parser.py     # 切片 + Embedding 入库
    │   ├── character_extractor.py  # LLM 抽取人物关系 / NPC 人设 / 关键事件
    │   └── prompts.py          # 所有 Prompt 集中管理
    ├── api/                    # FastAPI 接口层
    │   ├── main.py             # 入口 + 静态文件挂载
    │   ├── route_novel.py      # 上传 / 书架
    │   ├── route_game.py       # 开局 / 恢复 / 动作（SSE）
    │   └── route_graph.py      # 人物关系图数据
    ├── static/                 # 前端：书架 + 游戏界面（原生 JS，无框架）
    ├── graphic/relation.html   # D3.js 关系力导图（单文件）
    └── data/                   # 运行时数据（不入库，见下）
```

分层依赖是单向的：`api` → `agents` → `memory` / `pipeline` → `models` / `config`。

---

## 快速开始

### 环境要求

- Python 3.10+
- 能访问 `https://api.deepseek.com`
- 首次运行会下载中文 Embedding 模型 `BAAI/bge-base-zh-v1.5`（约 400 MB），代码里已预置 `hf-mirror.com` 国内镜像加速

### 1. 安装依赖

```bash
cd novel_game
pip install -r requirements.txt
```

安装慢的话加镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 2. 配置 API Key

在 `novel_game/` 下新建 `.env`：

```ini
LLM_API_KEY=sk-你的DeepSeek密钥
```

其余配置都有安全默认值，不填也能跑。

### 3. 启动

```bash
cd novel_game
uvicorn api.main:app --reload --port 8000
```

浏览器打开 <http://localhost:8000> → 上传小说（或从书架选）→ 开玩。

想先快速验证链路，仓库自带一本测试小说 [`novel_game/data/novels/西游记-样本.txt`](novel_game/data/novels/西游记-样本.txt)，上传它即可。

### 4. 命令行模式

```bash
cd novel_game
python play.py
```

CLI 走的是同一张 LangGraph 图，行为和网页端一致。

---

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/novel/upload` | 上传小说（`multipart/form-data`，≤ 10 MB，仅 `.txt` / `.md`） |
| `GET` | `/api/novel/list` | 书架列表 |
| `GET` | `/api/novel/{novel_id}/graph` | 人物关系图（`nodes` + `links`） |
| `GET` | `/api/novel/{novel_id}/character/{name}` | 单个角色档案（性格 / 目标 / 说话风格 / 关键事件 / 原著片段），关系图点击节点时用 |
| `POST` | `/api/game/start` | 开局，入参 `{novel_id}`，返回 `session_id` + 开场场景 |
| `POST` | `/api/game/action` | **玩家动作，SSE 流式**，入参 `{session_id, novel_id, action}` |
| `POST` | `/api/game/resume` | 从存档恢复会话，入参 `{session_id}` |
| `POST` | `/api/game/chat` | **角色私聊（只读旁路）**，入参 `{session_id, npc_name, message, history?}`，返回 `{npc_name, reply}` |
| `GET` | `/api/game/sessions/{novel_id}` | 某本小说下的所有存档 |
| `DELETE` | `/api/game/sessions/{session_id}` | 删除单个存档（清内存状态 + 清快照 + 清会话脉络 + 从书架该条 `sessions` 摘除） |
| `DELETE` | `/api/novel/{novel_id}` | 删除小说（正文 / 封面 / ChromaDB 集合 / 人物缓存 / 其下所有存档 / 书架条目六处一起清） |

交互式文档：<http://localhost:8000/docs>

角色状态每次动作后**自动存档**（`session_store.save_session`，先写临时文件再 `os.replace` 原子替换，避免中途崩溃写坏 JSON）。

---

## 配置项

全部通过环境变量 / `.env` 覆盖，定义在 [`config.py`](novel_game/config.py)：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_API_KEY` | *(空)* | DeepSeek API Key，**必填** |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容端点 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `LLM_MAX_TOKENS` | `16384` | 单次生成上限 |
| `EMBEDDING_MODEL` | `BAAI/bge-base-zh-v1.5` | 中文向量模型 |
| `HF_ENDPOINT` | `https://hf-mirror.com` | HuggingFace 镜像（`setdefault`，可覆盖） |

代码内常量：`RETRIEVAL_TOP_K=5`、`SHORT_TERM_LIMIT=5`、`CHUNK_SIZE=800`、`CHUNK_OVERLAP=100`。

LLM 客户端是**进程级单例**，带 `timeout=60s` 和 `max_retries=2`（SDK 内置对 429 / 5xx 指数退避），避免 API 挂起导致请求永久阻塞。

---

## 测试

仓库自带验收脚本，分两类。**全部都要在 `novel_game/` 目录下执行**，且依赖测试小说 [`data/novels/西游记-样本.txt`](novel_game/data/novels/西游记-样本.txt)（已随仓库提供）。

#### 一、直接调 Python（不用启服务）

需要配好 `LLM_API_KEY`，首次运行会下载 Embedding 模型。特点是绕过 HTTP，直接验证 agent 与记忆层：

```bash
python test_lock.py    # 硬锁：玩家试图跳过原著关键事件，看白名单+顺序闸门是否拦住
python test_lock2.py
python test_lock3.py
python test_lock4.py
python test_lock5.py   # 检查 DM 返回的 story/flag 里是否泄漏关键事件名
```

#### 二、打 HTTP 接口（需先启服务）

⚠️ 注意脚本里写死了端口，且**两批脚本端口不一致**：

```bash
# ——— 端口 8000 ———
uvicorn api.main:app --port 8000
python test_api.py        # 主链路：上传 / 开局 / 动作 SSE
python test_upload.py     # 上传接口边界（大小 / 类型 / 空文件）
python diag.py

# ——— 端口 8888 ———
uvicorn api.main:app --port 8888
python test_tech_debt.py  # 全链路验收
python test_shelf.py      # 书架 + 自动存档 + 恢复
python test_timeline.py   # 关键事件时间线
python _diag_sse.py
```

`_tmp_*.py` / `_tmp_*.js` / `diag2.py` 是开发期留下的临时诊断脚本，保留在此作为调试参考，不属于正式用例。

---

## 当前状态

| 编号 | 功能 | 状态 |
|---|---|---|
| F1 | 小说解析入库 | ✅ 完成 |
| F2 | RAG 三层记忆 | ✅ 完成 |
| F3 | DM Agent | ✅ 完成（真 token 流式） |
| F4 | 多 Agent 协作 | ✅ 完成（LangGraph 条件路由） |
| F5 | SSE 流式输出 | ✅ 完成 |
| F6 | 游戏前端 | ✅ 完成（书架 / 游戏 / 菜单 / 场景弹窗） |
| F7 | 人物关系力导图 | ✅ 完成（D3.js） |
| F9 | 存档系统 | 🟡 自动存档 / 恢复 / 删存档 / 删小说已做（`DELETE` 端点），成就系统未做 |
| F8 | 分支剧情 JSON | ⬜ 未开始 |
| F10 | 场景氛围特效 | ⬜ 未开始（`style.css` 现有 6 个 `@keyframes` 全是通用 UI 动效 —— 光标 `blink`、翻页 `pagePop`/`pageSink`、`tapBreathe`、卡片 `rise`、状态 `pulse`；**没有**情景驱动的雨滴 / 抖动 / 闪光，`app.js` 中零引用） |
| F11 | 角色私聊（只读旁路） | ✅ 完成（独立 `POST /api/game/chat`，不写状态、不落盘、复用 A11 Mask 防剧透口径） |
| F12 | 角色百科面板 | ✅ 完成（关系图点击节点展开：性格 / 目标 / 说话风格 / 隐秘 + 关键事件 + 原著片段，`GET /api/novel/{id}/character/{name}`） |
| F13 | 好感度 / 理智度 | ✅ 完成（激活 `GameState.val` / `hp`：DM 按人物特质裁决每回合 ±10 以内的增减，顶栏 HUD 实时展示，`[全局状态]` 以新口径注入 DM / NPC） |

### 明确不做

- 多人在线 / 账号系统
- MySQL 持久化（所有状态在内存 + ChromaDB + JSON 快照）
- React 前端（单页原生 JS 足够）
- 百万字长篇支持（先跑通 3000 字级短篇）

---

## 关于仓库里的数据文件

`novel_game/data/` 下的**运行时数据不入库**，因为它们由用户上传的小说派生而来：

```
data/novels/*              # 上传的小说正文与封面（第三方版权），仅保留测试样本
data/sessions/*.json       # 会话快照，含 AI 生成的剧情正文
data/character_cache/*     # 从小说原文提取的人物关系 / 关键事件
data/session_memory/*.json # 会话脉络：早期关键节点缓存（随存档派生，可重建）
data/bookshelf.json        # 书架索引
```

**克隆后可直接启动**：`config.py` 会在导入时自动 `mkdir` 重建这些目录，`session_store` 读到缺失的 `bookshelf.json` 会返回空列表。已实测验证：干净克隆 → 依赖安装 → 服务启动 → 上传小说全流程正常。

**孤儿数据注记**：`bookshelf.json` 是唯一索引，磁盘上的文件未必都被它引用 —— 早期手工测试（以及 `DELETE` 端点上线前的删除方式）会留下孤儿文件。实测当前工作区：`data/novels/` 73 个文件里 16 个不在书架条目内（71 个 `.txt` + 2 个封面 `.png`），`data/sessions/` 128 个存档里 56 个不在任何书架条目的 `sessions` 中，`data/character_cache/` 55 个全部有归属。它们**不影响启动与游玩**，只是占空间。要清理请走网页上的「删除小说 / 删除存档」按钮（端点会把 ChromaDB 集合一并删掉），**不要手工删文件**。

要换成自己的小说，只需在网页上上传，或把 TXT 丢进 `novel_game/data/novels/` 后调用 `ingest()`。

---

## 相关文档

- [`PRD.md`](PRD.md) — 产品定位、功能清单、数据流、风险点
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — 技术选型、模块划分、核心数据结构
- [`novel_game/DEV_GUIDE.md`](novel_game/DEV_GUIDE.md) — 分阶段开发执行手册（含每步验收命令）
- [`AGENTS.md`](AGENTS.md) — 项目开发铁律
