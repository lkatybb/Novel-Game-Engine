# 开发执行手册

> 本文档面向开发者（老三），按顺序执行，每步都有验收命令。跑不通就停下问，不要跳步。

## 现状说明（先读这段，再看后面的历史步骤）

本文档记录的是 **Phase 1 建设期**的原始施工步骤，**不是当前状态的描述**。项目已走完 Phase 1 并按任务单完成一轮债务清理，**以下内容与现状不符，以 `README.md` 与 `ARCHITECTURE.md` 为准**：

- `models.py` 中的 `PlayerAction` 已被删除（全文搜索还会命中本文档第 162 行附近的旧代码片段，那是历史记录）。图状态 `AgentState` 现在是 `TypedDict`，字段为 `session_id` / `novel_id` / `player_action`(str) / `action_category` / `target_npc` / `npc_dialogue` / `result`。
- 动作接口路径是 `POST /api/game/action`（不是 `/api/action`）；另有 `/api/game/start`、`/api/game/resume`、`GET|DELETE /api/game/sessions/...`、`POST /api/novel/upload`、`GET /api/novel/list`、`GET|DELETE /api/novel/{novel_id}`。
- `agents/rules.py` **从未存在**（Rules 判定已合并进 `agents/router.py`）。
- 验收测试以 `test_contract.py`（契约）与 `test_tech_debt.py`（全链路回归）为准，`test_lock*.py` / `_tmp_*.py` / `diag*.py` 是开发期诊断脚本。
- 前端 UI 与「纸感」主题集中在 `static/style.css` + `static/app.js`，关系图是独立的 `graphic/relation.html`（自带样式，不引用 `style.css`）。

---

## 前置准备

### 环境要求
- Python 3.10+
- Windows / macOS / Linux
- 网络可访问 `https://api.deepseek.com`

### 第0步：安装依赖

```bash
cd e:\小说\novel_game
pip install -r requirements.txt
```

如果安装慢，加镜像：
```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 第0.5步：复制测试小说

```bash
copy "e:\小说\story_read\AI-Reader-V2-main\backend\sample-novels\西游记-样本.txt" "e:\小说\novel_game\data\novels\"
```

验证文件存在：
```bash
dir "e:\小说\novel_game\data\novels\"
```

应该看到 `西游记-样本.txt`。

### 第0.6步：创建包文件

在项目各子目录下创建空的 `__init__.py`（Python 包标识，确保 import 正常）：

```bash
cd e:\小说\novel_game
echo. > agents\__init__.py
echo. > memory\__init__.py
echo. > pipeline\__init__.py
echo. > api\__init__.py
```

---

## Phase 1：小说入库 + 人物提取 + 检索

### 已完成的文件（不要改）

| 文件                              | 作用                        |
| --------------------------------- | --------------------------- |
| `config.py`                       | DeepSeek + ChromaDB 配置    |
| `pipeline/novel_parser.py`        | 小说切片 + ChromaDB 入库    |
| `memory/long_term.py`             | ChromaDB 向量检索           |
| `pipeline/character_extractor.py` | LLM 提取人物关系 + NPC 人设 |
| `.env`                            | API Key 配置                |

### 验收命令

在 `novel_game` 目录下运行：

```bash
python -c "from pipeline.novel_parser import ingest; print(ingest('西游记-样本.txt'))"
```

预期输出：
```
入库: {'novel_id': 'novel_xxxxxxxx', 'title': '西游记-样本', 'chunk_count': N}
```
（N 取决于样本长度，西游记样本约 3-5 段，完整小说会更多）

如果报错：
- `ModuleNotFoundError` → 检查 `pip install` 是否成功
- `FileNotFoundError` → 检查小说文件是否在 `data/novels/` 下
- `ChromaDB 相关错误` → 删掉 `chroma_db/` 文件夹重试

```bash
python -c "from pipeline.novel_parser import ingest; from memory.long_term import retrieve, format_context; r = ingest('西游记-样本.txt'); print(format_context(retrieve(r['novel_id'], '孙悟空大闹天宫')))"
```

预期输出：
```
[原著片段1] 那猴王...
[原著片段2] 玉帝...
...
```

```bash
python -c "import pathlib; from pipeline.novel_parser import ingest; from pipeline.character_extractor import extract_characters, get_npc_profile; t = pathlib.Path('data/novels/西游记-样本.txt').read_text(encoding='utf-8'); r = ingest('西游记-样本.txt'); c = extract_characters(r['novel_id'], t); print('人物:', [n['id'] for n in c['graph']['nodes'][:5]]); print('孙悟空:', get_npc_profile(r['novel_id'], '孙悟空'))"
```

预期输出：
```
人物: ['孙悟空', '唐僧', '猪八戒', ...]
孙悟空: {'personality': '桀骜不驯...', 'secret': '石猴出世', 'speech_style': '口语化...', 'goal': '...'}
```

### 补丁：在 character_extractor.py 中添加 get_graph_data 函数

> Phase 4 的 `route_graph.py` 和 Phase 5 的力导图需要调用此函数，但原始代码中未定义。

在 `pipeline/character_extractor.py` 末尾追加：

```python
def get_graph_data(novel_id: str) -> dict:
    """获取人物关系图数据（供API和力导图使用）"""
    if novel_id not in _cache:
        raise ValueError(f"未找到小说人物数据: {novel_id}，请先上传并提取人物")
    return _cache[novel_id]["graph"]
