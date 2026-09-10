"""DM Agent：接收玩家动作，结合记忆推演剧情"""

import json
import logging
import re
from config import LLM_MODEL, LLM_MAX_TOKENS, get_llm_client
from pipeline.prompts import DM_SYSTEM
from memory.short_term import format_memory
from memory.global_state import format_state, get_next_event
from memory.long_term import retrieve, format_context

logger = logging.getLogger(__name__)


def _build_prompt(session_id: str, novel_id: str, player_action: str) -> str:
    """组装DM上下文（推理和流式共用）"""
    short_mem = format_memory(session_id)
    state_text = format_state(session_id)
    retrieved = retrieve(novel_id, player_action)
    long_mem = format_context(retrieved)

    # 只注入"下一个待触发关键事件"：一次把整条未触发清单塞进 prompt 等于提前把后文剧情告诉模型，
    # 且原先连 trigger_condition 一起下发，等于把"何时该发生"也剧透掉
    try:
        nxt = get_next_event(session_id)
    except ValueError:
        nxt = None  # 游戏未初始化时忽略

    key_events_text = ""
    if nxt:
        key_events_text = (f"\n\n[下一个必须发生的关键事件]\n"
                           f"  {nxt['order']}. {nxt['event_name']}\n"
                           f"你有责任在合适时机自然推进它发生，不能让玩家跳过或改变结果。")

    return f"""[短期记忆]
{short_mem}

[长期记忆]
{long_mem}

[全局状态]
{state_text}{key_events_text}

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

    client = get_llm_client()
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": DM_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=LLM_MAX_TOKENS,
            )
            result = json.loads(response.choices[0].message.content)
            break
        except Exception as e:
            if attempt == 0:
                logger.warning("DM推理失败，重试: %s", e)
                continue
            raise

    return result


def dm_update_memory(session_id: str, player_action: str, result: dict):
    """更新短期记忆、全局状态，并检查硬锁关键事件是否触发"""
    from memory.short_term import add as add_memory
    from memory.global_state import update_state, trigger_event

    add_memory(session_id, {"player": player_action, "dm": result.get("story", "")})

    state_changes = result.get("state_changes", {})
    if state_changes:
        update_state(session_id, state_changes)

    # === 硬锁事件触发 ===
    triggered_now = set()

    # 关键事件只认 DM 在 state_changes.triggered_events 里的显式声明（必填字段）
    # （白名单过滤与 order 顺序闸门在 global_state 写入入口统一执行，编造/跳序事件名不会入库）
    dm_triggered = state_changes.get("triggered_events", []) if isinstance(state_changes, dict) else []
    logger.info("DM 声明的 triggered_events: %s", dm_triggered)
    for en in dm_triggered:
        triggered_now.add(en)

    for en in triggered_now:
        try:
            trigger_event(session_id, en)
            logger.info("关键事件已触发: %s", en)
        except Exception as e:
            logger.warning("trigger_event(%s) 失败: %s", en, e)


def dm_run(session_id: str, novel_id: str, player_action: str) -> dict:
    """完整流程：推理 + 更新记忆（Phase 2验收用）"""
    result = dm_inference(session_id, novel_id, player_action)
    dm_update_memory(session_id, player_action, result)
    return result


_STORY_KEY_RE = re.compile(r'"story"\s*:\s*"')
_SC_KEY_RE = re.compile(r'"state_changes"\s*:\s*')
_SCENE_KEY_RE = re.compile(r'"scene"\s*:\s*')
_NAME_KEY_RE = re.compile(r'"name"\s*:\s*"')
_DESC_KEY_RE = re.compile(r'"desc"\s*:\s*"')
_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\',
            '/': '/', 'b': '\b', 'f': '\f'}


def _extract_story_so_far(full: str) -> str | None:
    """从可能不完整的流式 JSON 中增量提取 "story" 字段已生成的文本。

    story 字段尚未开始返回 None；未闭合时返回已生成部分。
    转义序列（\\n 等）解码后输出，不完整的转义留到下一个 delta。
    """
    m = _STORY_KEY_RE.search(full)
    if not m:
        return None
    i = m.end()
    out = []
    while i < len(full):
        c = full[i]
        if c == '\\':
            if i + 1 >= len(full):
                break  # 转义序列跨 delta，等待后续
            out.append(_ESCAPES.get(full[i + 1], full[i + 1]))
            i += 2
        elif c == '"':
            break  # story 字段闭合
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def _scan_object_end(s: str, brace_pos: int):
    """字符串感知的花括号配对：返回与 s[brace_pos] 的 '{' 配对的 '}' 位置，未闭合返回 None。"""
    depth = 0
    in_str = False
    esc = False
    i = brace_pos
    while i < len(s):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return None


def _read_string_at(s: str, quote_pos: int):
    """s[quote_pos] 为开口引号，解码 JSON 字符串；闭合返回 (值, 结束位置)，未闭合返回 None。"""
    out = []
    i = quote_pos + 1
    while i < len(s):
        c = s[i]
        if c == '\\':
            if i + 1 >= len(s):
                return None
            out.append(_ESCAPES.get(s[i + 1], s[i + 1]))
            i += 2
        elif c == '"':
            return ''.join(out), i + 1
        else:
            out.append(c)
            i += 1
    return None


def _extract_scene_so_far(full: str):
    """从流式 JSON 中尽早提取 state_changes.scene（要求 prompt 把 state_changes 放在 story 前）。

    返回 (status, scene)：
      ("present", {"name", "desc"})  scene.name 已完整
      ("absent", None)               可确定本轮无 scene（state_changes 闭合无 scene / scene:null）
      ("pending", None)              尚不能确定，继续等待
    """
    m = _SC_KEY_RE.search(full)
    if not m:
        return 'pending', None
    i = m.end()
    while i < len(full) and full[i] in ' \n\r\t':
        i += 1
    if i >= len(full):
        return 'pending', None
    if full[i] != '{':
        return 'absent', None  # state_changes 为 null 等异常形态
    sc_close = _scan_object_end(full, i)
    sc_body = full[i + 1: sc_close + 1 if sc_close is not None else len(full)]

    sm = _SCENE_KEY_RE.search(sc_body)
    if not sm:
        return ('absent', None) if sc_close is not None else ('pending', None)
    j = sm.end()
    while j < len(sc_body) and sc_body[j] in ' \n\r\t':
        j += 1
    if j >= len(sc_body):
        return 'pending', None
    if sc_body[j] == 'n':
        return 'absent', None  # scene: null
    if sc_body[j] != '{':
        return 'absent', None

    scene_close = _scan_object_end(sc_body, j)
    scene_body = sc_body[j + 1: scene_close + 1 if scene_close is not None else len(sc_body)]
    nm = _NAME_KEY_RE.search(scene_body)
    if not nm:
        return ('absent', None) if scene_close is not None else ('pending', None)
    name_res = _read_string_at(scene_body, nm.end() - 1)
    if not name_res:
        return 'pending', None
    name = name_res[0].strip()
    if not name:
        return ('absent', None) if scene_close is not None else ('pending', None)

    desc = ''
    dm = _DESC_KEY_RE.search(scene_body)
    if dm:
        desc_res = _read_string_at(scene_body, dm.end() - 1)
        if desc_res:
            desc = desc_res[0].strip()
    return 'present', {'name': name, 'desc': desc}


def dm_stream(session_id: str, novel_id: str, player_action: str):
    """
    流式DM：LLM 真流式输出。

    story 文本随 token 到达增量推送（前端打字机天然跟速，推理不再"卡住"），
    流结束后完整解析 JSON 推送 result（choices / state_changes），再更新记忆。

    Yields: {"type": "scene", "text": "..."} 或 {"type": "result", "data": {...}}
    """
    user_prompt = _build_prompt(session_id, novel_id, player_action)
    client = get_llm_client()

    stream = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": DM_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=LLM_MAX_TOKENS,
        stream=True,
    )

    full_content = ""
    emitted_len = 0
    gate_open = False   # scene 归属确定前缓冲正文，保证 scene_change 永远先于文字
    pending_scene = None

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content or ""
        if not delta:
            continue
        full_content += delta

        # 门控：state_changes 在 JSON 前部（prompt 约束字段顺序），尽早判定 scene
        if not gate_open:
            status, scene = _extract_scene_so_far(full_content)
            if status != "pending":
                gate_open = True
                if status == "present":
                    pending_scene = dict(scene)

        story = _extract_story_so_far(full_content)
        if story and len(story) > emitted_len:
            new_part = story[emitted_len:]
            emitted_len = len(story)
            if gate_open:
                if pending_scene:
                    yield {"type": "scene_change", "scene": dict(pending_scene)}
                    pending_scene = None
                yield {"type": "scene", "text": new_part}
            # 门未开：正文留在 full_content 里等待，暂不下发

    # 流结束仍 pending（字段顺序异常 / JSON 截断）：最终判定一次并放行全部缓冲
    if not gate_open:
        status, scene = _extract_scene_so_far(full_content)
        if status == "present":
            pending_scene = dict(scene)
    if pending_scene:
        yield {"type": "scene_change", "scene": dict(pending_scene)}
        pending_scene = None
    story = _extract_story_so_far(full_content)
    if story and len(story) > emitted_len:
        yield {"type": "scene", "text": story[emitted_len:]}

    # 流结束：完整解析 JSON，取 choices / state_changes
    try:
        result = json.loads(full_content)
    except json.JSONDecodeError:
        # 被 max_tokens 截断等异常：用已推送的 story 兜底，choices 留空走自由输入
        logger.exception("DM 流式返回的 JSON 不完整，使用 story 兜底")
        result = {"story": _extract_story_so_far(full_content) or "", "choices": []}

    yield {"type": "result", "data": result}

    # 更新记忆
    dm_update_memory(session_id, player_action, result)
