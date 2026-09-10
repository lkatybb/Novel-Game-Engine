"""会话脉络：把滑出短期记忆窗口的历史轮次压缩成关键节点落盘。

定位：派生物（与 chroma_db、character_cache 同类）。内容全部来自短期记忆与全局状态，
可随时重建，缺失或损坏都不影响游戏继续；因此**不进 GameState**（不动接口字段）、
不随 SSE 下发，只作为 prompt 上下文的一部分。

与短期记忆的分工：短期记忆保留最近 SHORT_TERM_LIMIT 轮原文；本模块只补"更早的轮次"，
注入 prompt 时取窗口之外的节点，同一轮不会重复出现。
有界性：滑出窗口的轮次最多注入 EARLY_NODE_LIMIT 条，更早的只保留触发过关键事件的节点，
避免 prompt 随轮次无限膨胀。
"""

import json
import logging

from config import EARLY_NODE_LIMIT, SESSION_MEMORY_DIR
from memory.short_term import get as get_short_term

logger = logging.getLogger(__name__)

ACTION_LIMIT = 60   # 每轮玩家动作保留字数
STORY_LIMIT = 120   # 每轮 DM 正文摘录字数


def _path(session_id: str):
    return SESSION_MEMORY_DIR / f"{session_id}.json"


def _clip(text, limit: int) -> str:
    """压成单行并截断：关键节点只留脉络，不留全文"""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def load(session_id: str) -> dict:
    """读取会话脉络；尚未记录过返回空节点列表"""
    path = _path(session_id)
    if not path.exists():
        return {"session_id": session_id, "nodes": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        # 派生物损坏不该让整轮推演失败（与 long_term.retrieve 的失败口径一致）：本轮按无节点处理
        logger.warning("会话脉络读取失败，本轮忽略: %s (%s)", path.name, e)
        return {"session_id": session_id, "nodes": []}


def record_turn(session_id: str, action: str, story: str, events: list[str]):
    """追加一轮关键节点（每轮推演结束后调用一次；events 只记本轮新触发的事件）"""
    data = load(session_id)
    nodes = data.setdefault("nodes", [])
    nodes.append({
        "turn": len(nodes) + 1,
        "action": _clip(action, ACTION_LIMIT),
        "story": _clip(story, STORY_LIMIT),
        "events": list(events),
    })
    _path(session_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def format_early_nodes(session_id: str) -> str:
    """把已滑出短期记忆窗口的关键节点格式化为 prompt 片段；没有则返回空串"""
    nodes = load(session_id).get("nodes", [])
    evicted = nodes[: max(0, len(nodes) - len(get_short_term(session_id)))]
    # 更早的轮次只保留触发过关键事件的（剧情不可逆的节点），其余丢弃以保持有界
    early = [n for n in evicted[:-EARLY_NODE_LIMIT] if n.get("events")]
    early += evicted[-EARLY_NODE_LIMIT:]

    lines = []
    for node in early:
        line = f"第{node['turn']}轮 玩家：{node['action']}"
        if node.get("events"):
            line += f"（触发关键事件：{'、'.join(node['events'])}）"
        lines.append(line)
        if node.get("story"):
            lines.append(f"  当时剧情：{node['story']}")
    return "\n".join(lines)


def drop(session_id: str):
    """删除会话脉络文件（存档或小说被删除时调用；不存在则无操作）"""
    _path(session_id).unlink(missing_ok=True)