```

验证补丁（需要先跑过人物提取，在同一 Python 进程中）：
```bash
python -c "
from pipeline.novel_parser import ingest
from pipeline.character_extractor import extract_characters, get_graph_data
import pathlib
t = pathlib.Path('data/novels/西游记-样本.txt').read_text(encoding='utf-8')
r = ingest('西游记-样本.txt')
extract_characters(r['novel_id'], t)
g = get_graph_data(r['novel_id'])
print('节点数:', len(g['nodes']), '边数:', len(g.get('links', g.get('edges', []))))
"
```
> **注意**：`character_extractor` 的缓存在内存中，重启 Python 后丢失。必须在一条命令里完成提取+验证。

### 验收通过后

Phase 1 完成，开始 Phase 2。

---

## Phase 2：单 Agent 跑通

### 需要新建的文件

#### 1. `models.py`（项目根目录）

数据结构定义，所有 Agent 共享。

```python
from pydantic import BaseModel, Field
from typing import Optional

class GameState(BaseModel):
    """全局游戏状态，所有Agent共享"""
    novel_id: str = ""
    player_location: str = "起始场景"
    inventory: list[str] = Field(default_factory=list)
    flags: dict[str, bool] = Field(default_factory=dict)
    val: int = 50  # 主状态值 0-100
    hp: int = 100
    current_npcs: list[str] = Field(default_factory=list)

class PlayerAction(BaseModel):
    """玩家输入"""
    action_type: str = "free_input"  # "choice" | "free_input"
    content: str = ""

class AgentState(BaseModel):
    """LangGraph节点间传递的状态"""
    game_state: GameState = Field(default_factory=GameState)
    player_action: PlayerAction = Field(default_factory=PlayerAction)
    short_term_memory: list[dict] = Field(default_factory=list)
    retrieved_context: str = ""
    action_category: str = ""  # Router判定: dialog/action/off_rail
    npc_dialogue: str = ""
    story_output: str = ""
    choices: list[str] = Field(default_factory=list)
```

#### 2. `pipeline/prompts.py`

所有 Prompt 模板集中管理。

```python
"""所有Agent的Prompt模板"""

# DM Agent 的 System Prompt
DM_SYSTEM = """你是一个文字冒险游戏的DM（地下城主）。

你的任务：根据玩家动作，推演下一步剧情。

你需要遵守以下规则：
1. 严格基于原著设定，不得创造与原著矛盾的内容
2. 玩家可以选择或自由输入，你需要合理回应
3. 每次回复必须给出2-4个后续选项
4. 回复格式必须是JSON

输出JSON格式：
{
  "story": "剧情描述（2-5句话）",
  "npc_dialogue": "NPC台词（如无NPC对话则为null）",
  "state_changes": {"location": "新位置（如变化）", "new_item": "新物品（如获得）", "flag": "事件标记（如触发）"},
  "choices": ["选项1", "选项2", "选项3"]
}

你收到的信息包含：
- [短期记忆] 最近5轮对话摘要
- [长期记忆] 原著相关片段（ChromaDB检索）
- [全局状态] 当前位置/物品/标记
- [玩家动作] 玩家输入的内容
"""

# Router Agent 的 System Prompt
ROUTER_SYSTEM = """你是一个动作分类器。判断玩家动作的类型。

分类规则：
- dialog：与NPC对话（如"和XX说话"、"问XX问题"）
- action：物理动作（如"向左走"、"拿钥匙"、"使用手电筒"）
- off_rail：脱离剧情的无理要求（如"我要飞"、"炸掉世界"）

输出JSON：{"category": "dialog"|"action"|"off_rail", "target_npc": "NPC名（如dialog类型，否则null）"}
"""

# NPC Agent 的 System Prompt
NPC_SYSTEM = """你是一个角色扮演Agent。根据NPC的人设档案，生成符合角色性格的台词。

规则：
1. 严格遵循角色的性格、说话风格
2. 如果角色有秘密，在信任度不够时不要泄露
3. 台词要自然，符合角色身份

你收到的信息：
- [NPC人设] 角色的性格、秘密、说话风格
- [玩家行为] 玩家对NPC做了什么
- [当前状态] 场景和已有物品

输出JSON：{"dialogue": "NPC说的一段话", "emotion": "NPC当前情绪"}
"""
```

#### 3. `memory/short_term.py`

```python
"""短期记忆：维护最近5轮对话，FIFO"""

