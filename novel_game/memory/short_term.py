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


def drop(session_id: str):
    """丢弃会话的短期记忆（存档被删除时调用；不存在则无操作）"""
    _memory.pop(session_id, None)


def format_memory(session_id: str) -> str:
    """格式化为Prompt可用文本"""
    turns = get(session_id)
    if not turns:
        return "（暂无历史对话）"
    parts = []
    for i, t in enumerate(turns, 1):
        parts.append(f"第{i}轮 玩家: {t['player']} → DM: {t['dm']}")
    return "\n".join(parts)
