"""LangGraph 多 Agent 编排：Router →(条件边)→ NPC → DM

把原来手写在 api/route_game.py 与 play.py 里的编排逻辑收敛到一张图：

    START → router ─┬─ dialog 且命中 NPC → npc → dm → END
                    └─ 其它             → dm      → END

三个节点都直接复用已有实现（agents/router.route、agents/npc.generate_dialogue、
agents/dm.dm_stream），本模块只负责"谁来调、按什么顺序调"。

流式输出走 LangGraph 的 custom stream：dm 节点内部用 get_stream_writer() 把 LLM
逐 token 的产出实时推给图外，因此走图不会牺牲打字机效果。
"""

import logging

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from agents.dm import dm_stream
from agents.npc import generate_dialogue
from agents.router import route
from models import AgentState

logger = logging.getLogger(__name__)

# 节点名（条件边的分支目标也用这些常量，避免字符串写错）
NODE_ROUTER = "router"
NODE_NPC = "npc"
NODE_DM = "dm"


def _emit(event: dict):
    """把一条事件推给图外（SSE 层消费）。

    非流式调用（invoke）时 LangGraph 返回 no-op writer，写入被直接丢弃。
    """
    get_stream_writer()(event)


def _router_node(state: AgentState) -> dict:
    """判定动作类型；分类失败降级为普通动作，不阻断本轮推演。"""
    _emit({"type": "stage", "text": "正在理解你的行动…"})
    try:
        result = route(state["player_action"])
        category = result.get("category", "action")
        target_npc = result.get("target_npc")
    except Exception:
        logger.exception("路由分类失败，降级为普通动作")
        category, target_npc = "action", None
    return {"action_category": category, "target_npc": target_npc}


def _route_after_router(state: AgentState) -> str:
    """条件边：只有"对话类且指名了 NPC"才绕道 NPC 节点（省一次 LLM 调用）。"""
    if state.get("action_category") == "dialog" and state.get("target_npc"):
        return NODE_NPC
    return NODE_DM


def _npc_node(state: AgentState) -> dict:
    """按人设档案生成 NPC 台词；生成失败用兜底文案，不中断本轮。"""
    target_npc = state["target_npc"]
    _emit({"type": "stage", "text": f"正在等待{target_npc}回应…"})
    try:
        dialogue = generate_dialogue(
            state["novel_id"], state["session_id"], target_npc, state["player_action"]
        )
    except Exception:
        logger.exception("NPC 台词生成失败，使用兜底文案")
        dialogue = "（沉默不语）"

    # 台词在此推出，但由 SSE 层压后到场景弹窗之后展示（保证弹窗先于一切语句）
    _emit({"type": "npc", "speaker": target_npc, "text": dialogue})
    return {"npc_dialogue": dialogue}


def _dm_node(state: AgentState) -> dict:
    """主推演节点：把 NPC 台词并入输入，流式转发 DM 产出，最后收集完整结果。"""
    _emit({"type": "stage", "text": "正在推演剧情…"})

    player_action = state["player_action"]
    if state.get("npc_dialogue"):
        player_action += f"\n（NPC回应: {state['npc_dialogue']}）"

    result: dict = {}
    for chunk in dm_stream(state["session_id"], state["novel_id"], player_action):
        if chunk.get("type") == "result":
            # 结果不回流，交由图状态的 result 字段向外传递（避免重复投递）
            result = chunk.get("data") or {}
            continue
        _emit(chunk)

    return {"result": result}


def _build_graph():
    builder = StateGraph(AgentState)
    builder.add_node(NODE_ROUTER, _router_node)
    builder.add_node(NODE_NPC, _npc_node)
    builder.add_node(NODE_DM, _dm_node)

    builder.add_edge(START, NODE_ROUTER)
    builder.add_conditional_edges(
        NODE_ROUTER, _route_after_router, {NODE_NPC: NODE_NPC, NODE_DM: NODE_DM}
    )
    builder.add_edge(NODE_NPC, NODE_DM)
    builder.add_edge(NODE_DM, END)
    return builder.compile()


# 编译一次常驻复用（纯内存操作，不涉及网络）
_APP = _build_graph()


def _initial_state(session_id: str, novel_id: str, player_action: str) -> AgentState:
    return {
        "session_id": session_id,
        "novel_id": novel_id,
        "player_action": player_action,
        "action_category": "",
        "target_npc": None,
        "npc_dialogue": "",
        "result": {},
    }


def stream_graph(session_id: str, novel_id: str, player_action: str):
    """流式运行图（供 SSE 端点使用）。

    Yields:
        (mode, chunk) 二元组：
          ("custom",  event)  —— DM 的流式事件 / stage 提示 / npc 台词
          ("updates", {节点名: 该节点的增量产出})

    custom 事件按产生顺序实时到达；updates 在对应节点结束时到达。
    """
    yield from _APP.stream(
        _initial_state(session_id, novel_id, player_action),
        stream_mode=["custom", "updates"],
    )


def run_graph(session_id: str, novel_id: str, player_action: str) -> dict:
    """一次性跑完整图（无流式），供 CLI / 测试 / 批处理使用。

    Returns:
        {"category", "target_npc", "npc_dialogue", "story", "choices"}
    """
    final = _APP.invoke(_initial_state(session_id, novel_id, player_action))
    result = final.get("result") or {}
    return {
        "category": final.get("action_category", ""),
        "target_npc": final.get("target_npc"),
        "npc_dialogue": final.get("npc_dialogue", ""),
        "story": result.get("story", ""),
        "choices": result.get("choices", []),
    }