from collections import deque
from config import SHORT_TERM_LIMIT

_memory: dict[str, deque] = {}

def init(session_id: str):
    _memory[session_id] = deque(maxlen=SHORT_TERM_LIMIT)

def add(session_id: str, turn: dict):
    """添加一轮对话: {"player": "向左走", "dm": "你走进了走廊..."}"""
    if session_id not in _memory:
        init(session_id)
    _memory[session_id].append(turn)

def get(session_id: str) -> list[dict]:
    if session_id not in _memory:
        return []
    return list(_memory[session_id])

def format_memory(session_id: str) -> str:
    """格式化为Prompt可用文本"""
    turns = get(session_id)
    if not turns:
        return "（暂无历史对话）"
    parts = []
    for i, t in enumerate(turns, 1):
        parts.append(f"第{i}轮 玩家: {t['player']} → DM: {t['dm']}")
    return "\n".join(parts)
```

#### 4. `memory/global_state.py`

```python
"""全局状态管理：Dict维护位置/物品/flag"""

from models import GameState

_states: dict[str, GameState] = {}

def init_state(session_id: str, novel_id: str):
    _states[session_id] = GameState(novel_id=novel_id)

def get_state(session_id: str) -> GameState:
    if session_id not in _states:
        raise ValueError(f"游戏未初始化: {session_id}")
    return _states[session_id]

def update_state(session_id: str, state_changes: dict):
    """更新全局状态"""
    state = get_state(session_id)
    if "location" in state_changes:
        state.player_location = state_changes["location"]
    if "new_item" in state_changes:
        if state_changes["new_item"] not in state.inventory:
            state.inventory.append(state_changes["new_item"])
    if "flag" in state_changes:
        state.flags[state_changes["flag"]] = True
    if "val" in state_changes:
        state.val = max(0, min(100, state.val + state_changes["val"]))
    if "hp" in state_changes:
        state.hp = max(0, state.hp + state_changes["hp"])

def format_state(session_id: str) -> str:
    """格式化为Prompt可用文本"""
    s = get_state(session_id)
    items = ", ".join(s.inventory) if s.inventory else "无"
    flags = ", ".join(s.flags.keys()) if s.flags else "无"
    return f"当前位置: {s.player_location}\n物品: {items}\n已触发事件: {flags}\n状态值: {s.val}/100\n生命: {s.hp}"
```

#### 5. `agents/dm.py`（单 Agent 版本，函数化）

> **架构说明**：`dm_inference` 只做推理不更新记忆，`dm_update_memory` 负责更新。
> 这样 Phase 3 的 LangGraph 可以在 NPC 台词生成后再更新记忆，避免顺序混乱。
> `dm_stream` 供 Phase 4 的 SSE 流式输出使用，服务端分块推送，无需第二次 LLM 调用。

```python
"""DM Agent：接收玩家动作，结合记忆推演剧情"""

import json
import logging
from openai import OpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from pipeline.prompts import DM_SYSTEM
from memory.short_term import format_memory
from memory.global_state import format_state, get_state
from memory.long_term import retrieve, format_context

logger = logging.getLogger(__name__)
_client = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    return _client

def _build_prompt(session_id: str, novel_id: str, player_action: str) -> str:
    """组装DM上下文（推理和流式共用）"""
    short_mem = format_memory(session_id)
    state_text = format_state(session_id)
    retrieved = retrieve(novel_id, player_action)
    long_mem = format_context(retrieved)

    return f"""[短期记忆]
{short_mem}

[长期记忆]
{long_mem}

[全局状态]
{state_text}

[玩家动作]
{player_action}
"""

def dm_inference(session_id: str, novel_id: str, player_action: str) -> dict:
    """
    DM Agent推理（不更新记忆，Phase 2/3共用）

    Returns:
        {"story": "...", "npc_dialogue": "...", "state_changes": {...}, "choices": [...]}
    """
    user_prompt = _build_prompt(session_id, novel_id, player_action)

    client = _get_client()
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": DM_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=2048,
            )
            result = json.loads(response.choices[0].message.content)
            break
        except (json.JSONDecodeError, Exception) as e:
            if attempt == 0:
                logger.warning("DM推理失败，重试: %s", e)
                continue
            raise

    return result

def dm_update_memory(session_id: str, player_action: str, result: dict):
    """更新短期记忆和全局状态（在推理之后调用）"""
    from memory.short_term import add as add_memory
    from memory.global_state import update_state
    add_memory(session_id, {"player": player_action, "dm": result.get("story", "")})
    state_changes = result.get("state_changes", {})
    if state_changes:
        update_state(session_id, state_changes)

