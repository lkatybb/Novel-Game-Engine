"""路由Agent：判断玩家动作类型"""

import json
from config import LLM_MODEL, get_llm_client
from pipeline.prompts import ROUTER_SYSTEM


def route(player_action: str) -> dict:
    """
    判断玩家动作类型

    Returns: {"category": "dialog"|"action"|"off_rail", "target_npc": "NPC名或null"}
    """
    client = get_llm_client()
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
