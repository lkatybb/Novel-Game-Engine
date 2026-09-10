# 架构方案：小说互动游戏引擎

## 一、技术栈（最终确定）

| 层级       | 选型                      | 版本/型号             | 说明                          |
| ---------- | ------------------------- | --------------------- | ----------------------------- |
| 后端框架   | FastAPI + Uvicorn         | 最新稳定版            | 异步支持好，原生SSE           |
| LLM        | DeepSeek V3               | `deepseek-chat`       | OpenAI兼容，中文强，成本低    |
| Embedding  | BAAI/bge-base-zh-v1.5     | sentence-transformers | 本地运行，中文向量质量好      |
| 向量数据库 | ChromaDB                  | 最新稳定版            | 轻量，纯Python，无独立服务    |
| Agent编排  | LangGraph                 | 最新稳定版            | StateGraph，多节点协作        |
| 前端       | 单页HTML + 原生JS + CSS   | 无框架                | 游戏式界面，参考Story-to-Game |
| 可视化     | D3.js v7                  | CDN引入               | 力导图，单文件HTML            |
| 流式传输   | FastAPI StreamingResponse | SSE                   | 前端EventSource接收           |

**不用的东西**：React、Go、MySQL、Streamlit、Ollama（开发阶段先用DeepSeek，简历写"支持双模式"）

## 二、项目结构

```
e:\小说\novel_game\
├── config.py                    # 配置中心（DeepSeek/ChromaDB路径等）
├── models.py                     # 数据结构定义（GameState/AgentState）
├── requirements.txt              # Python依赖
├── .env                          # 环境变量（API Key）
│
├── memory/                       # RAG分层记忆
│   ├── __init__.py
│   ├── short_term.py             # 短期记忆：List维护最近5轮对话
│   ├── long_term.py              # 长期记忆：ChromaDB向量检索
│   ├── global_state.py           # 全局状态：Dict维护位置/物品/flag
│   └── session_store.py          # 存档：原子写入 + 会话快照
│
├── agents/                       # LangGraph多Agent
│   ├── __init__.py
│   ├── graph.py                  # StateGraph定义 + 节点连接
│   ├── router.py                 # 路由Agent：判断动作类型
│   ├── npc.py                    # NPC Agent：加载人设生成台词
│   └── dm.py                     # DM Agent：推演剧情主逻辑
│
├── pipeline/                     # 小说处理管线
│   ├── __init__.py
│   ├── novel_parser.py           # 小说→章节切片→ChromaDB入库
│   ├── character_extractor.py    # LLM提取人物关系JSON
│   └── prompts.py                # 所有Prompt模板集中管理
│
├── api/                          # FastAPI接口层
│   ├── __init__.py
│   ├── main.py                   # FastAPI入口 + 静态文件挂载
│   ├── route_novel.py            # POST /api/novel/upload
│   ├── route_game.py             # POST /api/action (SSE)
│   └── route_graph.py            # GET /api/novel/{id}/graph
│
├── static/                       # 前端
│   ├── index.html                # 游戏主界面
│   ├── style.css                 # 暗色主题
│   └── app.js                    # SSE客户端 + 渲染逻辑
│
├── graphic/                      # 可视化
│   └── relation.html             # 人物关系力导图（D3.js单文件）
│
└── data/                         # 数据
    └── novels/                   # 测试小说文件
```

## 三、核心数据结构

### 3.1 游戏状态（贯穿所有Agent）

```python
# models.py

class GameState(BaseModel):
    """全局游戏状态，所有Agent共享"""
    novel_id: str = ""
    player_location: str = "起始场景"
    inventory: list[str] = []
    flags: dict[str, bool] = {}          # 事件标记（如"已拿钥匙"）
    val: int = 50                        # 主状态值 0-100（如理性值）
    hp: int = 100                        # 生命值
    current_npcs: list[str] = []
    triggered_events: list[str] = []     # 已触发的关键事件名

class AgentState(TypedDict, total=False):
    """LangGraph 节点间传递的状态（见 agents/graph.py）"""
    session_id: str
    novel_id: str
    player_action: str        # 玩家原始输入（不含 NPC 台词拼接）
    action_category: str      # Router 判定：dialog/action/off_rail
    target_npc: str | None    # Router 指出的对话目标
    npc_dialogue: str         # NPC Agent 产出的台词
    result: dict              # DM Agent 产出的完整结果（story/choices/state_changes）
```

> 用 `TypedDict` 而非 pydantic 模型承载图状态 —— LangGraph 对 `TypedDict` 的增量更新语义最稳定（节点只需返回自己改动的字段）。
> `game_state` / 短期记忆 / 检索结果**不进图状态**：它们由 `memory` 层按 `session_id` 持有（内存字典 + ChromaDB），图里只传引用与中间产物，避免每步深拷贝大对象。

### 3.2 SSE响应格式