def dm_run(session_id: str, novel_id: str, player_action: str) -> dict:
    """完整流程：推理 + 更新记忆（Phase 2验收用）"""
    result = dm_inference(session_id, novel_id, player_action)
    dm_update_memory(session_id, player_action, result)
    return result

def dm_stream(session_id: str, novel_id: str, player_action: str, chunk_size: int = 3):
    """
    流式DM：yield SSE数据块，服务端分块推送（无需第二次LLM调用）

    Yields: {"type": "scene", "text": "..."} 或 {"type": "result", "data": {...}}
    """
    result = dm_inference(session_id, novel_id, player_action)

    # 分块推送剧情文本（打字机效果）
    story = result.get("story", "")
    for i in range(0, len(story), chunk_size):
        chunk = story[i:i + chunk_size]
        yield {"type": "scene", "text": chunk}

    # 推送完整结果（含choices和state_changes）
    yield {"type": "result", "data": result}

    # 更新记忆
    dm_update_memory(session_id, player_action, result)
```

### 验收命令

```bash
python -c "
from pipeline.novel_parser import ingest
from pipeline.character_extractor import extract_characters
from memory.global_state import init_state
from agents.dm import dm_run
import pathlib

# 1. 入库
r = ingest('西游记-样本.txt')
print('入库:', r)

# 2. 初始化游戏状态
session_id = 'test001'
init_state(session_id, r['novel_id'])

# 3. DM推理（含记忆更新）
result = dm_run(session_id, r['novel_id'], '我想四处看看')
print('结果:', result)
"
```

预期输出：
```
入库: {'novel_id': 'novel_xxxx', 'title': '西游记-样本', 'chunk_count': N}
结果: {'story': '你环顾四周...', 'choices': ['往东走', '往西走', '...'], ...}
```

### 验收通过后

Phase 2 完成，开始 Phase 3。

---

## Phase 3：LangGraph 多 Agent + 条件路由

### 需要新建的文件

#### 1. `agents/router.py`

```python
"""路由Agent：判断玩家动作类型"""

import json
from openai import OpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from pipeline.prompts import ROUTER_SYSTEM

_client = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    return _client

def route(player_action: str) -> dict:
    """
    判断玩家动作类型

    Returns: {"category": "dialog"|"action"|"off_rail", "target_npc": "NPC名或null"}
    """
    client = _get_client()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM},
            {"role": "user", "content": player_action},
        ],
        response_format={"type": "json_object"},
        max_tokens=256,
    )
    return json.loads(response.choices[0].message.content)
```

#### 2. `agents/npc.py`

```python
"""NPC Agent：根据人设档案生成台词"""

import json
from openai import OpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from pipeline.prompts import NPC_SYSTEM
from pipeline.character_extractor import get_npc_profile
from memory.global_state import format_state

_client = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    return _client

def generate_dialogue(novel_id: str, session_id: str, npc_name: str, player_action: str) -> str:
    """
    生成NPC台词

    Returns: NPC说的一段话
    """
    profile = get_npc_profile(novel_id, npc_name)
    state_text = format_state(session_id)

    user_prompt = f"""[NPC人设]
角色名: {npc_name}
性格: {profile.get('personality', '未知')}
秘密: {profile.get('secret', '无')}
说话风格: {profile.get('speech_style', '正常')}
核心目标: {profile.get('goal', '未知')}

[当前状态]
{state_text}

[玩家行为]
{player_action}
"""
    client = _get_client()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": NPC_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=512,
    )
    result = json.loads(response.choices[0].message.content)
    return result.get("dialogue", "")
```

#### 3. `agents/graph.py`

```python
"""LangGraph多Agent编排：条件路由"""

from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

from agents.router import route
from agents.npc import generate_dialogue
from agents.dm import dm_inference, dm_update_memory

# 使用dict作为LangGraph状态（Pydantic模型在LangGraph中需要用dict形式）
class GraphState(TypedDict):
    session_id: str
    novel_id: str
    player_action: str
    action_category: str  # dialog/action/off_rail
    target_npc: Optional[str]
    npc_dialogue: str
    story_output: str
    choices: list[str]

def router_node(state: GraphState) -> dict:
    """路由节点：判断动作类型"""
    result = route(state["player_action"])
    return {
        "action_category": result.get("category", "action"),
        "target_npc": result.get("target_npc"),
    }

def npc_node(state: GraphState) -> dict:
    """NPC节点：生成台词（仅dialog路径触发）"""
    npc_name = state.get("target_npc")
    if not npc_name:
        return {"npc_dialogue": ""}

    dialogue = generate_dialogue(
        state["novel_id"],
        state["session_id"],
        npc_name,
        state["player_action"],
    )
    return {"npc_dialogue": dialogue}

