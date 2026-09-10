"""NPC Agent：根据人设档案生成台词"""

import json
from config import LLM_MODEL, get_llm_client
from pipeline.prompts import NPC_SYSTEM
from pipeline.character_extractor import get_npc_profile
from memory.global_state import format_state


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
    client = get_llm_client()
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