```
data: {"type": "scene", "text": "你走进了黑暗的走廊..."}
data: {"type": "npc", "speaker": "守夜人", "text": "你谁？"}
data: {"type": "state", "key": "location", "value": "走廊"}
data: {"type": "choices", "options": ["往前走", "回头", "用手机照明"]}
data: {"type": "done"}
```

### 3.3 人物关系图数据

```json
{
  "nodes": [
    {"id": "孙悟空", "weight": 95, "group": "主角"},
    {"id": "唐僧", "weight": 90, "group": "主角团"}
  ],
  "links": [
    {"source": "孙悟空", "target": "唐僧", "relation": "师徒", "type": "师徒"}
  ]
}
```

## 四、开发流程（5天）

### Phase 1：后端基础设施（Day 1）

**目标**：小说上传→切片→Embedding→ChromaDB检索 + 人物提取，全链路Terminal跑通

**任务清单**：
1. 创建 `novel_game/` 项目目录结构
2. 编写 `requirements.txt`（fastapi, uvicorn, chromadb, sentence-transformers, openai, langgraph, jieba）
3. 编写 `config.py`（DeepSeek + ChromaDB配置）
4. 编写 `pipeline/novel_parser.py`：
   - 读取TXT文件
   - 按段落/章节切片（每段500-1000字）
   - 调用embedding入ChromaDB
5. 编写 `pipeline/character_extractor.py`（提前到Phase 1）：
   - LLM提取人物关系JSON（nodes+links）
   - LLM提取NPC人设档案（性格、秘密、说话风格）
   - 缓存在内存字典中
6. 编写 `memory/long_term.py`：
   - 封装ChromaDB检索接口
   - 输入：玩家动作文本 → 输出：相关段落top-K
7. Terminal测试：上传西游记样本→检索"孙悟空"→返回段落 + 人物列表

**验收标准**：
```
输入: novel_parser.ingest("xiyouji.txt")
输出: 已入库 120 段, novel_id=xxx

输入: long_term.retrieve("孙悟空大闹天宫")
输出: [第3段: "那猴王...", 第15段: "玉帝...", ...]

输入: character_extractor.get_npc_profile("孙悟空")
输出: {personality: "桀骜不驯", secret: "石猴出世", speech_style: "口语化，自称俺老孙"}
```

### Phase 2：单Agent跑通（Day 2）

**目标**：DM Agent单Prompt跑通"玩家动作→剧情推演"，函数化设计，Phase 3直接复用

**任务清单**：
1. 编写 `models.py`（GameState/PlayerAction/AgentState）
2. 编写 `pipeline/prompts.py`：
   - DM Prompt：system角色设定 + 记忆注入 + 玩家动作 → 输出JSON
3. 编写 `memory/short_term.py`（List维护5轮，FIFO）
4. 编写 `memory/global_state.py`（Dict初始化）
5. 编写 `agents/dm.py`（函数化，不扔代码）：
   - 核心函数 `dm_inference(state) -> result`
   - 组装：system prompt + 短期记忆 + 检索结果 + 全局状态 + 玩家动作
   - 调用DeepSeek API，解析JSON输出
   - 加retry机制（JSON解析失败重试1次）
6. Terminal测试：输入"向左走"→返回新剧情+选项

**验收标准**：
```
输入: dm_inference(state_with_action("向左走"))
输出: {"story": "你走进了黑暗的走廊...", "choices": ["继续走", "回头"], "state_changes": {"location": "走廊"}}
```

### Phase 3：升级多Agent + 条件路由（Day 3）

**目标**：单Agent→LangGraph多节点协作，条件边路由（非纯线性）

**任务清单**：
1. 编写 `agents/router.py`：
   - Prompt：判断动作类型（dialog/action/off_rail）
   - 输出：action_category
2. 编写 `agents/npc.py`：
   - Prompt：根据NPC人设档案生成台词
   - 输出：npc_dialogue
3. 编写 `agents/dm.py`（升级版）：
   - 调用Phase 2的 `dm_inference()` 函数（不重写）
   - 接收Router分类和NPC台词（如有）
4. 编写 `agents/graph.py`：
   - 定义StateGraph + **条件边**
   - 路由逻辑：
     ```
     Router → 判定类型
       ├─ dialog  → NPC Agent → DM Agent
       ├─ action  → DM Agent（跳过NPC）
       └─ off_rail → DM Agent（直接处理）
     ```
   - 不使用Rules Agent（合并到Router中）
5. Terminal测试：
   - 输入"和守夜人说话"→Router判定dialog→NPC生成台词→DM推演
   - 输入"向左走"→Router判定action→跳过NPC→DM推演

**验收标准**：
```
输入: game.act("和守夜人说话")
输出: {category: "dialog", npc_dialogue: "你谁？...", story: "守夜人警惕地看着你...", choices: [...]}

输入: game.act("向左走")
输出: {category: "action", npc_dialogue: null, story: "你走进了走廊...", choices: [...]}
```