def dm_node(state: GraphState) -> dict:
    """DM节点：推演剧情 + 更新记忆（使用拆分后的函数）"""
    # 把NPC台词拼到玩家动作后面
    action = state["player_action"]
    if state.get("npc_dialogue"):
        action = action + "\n（NPC回应: " + state["npc_dialogue"] + "）"

    # 只推理，不更新记忆（NPC台词已生成，此时状态正确）
    result = dm_inference(state["session_id"], state["novel_id"], action)
    # 推理完成后更新记忆
    dm_update_memory(state["session_id"], action, result)
    return {
        "story_output": result.get("story", ""),
        "choices": result.get("choices", []),
    }

def should_visit_npc(state: GraphState) -> str:
    """条件边：判断是否需要走NPC节点"""
    if state["action_category"] == "dialog":
        return "npc"
    return "dm"

def build_graph():
    """构建LangGraph"""
    graph = StateGraph(GraphState)

    graph.add_node("router", router_node)
    graph.add_node("npc", npc_node)
    graph.add_node("dm", dm_node)

    # 入口 → router
    graph.set_entry_point("router")

    # router → 条件路由
    graph.add_conditional_edges(
        "router",
        should_visit_npc,
        {
            "npc": "npc",
            "dm": "dm",
        },
    )

    # npc → dm
    graph.add_edge("npc", "dm")

    # dm → 结束
    graph.add_edge("dm", END)

    return graph.compile()

# 全局编译好的图实例
_game_graph = None

def get_graph():
    global _game_graph
    if _game_graph is None:
        _game_graph = build_graph()
    return _game_graph

def run_game(session_id: str, novel_id: str, player_action: str) -> dict:
    """
    运行游戏图，返回结果

    Returns: {category, npc_dialogue, story, choices}
    """
    graph = get_graph()
    result = graph.invoke({
        "session_id": session_id,
        "novel_id": novel_id,
        "player_action": player_action,
    })
    return {
        "category": result.get("action_category", ""),
        "npc_dialogue": result.get("npc_dialogue", ""),
        "story": result.get("story_output", ""),
        "choices": result.get("choices", []),
    }
```

### 验收命令

```bash
python -c "
from pipeline.novel_parser import ingest
from memory.global_state import init_state
from agents.graph import run_game

r = ingest('西游记-样本.txt')
session_id = 'test002'
init_state(session_id, r['novel_id'])

# 测试1: 对话类动作
result1 = run_game(session_id, r['novel_id'], '和孙悟空说话')
print('对话:', result1)

# 测试2: 动作类
result2 = run_game(session_id, r['novel_id'], '向左走')
print('动作:', result2)
"
```

预期输出：
```
对话: {'category': 'dialog', 'npc_dialogue': '你是何人？...', 'story': '...', 'choices': [...]}
动作: {'category': 'action', 'npc_dialogue': '', 'story': '...', 'choices': [...]}
```

关键验证点：对话类输出的 `npc_dialogue` 非空，动作类输出的 `npc_dialogue` 为空。

### 验收通过后

Phase 3 完成，开始 Phase 4。

---

## Phase 4：FastAPI SSE + 前端

### 需要新建的文件

#### 1. `api/route_novel.py`

```python
"""小说上传接口"""

import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File
from pipeline.novel_parser import ingest
from pipeline.character_extractor import extract_characters
from config import NOVELS_DIR

router = APIRouter(prefix="/api/novel", tags=["novel"])

@router.post("/upload")
async def upload_novel(file: UploadFile = File(...)):
    # 保存文件
    file_path = NOVELS_DIR / file.filename
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 入库
    result = ingest(file.filename)

    # 提取人物
    text = file_path.read_text(encoding="utf-8")
    extract_characters(result["novel_id"], text)

    return result
```

#### 2. `api/route_game.py`

> **修正说明**：去掉了第二次 LLM 调用（冗余的"重新流式输出"）。
> 改用 `dm_stream` 做服务端分块推送，打字机效果不花额外 API 费用。

```python
"""游戏接口：初始化 + 玩家动作（SSE）"""

import json
import uuid
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from memory.global_state import init_state, get_state
from memory.short_term import add as add_memory
from memory.long_term import retrieve, format_context
from agents.dm import dm_stream, _get_client, dm_inference, dm_update_memory
from agents.router import route
from agents.npc import generate_dialogue
from pipeline.prompts import DM_SYSTEM
from config import LLM_MODEL

router = APIRouter(prefix="/api/game", tags=["game"])

class StartRequest(BaseModel):
    novel_id: str

class ActionRequest(BaseModel):
    session_id: str
    novel_id: str
    action: str

