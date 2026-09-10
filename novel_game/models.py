"""数据结构定义，所有 Agent 共享"""

from typing import TypedDict

from pydantic import BaseModel, Field


class GameState(BaseModel):
    """全局游戏状态，所有Agent共享"""
    novel_id: str = ""
    player_location: str = "起始场景"
    inventory: list[str] = Field(default_factory=list)
    flags: dict[str, bool] = Field(default_factory=dict)
    val: int = 50  # 主状态值 0-100
    hp: int = 100
    current_npcs: list[str] = Field(default_factory=list)
    triggered_events: list[str] = Field(default_factory=list)  # 已触发的关键事件名


class AgentState(TypedDict, total=False):
    """LangGraph 节点间传递的状态（见 agents/graph.py）

    注意：这里用 TypedDict 而非 pydantic 模型——LangGraph 对 TypedDict 的
    增量更新语义最稳定（节点只需返回自己改动的字段）。

    game_state / 短期记忆 / 检索结果不放进图状态：它们由 memory 层按 session_id
    持有（内存字典 + ChromaDB），图里只传引用与中间产物，避免每步深拷贝大对象。
    """
    session_id: str
    novel_id: str
    player_action: str        # 玩家原始输入（不含 NPC 台词拼接）
    action_category: str      # Router 判定: dialog/action/off_rail
    target_npc: str | None    # Router 指出的对话目标
    npc_dialogue: str         # NPC Agent 产出的台词
    result: dict              # DM Agent 产出的完整结果（story/choices/state_changes）
