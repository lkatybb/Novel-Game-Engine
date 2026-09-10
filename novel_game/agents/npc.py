"""NPC Agent：根据人设档案生成台词 / 私聊回话"""

import json
from config import LLM_MODEL, get_llm_client
from pipeline.prompts import NPC_SYSTEM, NPC_CHAT_SYSTEM
from pipeline.character_extractor import get_npc_profile
from memory.global_state import format_state
from memory.short_term import format_memory

# 台词与私聊回话都是 2~5 句量级，512 够用又压住延迟
_NPC_MAX_TOKENS = 512
_CHAT_HISTORY_LIMIT = 6      # 只带最近 3 个来回，避免客户端塞长文拖慢推理
_CHAT_TEXT_LIMIT = 200       # 单条对话文本上限


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
        max_tokens=_NPC_MAX_TOKENS,
    )
    result = json.loads(response.choices[0].message.content)
    return result.get("dialogue", "")


def _format_history(history: list[dict] | None) -> str:
    """把前端回传的近几轮私聊格式化为文本（对话记录不落盘，服务端没有历史可言）"""
    lines = []
    for turn in (history or [])[-_CHAT_HISTORY_LIMIT:]:
        if not isinstance(turn, dict):
            continue
        content = str(turn.get("content") or "").strip()[:_CHAT_TEXT_LIMIT]
        if content:
            lines.append(f"{'玩家' if turn.get('role') == 'user' else '你'}: {content}")
    return "\n".join(lines) if lines else "（这是你们第一次私下交谈）"


def build_chat_prompt(novel_id: str, session_id: str, npc_name: str, message: str,
                      history: list[dict] | None = None) -> str:
    """组装私聊 prompt：只给角色"已经知道"的东西（人设 + 已发生剧情 + 当前状态）。

    未触发关键事件清单、trigger_condition、"下一个必须发生的关键事件"一律不注入——角色不知道后文。
    这与 A11 锁死的 Mask 口径一致：DM 自由文本容器（位置/物品/见闻）与已触发事件可用，
    未触发的结构性进度不给。
    """
    profile = get_npc_profile(novel_id, npc_name)
    return f"""[NPC人设]
角色名: {npc_name}
性格: {profile.get('personality', '未知')}
秘密: {profile.get('secret', '无')}
说话风格: {profile.get('speech_style', '正常')}
核心目标: {profile.get('goal', '未知')}

[已知剧情]
{format_memory(session_id)}

[角色已知的状态]
{format_state(session_id)}

[对话记录]
{_format_history(history)}

[玩家的话]
{message}
"""


def chat(novel_id: str, session_id: str, npc_name: str, message: str,
         history: list[dict] | None = None) -> str:
    """AI 角色私聊：以角色身份回话。

    只读——不写全局状态、不写短期记忆、不触发关键事件、不产生存档快照。

    Raises:
        ValueError: 该角色没有可用的人设档案（人设校准不了，宁可明说也不硬答）
    """
    if not get_npc_profile(novel_id, npc_name):
        raise ValueError(f"没有该角色的人设档案: {npc_name}")

    client = get_llm_client()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": NPC_CHAT_SYSTEM},
            {"role": "user",
             "content": build_chat_prompt(novel_id, session_id, npc_name, message, history)},
        ],
        response_format={"type": "json_object"},
        max_tokens=_NPC_MAX_TOKENS,
    )
    result = json.loads(response.choices[0].message.content)
    return str(result.get("reply", "")).strip()