@router.post("/start")
async def start_game(req: StartRequest):
    """游戏初始化：LLM生成开场场景"""
    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    init_state(session_id, req.novel_id)

    # 检索小说开头
    retrieved = retrieve(req.novel_id, "故事开头 开场")
    long_mem = format_context(retrieved)

    # LLM生成开场
    client = _get_client()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": DM_SYSTEM},
            {"role": "user", "content": f"游戏开始。请根据以下原著内容生成开场场景。\n\n[原著开头]\n{long_mem}\n\n请输出JSON。"},
        ],
        response_format={"type": "json_object"},
        max_tokens=2048,
    )
    result = json.loads(response.choices[0].message.content)

    # 记录到短期记忆
    add_memory(session_id, {"player": "(游戏开始)", "dm": result.get("story", "")})
    state_changes = result.get("state_changes", {})
    if state_changes:
        from memory.global_state import update_state
        update_state(session_id, state_changes)

    return {
        "session_id": session_id,
        "story": result.get("story", ""),
        "choices": result.get("choices", []),
        "state": get_state(session_id).model_dump(),
    }

@router.post("/action")
async def player_action(req: ActionRequest):
    """玩家动作，SSE流式返回（服务端分块，无需第二次LLM调用）"""
    def event_stream():
        try:
            # 1. Router快速判定动作类型
            route_result = route(req.action)
            category = route_result.get("category", "action")
            target_npc = route_result.get("target_npc")

            yield f"data: {json.dumps({'type': 'category', 'value': category})}\n\n"

            # 2. 如果是对话类，先生成NPC台词
            npc_dialogue = ""
            if category == "dialog" and target_npc:
                try:
                    npc_dialogue = generate_dialogue(
                        req.novel_id, req.session_id, target_npc, req.action
                    )
                    yield f"data: {json.dumps({'type': 'npc', 'text': npc_dialogue}, ensure_ascii=False)}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'npc', 'text': f'（{target_npc}沉默不语）'}, ensure_ascii=False)}\n\n"

            # 3. DM流式推演剧情（服务端分块，打字机效果）
            action_with_npc = req.action
            if npc_dialogue:
                action_with_npc += f"\n（NPC回应: {npc_dialogue}）"

            for chunk in dm_stream(req.session_id, req.novel_id, action_with_npc):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

            # 4. 推送状态更新
            state = get_state(req.session_id)
            yield f"data: {json.dumps({'type': 'state', 'state': state.model_dump()}, ensure_ascii=False)}\n\n"

            # 5. 结束
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            # 兜底：任何异常都通过SSE通知前端，避免页面卡死
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

#### 3. `api/route_graph.py`

```python
"""人物关系图接口"""

from fastapi import APIRouter
from pipeline.character_extractor import get_graph_data

router = APIRouter(prefix="/api/novel", tags=["graph"])

@router.get("/{novel_id}/graph")
async def get_graph(novel_id: str):
    return get_graph_data(novel_id)
```

#### 4. `api/main.py`

```python
"""FastAPI入口"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from api.route_novel import router as novel_router
from api.route_game import router as game_router
from api.route_graph import router as graph_router

app = FastAPI(title="小说互动游戏引擎")

app.include_router(novel_router)
app.include_router(game_router)
app.include_router(graph_router)

# 挂载静态文件
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

#### 5. `static/index.html`

> **修正说明**：加了独立的上传按钮，不再复用"确认"按钮。

```html
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <title>小说互动游戏</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div id="app">
        <!-- 左侧：场景描述 -->
        <div id="scene-panel">
            <div id="scene-text"></div>
            <div id="npc-text"></div>
        </div>

        <!-- 右侧：状态面板 -->
        <div id="state-panel">
            <h3>角色状态</h3>
            <div>位置: <span id="state-location">-</span></div>
            <div>物品: <span id="state-inventory">无</span></div>
            <div>状态值: <span id="state-val">50</span>/100</div>
            <div>生命: <span id="state-hp">100</span></div>
            <hr>
            <button id="upload-btn">上传新小说</button>
        </div>

        <!-- 底部：操作区 -->
        <div id="action-panel">
            <div id="choices"></div>
            <div id="input-area">
                <input type="text" id="player-input" placeholder="输入你的动作..." disabled>
                <button id="submit-btn" disabled>确认</button>
            </div>
        </div>
    </div>
    <!-- 隐藏的文件上传 -->
    <input type="file" id="file-input" accept=".txt,.md" style="display:none">
    <script src="app.js"></script>
