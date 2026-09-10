"""全局状态管理：Dict维护位置/物品/flag + 关键事件硬锁"""

import logging

from models import GameState
from collections import deque
from config import SHORT_TERM_LIMIT
from pipeline.character_extractor import get_key_events

logger = logging.getLogger(__name__)

_states: dict[str, GameState] = {}


def restore_session(session_id: str, game_state: dict, short_term_memory: list[dict]):
    """
    从快照还原 session 到内存（封装私有变量操作）

    Args:
        session_id: 会话 ID
        game_state: GameState dict（快照里存的）
        short_term_memory: 短期记忆轮次列表
    """
    from memory.short_term import _memory as st_memory
    _states[session_id] = GameState(**game_state)
    dq = deque(maxlen=SHORT_TERM_LIMIT)
    for turn in short_term_memory:
        dq.append(turn)
    st_memory[session_id] = dq


def init_state(session_id: str, novel_id: str):
    _states[session_id] = GameState(novel_id=novel_id)


def get_state(session_id: str) -> GameState:
    if session_id not in _states:
        raise ValueError(f"游戏未初始化: {session_id}")
    return _states[session_id]


def drop_state(session_id: str):
    """丢弃会话在内存中的状态（存档被删除时调用；不存在则无操作）"""
    _states.pop(session_id, None)


def _as_str_list(value) -> list[str]:
    """LLM 可能返回 str / list / None，统一归一化为去空字符串列表"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _accept_triggered(state: GameState, names) -> list[str]:
    """过滤本轮允许入库的关键事件：
    1) 白名单：必须是小说预设事件（丢弃 LLM 编造名）；
    2) 顺序闸门：必须接续已发生事件，不得跳过前置事件（防 LLM 提前剧透式触发），
       同一批内允许连续触发多个。取不到事件清单时放行，避免误杀。
    """
    try:
        order_map = {e["event_name"]: e.get("order", 999)
                     for e in get_key_events(state.novel_id)}
    except Exception as e:
        logger.warning("关键事件清单加载失败，本次放行: %s", e)
        return list(dict.fromkeys(str(n).strip() for n in names if n is not None and str(n).strip()))

    names = [str(n).strip() for n in names if n is not None and str(n).strip()]
    done_order = [order_map[n] for n in state.triggered_events if n in order_map]
    max_order = max(done_order, default=0)
    # 同批可能乱序传入（如 [事件3, 事件2]），按 order 排序后再做连续性判定
    candidates = sorted(set(names), key=lambda n: order_map.get(n, 10**9))
    accepted = []
    for n in candidates:
        if n not in order_map:
            logger.info("忽略非预设关键事件: %s", n)
            continue
        if n in state.triggered_events or n in accepted:
            continue
        if order_map[n] <= max_order + 1:
            accepted.append(n)
            max_order = max(max_order, order_map[n])
        else:
            logger.info("忽略跳序关键事件: %s（order=%s，当前已到 %s）",
                        n, order_map[n], max_order)
    return accepted


def update_state(session_id: str, state_changes: dict):
    """更新全局状态（new_item/flag 兼容 LLM 返回字符串或数组）"""
    if not isinstance(state_changes, dict):
        return
    state = get_state(session_id)
    if state_changes.get("location"):
        state.player_location = str(state_changes["location"])
    # scene 对象：DM 场景切换信息；未单独给 location 时用场景名同步位置
    scene = state_changes.get("scene")
    if isinstance(scene, dict) and scene.get("name") and not state_changes.get("location"):
        state.player_location = str(scene["name"])
    for item in _as_str_list(state_changes.get("new_item")):
        if item not in state.inventory:
            state.inventory.append(item)
    for flag_name in _as_str_list(state_changes.get("flag")):
        state.flags[flag_name] = True
    if state_changes.get("val"):
        try:
            state.val = max(0, min(100, state.val + int(state_changes["val"])))
        except (TypeError, ValueError):
            pass
    if state_changes.get("hp"):
        try:
            state.hp = max(0, state.hp + int(state_changes["hp"]))
        except (TypeError, ValueError):
            pass
    for en in _accept_triggered(
        state, _as_str_list(state_changes.get("triggered_events"))
    ):
        state.triggered_events.append(en)


def trigger_event(session_id: str, event_name: str):
    """标记一个关键事件已发生（硬锁机制的状态记录；白名单+顺序闸门统一过滤）"""
    state = get_state(session_id)
    for en in _accept_triggered(state, [event_name]):
        if en not in state.triggered_events:
            state.triggered_events.append(en)


def get_untriggered_events(novel_id: str, session_id: str) -> list[dict]:
    """
    获取当前尚未触发的关键事件（按 order 排序）

    Returns: [{"event_name": "...", "trigger_condition": "...", "order": 1}, ...]
    """
    state = get_state(session_id)
    triggered = set(state.triggered_events)
    all_events = get_key_events(novel_id)
    untriggered = [e for e in all_events if e["event_name"] not in triggered]
    # 按 order 排序，优先级高的在前
    untriggered.sort(key=lambda e: e.get("order", 999))
    return untriggered


def format_state(session_id: str) -> str:
    """格式化为Prompt可用文本"""
    s = get_state(session_id)
    items = ", ".join(s.inventory) if s.inventory else "无"
    flags = ", ".join(s.flags.keys()) if s.flags else "无"
    triggered = ", ".join(s.triggered_events) if s.triggered_events else "（尚未发生任何关键事件）"
    return (f"当前位置: {s.player_location}\n物品: {items}\n事件标记: {flags}\n"
            f"已触发关键事件: {triggered}\n状态值: {s.val}/100\n生命: {s.hp}")
