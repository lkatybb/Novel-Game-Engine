# PRD：小说互动游戏引擎

## 1. 产品定位

将小说文本转化为可交互的文字冒险游戏，玩家通过选项选择和自由输入推动剧情发展。核心展示价值在于 **RAG分层记忆 + 多Agent协作** 的后端架构。

## 2. 技术栈

| 层级 | 选型 | 来源 |
|------|------|------|
| 后端框架 | FastAPI + Uvicorn | 复用AI-Reader-V2 |
| 向量数据库 | ChromaDB | 复用AI-Reader-V2 |
| Embedding | BAAI/bge-base-zh-v1.5（本地） | 复用AI-Reader-V2 |
| LLM | DeepSeek V3 (`deepseek-chat`) | OpenAI兼容，中文强，成本低 |
| Agent编排 | LangGraph | 新开发 |
| 前端 | 单页HTML + 原生JS + CSS | 参考Story-to-Game启动器 |
| 可视化 | D3.js | 新开发（关系力导图） |
| 流式传输 | FastAPI StreamingResponse + SSE | 新开发 |

## 3. 功能点清单

### 3.1 核心功能（必须跑通）

| 编号 | 功能 | 描述 | 复用来源 | 工作量 |
|------|------|------|---------|--------|
| F1 | 小说解析入库 | 上传TXT/MD小说，按章节切片，Embedding入ChromaDB | AI-Reader-V2 embedding_service | 小 |
| F2 | RAG分层记忆 | 短期(List 5轮) + 长期(向量检索) + 全局状态(Dict) | 新开发，参考AI-Reader-V2检索逻辑 | 中 |
| F3 | DM Agent | 接收玩家动作，结合记忆检索结果，推演剧情 | 新开发 | 中 |
| F4 | 多Agent协作 | Router→NPC→Rules三Agent分工 | LangGraph新开发 | 中 |
| F5 | 流式输出 | 后端SSE流式返回剧情文本 | 新开发 | 小 |
| F6 | 游戏前端 | 选项按钮 + 自由输入 + 场景描述 + 状态面板 | 参考Story-to-Game启动器 | 中 |
| F7 | 人物关系力导图 | D3.js可视化人物关系 | LLM提取关系JSON（参考AI-Reader-V2聚合逻辑） | 小 |

### 3.2 增强功能（时间允许时做）

| 编号 | 功能 | 描述 | 依赖 |
|------|------|------|------|
| F8 | 分支剧情JSON | 小说→分支剧情JSON（用story-to-game.skill） | F1跑通后 |
| F9 | 成就/存档系统 | 玩家进度存档、成就解锁 | 复用Story-to-Game JSON规范 |
| F10 | 场景氛围特效 | 雨声/闪光/心跳等CSS动画 | 复用Story-to-Game ambient规范 |

### 3.3 不做

- 多人在线 / 账号系统
- MySQL持久化（所有状态在内存+ChromaDB）
- React前端（单页HTML足够）
- 百万字长篇支持（先跑通3000字短篇）

## 4. 核心数据流

```
玩家操作（选项/自由输入）
    │
    ▼
FastAPI /api/action (SSE)
    │
    ▼
LangGraph StateGraph
    │
    ├─► Router Agent（判断动作类型：对话/动作/脱轨）
    │
    ├─► Rules Agent（判定成功率，可选D20骰子）
    │
    ├─► NPC Agent（加载NPC人设，生成台词）
    │
    ├─► DM Agent（推演剧情）
    │       │
    │       ├─ 短期记忆（最近5轮对话 List）
    │       ├─ 长期记忆（ChromaDB向量检索）
    │       └─ 全局状态（Dict：位置/物品/flag）
    │
    ▼
流式返回JSON（新剧情 + NPC台词 + 状态变化）
    │
    ▼
前端打字机渲染 + 状态面板更新
```

## 5. 复用策略（最快跑通路径）

### 第一步：搬后端（Day 1上午）

从AI-Reader-V2中提取以下文件到新项目：

```
app/
├── config.py          ← 复制 AI-Reader-V2/backend/src/infra/config.py（精简）
├── embedding_service.py ← 复制 AI-Reader-V2/backend/src/services/embedding_service.py
├── chapter_parser.py  ← 参考 chapter_fact_extractor.py 的Prompt设计
├── memory/
│   ├── short_term.py  ← 新写：List维护5轮对话
│   ├── long_term.py   ← 新写：调用embedding_service做ChromaDB检索
│   └── global_state.py ← 新写：Dict维护玩家状态
├── agents/
│   ├── router.py      ← 新写：LangGraph Router节点
│   ├── npc.py         ← 新写：NPC Agent
│   └── rules.py       ← 新写：Rules Agent
├── dm.py              ← 新写：DM Agent主逻辑
├── main.py            ← 新写：FastAPI入口 + SSE
└── requirements.txt   ← 从AI-Reader-V2 pyproject.toml精简
```

### 第二步：搬前端（Day 4）

从Story-to-Game中提取：
- 参考启动器HTML的CSS主题和特效系统
- 参考JSON剧本规范设计前端状态管理
- 但不用其固定JSON剧本模式，改为动态SSE接收

### 第三步：搬可视化（Day 5上午）

- 用LLM Prompt从小说中提取人物关系JSON（nodes+links）
- 用D3.js渲染力导图（不直接复用AI-Reader-V2的visualization_service，它深度耦合SQLite管线）

## 6. API设计

### 6.1 小说上传
```
POST /api/novel/upload
Request: multipart/form-data (file)
Response: { novel_id, chapters, entities_count }
```

### 6.2 玩家动作（SSE流式）
```
POST /api/action
Request: { novel_id, action: "go_left" | "talk:手电筒" | "free:任意文本" }
Response: SSE stream
  data: { type: "scene", text: "你走进了黑暗的走廊..." }
  data: { type: "npc", speaker: "守夜人", text: "你谁？" }
  data: { type: "state", key: "location", value: "走廊" }
  data: { type: "choices", options: ["往前走", "回头", "用手机照明"] }
  data: { type: "done" }
```

### 6.3 人物关系图
```
GET /api/novel/{novel_id}/graph
Response: { nodes: [...], links: [...] }
```
（LLM提取关系JSON，不直接复用AI-Reader-V2的graph路由）

## 7. 小说素材

直接使用AI-Reader-V2自带的样本：
- `backend/sample-novels/xiyouji.txt` — 西游记（经典，人物关系丰富）
- `backend/sample-novels/三国演义-样本.txt` — 三国演义

建议先选一篇3000字左右的段落做测试，跑通后再全量。

## 8. 风险点

| 风险 | 影响 | 缓解方案 |
|------|------|---------|
| DeepSeek JSON输出不稳定 | Agent解析失败 | 用response_format=json_object，加retry |
| ChromaDB首次Embedding慢 | 首次加载等待长 | 加loading提示，异步处理 |
| LangGraph多Agent延迟 | 端到端响应慢 | Router+Rules合并为一个节点，减少到3节点 |
| 小说切片质量 | RAG检索不准 | 用jieba分词+段落级切片 |
| Agent间数据传递设计 | 状态混乱 | 提前定义AgentState结构体 |