</body>
</html>
```

#### 6. `static/style.css`

```css
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #1a1a2e; color: #e0e0e0; font-family: "Microsoft YaHei", sans-serif; }
#app { display: grid; grid-template-columns: 1fr 250px; grid-template-rows: 1fr auto; height: 100vh; gap: 1px; background: #333; }
#scene-panel { background: #1a1a2e; padding: 20px; overflow-y: auto; grid-row: 1; grid-column: 1; }
#state-panel { background: #16213e; padding: 20px; grid-row: 1; grid-column: 2; }
#action-panel { background: #0f3460; padding: 15px; grid-column: 1 / 3; }
#scene-text { font-size: 16px; line-height: 1.8; margin-bottom: 15px; }
#npc-text { color: #e94560; font-style: italic; margin-bottom: 15px; }
#choices { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }
.choice-btn { background: #533483; color: #fff; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
.choice-btn:hover { background: #6a4099; }
#input-area { display: flex; gap: 10px; }
#player-input { flex: 1; background: #1a1a2e; color: #e0e0e0; border: 1px solid #533483; padding: 8px 12px; border-radius: 4px; }
#submit-btn { background: #e94560; color: #fff; border: none; padding: 8px 20px; border-radius: 4px; cursor: pointer; }
#submit-btn:disabled { background: #555; cursor: not-allowed; }
#player-input:disabled { background: #333; cursor: not-allowed; }
#upload-btn { background: #533483; color: #fff; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; width: 100%; }
#upload-btn:hover { background: #6a4099; }
h3 { color: #e94560; margin-bottom: 15px; }
#state-panel div { margin-bottom: 8px; }
span { color: #e94560; }
```

#### 7. `static/app.js`

> **修正说明**：上传和动作分离为两个独立按钮；SSE 接收适配 `dm_stream` 的 `result` 类型块。

```javascript
let sessionId = null;
let novelId = null;

// === 上传小说 ===
document.getElementById('upload-btn').onclick = () => {
    document.getElementById('file-input').click();
};

document.getElementById('file-input').onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch('/api/novel/upload', { method: 'POST', body: formData });
    const data = await res.json();
    novelId = data.novel_id;
    // 开始游戏
    const startRes = await fetch('/api/game/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ novel_id: novelId })
    });
    const startData = await startRes.json();
    sessionId = startData.session_id;
    document.getElementById('scene-text').textContent = startData.story;
    renderChoices(startData.choices);
    updateState(startData.state);
    // 启用操作区
    document.getElementById('player-input').disabled = false;
    document.getElementById('submit-btn').disabled = false;
};

// === 发送玩家动作 ===
document.getElementById('submit-btn').onclick = async () => {
    if (!sessionId) return;
    const input = document.getElementById('player-input');
    const action = input.value.trim();
    if (!action) return;
    input.value = '';
    await sendAction(action);
};

async function sendAction(action) {
    // 禁用操作区，防止重复提交
    document.getElementById('submit-btn').disabled = true;
    document.getElementById('player-input').disabled = true;

    const response = await fetch('/api/game/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, novel_id: novelId, action })
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const sceneText = document.getElementById('scene-text');
    sceneText.textContent = '';
    document.getElementById('npc-text').textContent = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n\n');
        buffer = lines.pop();
        for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const data = JSON.parse(line.slice(6));
            switch (data.type) {
                case 'scene':
                    sceneText.textContent += data.text;
                    break;
                case 'npc':
                    document.getElementById('npc-text').textContent = data.text;
                    break;
                case 'result':
                    // dm_stream 的最终结果块：包含 choices 和 state_changes
                    if (data.data && data.data.choices) {
                        renderChoices(data.data.choices);
                    }
                    if (data.data && data.data.state_changes) {
                        // state 更新由后面的 state 块推送
                    }
                    break;
                case 'state':
                    updateState(data.state);
                    break;
                case 'choices':
                    renderChoices(data.options);
                    break;
                case 'error':
                    sceneText.textContent = '发生错误: ' + data.message;
                    break;
                case 'done':
                    break;
            }
        }
    }

    // 重新启用操作区
    document.getElementById('submit-btn').disabled = false;
    document.getElementById('player-input').disabled = false;
}

function updateState(state) {
    document.getElementById('state-location').textContent = state.player_location;
    document.getElementById('state-inventory').textContent = state.inventory.join(', ') || '无';
    document.getElementById('state-val').textContent = state.val;
    document.getElementById('state-hp').textContent = state.hp;
}

function renderChoices(choices) {
    const container = document.getElementById('choices');
    container.innerHTML = '';
    choices.forEach(choice => {
        const btn = document.createElement('button');
        btn.className = 'choice-btn';
        btn.textContent = choice;
        btn.onclick = () => sendAction(choice);
        container.appendChild(btn);
    });
}

// 回车提交
document.getElementById('player-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') document.getElementById('submit-btn').click();
});
```

### 验收命令

```bash
cd e:\小说\novel_game
python -m uvicorn api.main:app --port 8000
```

> **注意**：不要加 `--reload`！内存缓存（短期记忆/全局状态/人物数据）在 reload 时会丢失。改完代码后手动重启。

浏览器打开 `http://localhost:8000`