### Phase 4：API + 前端（Day 4）

**目标**：Terminal→Web游戏界面，SSE流式输出

**任务清单**：
1. 编写 `api/route_novel.py`（上传接口）
2. 编写 `api/route_game.py`：
   - `POST /api/game/start`：游戏初始化（LLM生成开场场景+初始状态）← 新增
   - `POST /api/action`：玩家动作
   - StreamingResponse + SSE
   - Router/Rules/NPC快速跑完，DM Agent用 `stream=True` 流式输出
3. 编写 `api/main.py`（FastAPI入口，挂载static目录）
4. 编写 `static/index.html`：
   - 左侧：场景描述区（流式渲染）
   - 右侧：状态面板（位置/物品/HP）
   - 底部：选项按钮 + 自由输入框
5. 编写 `static/style.css`（暗色游戏主题）
6. 编写 `static/app.js`：
   - 用 `fetch` + `ReadableStream` 接收POST的SSE流（不用EventSource，因为EventSource不支持POST）
   - 打字机效果渲染
   - 状态面板实时更新
   - 选项按钮动态生成
7. 浏览器测试：完整游戏流程跑通

**验收标准**：
- 浏览器打开 → 上传小说 → 开始游戏（调用/api/game/start）→ 选项/输入 → 流式剧情 → 状态更新

### Phase 5：力导图 + 收尾（Day 5）

**目标**：人物关系可视化 + 演示录制

**任务清单**：
1. 编写 `api/route_graph.py`（返回关系数据，从Phase 1缓存的character_extractor读取）
2. 编写 `graphic/relation.html`（D3.js力导图）
3. 录制1分钟演示视频
4. 整理技术指标数据

**验收标准**：
- 打开relation.html → 可交互的人物关系图
- 演示视频包含：正常对话、触发记忆检索、NPC符合人设的推演

## 五、多维度分析

### 5.1 安全性

| 维度         | 分析                     | 决策                                              |
| ------------ | ------------------------ | ------------------------------------------------- |
| API Key保护  | .env文件不进版本控制     | .gitignore排除.env                                |
| 用户输入过滤 | 玩家自由输入可能含注入   | Router Agent做脱轨检测，DM Prompt用system角色隔离 |
| 数据隔离     | 小说内容只在本地ChromaDB | 不持久化到外部服务                                |

### 5.2 性能

| 维度              | 分析                       | 决策                                       |
| ----------------- | -------------------------- | ------------------------------------------ |
| 首包延迟          | DeepSeek API约500-800ms    | SSE流式输出，首包即返回                    |
| 多Agent串行延迟   | 4个Agent串行约2-4秒        | Router和Rules可合并为一个节点，减少到3节点 |
| ChromaDB检索      | 本地查询<100ms             | 可接受                                     |
| Embedding首次加载 | bge模型约400MB，首次加载慢 | 启动时预加载，加loading提示                |

### 5.3 可维护性

| 维度           | 分析                                | 决策                          |
| -------------- | ----------------------------------- | ----------------------------- |
| Prompt集中管理 | 所有Prompt模板放pipeline/prompts.py | 统一维护，方便调优            |
| Agent解耦      | 每个Agent独立文件                   | 可单独测试和迭代              |
| 状态结构清晰   | GameState和AgentState分离           | LangGraph节点间数据流转可追踪 |
| 配置集中       | config.py管理所有配置               | 一处修改全局生效              |

### 5.4 可扩展性

| 维度      | 当前     | 未来可扩展                         |
| --------- | -------- | ---------------------------------- |
| LLM切换   | DeepSeek | config改一行即可切Ollama/其他      |
| 小说类型  | 短篇优先 | ChromaDB支持长篇，只需调整切片策略 |
| Agent扩展 | 4节点    | LangGraph可加节点（如CombatAgent） |
| 前端扩展  | 单页HTML | 后续可升级React，后端API不变       |

## 六、依赖清单

```
# requirements.txt
fastapi
uvicorn[standard]
chromadb
sentence-transformers
openai
langgraph
jieba
python-dotenv
pydantic>=2.0
```

## 七、关键Prompt设计策略

所有Prompt集中在 `pipeline/prompts.py`，核心原则：

1. **System角色隔离**：每个Agent有独立的system prompt，防止玩家输入污染指令
2. **JSON输出**：要求DeepSeek返回结构化JSON，用 `response_format={"type": "json_object"}`
3. **记忆注入格式**：
   ```
   [短期记忆] 最近5轮对话摘要
   [长期记忆] ChromaDB检索结果
   [全局状态] 当前位置/物品/flag
   [玩家动作] {action}
   ```
4. **Few-shot示例**：每个Agent的Prompt中带1-2个示例，稳定输出格式