操作流程：
1. 页面加载，右侧状态面板有"上传新小说"按钮，点击它 → 弹出文件选择 → 选择 `西游记-样本.txt`
2. 等待上传+入库+人物提取（约5-10秒），自动开始游戏
3. 显示开场剧情 + 选项，左侧场景区显示剧情文本
4. 点击选项按钮 或 在输入框输入文字后点"确认" → 流式显示新剧情 → 状态更新

### 验收通过后

Phase 4 完成，开始 Phase 5。

---

## Phase 5：力导图 + 收尾

### 需要新建的文件

#### 1. `graphic/relation.html`

```html
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>人物关系图</title>
    <style>
        body { margin: 0; background: #1a1a2e; }
        svg { width: 100vw; height: 100vh; }
        .node circle { stroke: #fff; stroke-width: 1.5px; cursor: pointer; }
        .node text { fill: #e0e0e0; font-size: 12px; }
        .link { stroke: #999; stroke-opacity: 0.6; }
        .link text { fill: #666; font-size: 10px; }
    </style>
</head>
<body>
    <div>
        <label>小说ID: </label>
        <input type="text" id="novel-id" placeholder="输入novel_id">
        <button onclick="loadGraph()">加载</button>
    </div>
    <svg id="graph"></svg>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <script>
        async function loadGraph() {
            const novelId = document.getElementById('novel-id').value;
            const res = await fetch(`http://localhost:8000/api/novel/${novelId}/graph`);
            const data = await res.json();

            const svg = d3.select('#graph');
            svg.selectAll('*').remove();
            const width = window.innerWidth, height = window.innerHeight;

            const simulation = d3.forceSimulation(data.nodes)
                .force('link', d3.forceLink(data.links).id(d => d.id).distance(100))
                .force('charge', d3.forceManyBody().strength(-300))
                .force('center', d3.forceCenter(width / 2, height / 2));

            const link = svg.selectAll('.link').data(data.links).enter().append('line')
                .attr('class', 'link').attr('stroke-width', d => Math.sqrt(d.weight || 1));

            const node = svg.selectAll('.node').data(data.nodes).enter().append('g')
                .attr('class', 'node').call(d3.drag()
                    .on('start', (e, d) => { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
                    .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
                    .on('end', (e, d) => { d.fx = null; d.fy = null; }));

            node.append('circle').attr('r', d => d.weight ? d.weight / 10 + 5 : 10)
                .attr('fill', d => d.group === '主角' ? '#e94560' : d.group === '配角团' ? '#533483' : '#0f3460');

            node.append('text').attr('dx', 12).attr('dy', '.35em').text(d => d.id);

            simulation.on('tick', () => {
                link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
                    .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
                node.attr('transform', d => `translate(${d.x},${d.y})`);
            });
        }
    </script>
</body>
</html>
```

### 验收命令

1. 确保 Phase 4 的后端在运行（`http://localhost:8000`）
2. 用浏览器打开 `graphic/relation.html`
3. 输入 novel_id（从 Phase 1 的入库结果获取）
4. 点击"加载" → 看到人物关系力导图

### 收尾任务

1. 录制 1 分钟演示视频
2. 整理技术指标数据

---

## 常见问题排查

| 问题                                            | 原因                                                             | 解决                                  |
| ----------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------- |
| `ModuleNotFoundError: No module named 'config'` | 工作目录不对                                                     | 必须在 `novel_game/` 目录下运行       |
| `ChromaDB 相关错误`                             | 数据库损坏                                                       | 删掉 `chroma_db/` 文件夹重试          |
| `DeepSeek API 超时`                             | 网络问题或并发太多                                               | 重试，或检查 API Key                  |
| `JSON 解析失败`                                 | LLM 没返回标准 JSON                                              | 代码里有 retry，如果还失败检查 Prompt |
| `LangGraph 条件路由不生效`                      | `should_visit_npc` 返回值和 `add_conditional_edges` 的映射不匹配 | 检查返回值是否是 `"npc"` 或 `"dm"`    |
| `SSE 前端收不到数据`                            | fetch 的 response 解析有问题                                     | 打开浏览器 Console 看 error           |
| `dm_stream 的 result 块没被处理`                | 前端 case 'result' 分支缺失                                      | 确认 app.js 中有 `case 'result'` 处理 |
| `上传小说后没有人物提取`                        | character_extractor 可能因为长文本超时                           | 检查 `max_chars` 参数，减小到 4000    |

## 开发顺序总结

```
Phase 1 (已写好) → 验收 → Phase 2 → 验收 → Phase 3 → 验收 → Phase 4 → 验收 → Phase 5
```

每个 Phase 跑通验收命令再进下一步。跑不通就停下问。
