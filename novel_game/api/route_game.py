"""游戏接口：初始化 + 玩家动作（SSE）+ 恢复会话"""

import json
import logging
import uuid
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from memory.global_state import init_state, get_state, update_state, restore_session
from memory.short_term import add as add_memory, get as get_memory
from memory.long_term import retrieve, format_context
from agents.graph import stream_graph
from pipeline.prompts import DM_SYSTEM
from config import LLM_MODEL, LLM_MAX_TOKENS, get_llm_client
from memory.session_store import (
    save_session, load_session,
    add_session_to_novel, update_session_meta, list_sessions_for_novel,
)

router = APIRouter(prefix="/api/game", tags=["game"])


def _enrich_state(state, novel_id):
    """把完整 key_events 清单挂到 state 上，供前端渲染时间线"""
    from pipeline.character_extractor import get_key_events
    all_events = get_key_events(novel_id)
    all_events_sorted = sorted(all_events, key=lambda e: e.get("order", 999))
    enriched = state.model_dump()
    enriched["_timeline"] = [
        {"event_name": e["event_name"], "order": e.get("order", 999),
         "trigger_condition": e.get("trigger_condition", "")}
        for e in all_events_sorted
    ]
    return enriched


class StartRequest(BaseModel):
    novel_id: str


class ResumeRequest(BaseModel):
    session_id: str


class ActionRequest(BaseModel):
    session_id: str
    novel_id: str
    action: str


@router.post("/start")
async def start_game(req: StartRequest):
    """游戏初始化：LLM生成开场场景"""
    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    try:
        init_state(session_id, req.novel_id)

        # 检索小说开头
        retrieved = retrieve(req.novel_id, "故事开头 开场")
        long_mem = format_context(retrieved)

        # LLM生成开场
        client = get_llm_client()
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": DM_SYSTEM},
                {"role": "user", "content": f"游戏开始。请根据以下原著内容生成开场场景。\n\n[原著开头]\n{long_mem}\n\n请输出JSON。"},
            ],
            response_format={"type": "json_object"},
            max_tokens=LLM_MAX_TOKENS,
        )
        result = json.loads(response.choices[0].message.content)

        # 记录到短期记忆
        add_memory(session_id, {"player": "(游戏开始)", "dm": result.get("story", "")})
        state_changes = result.get("state_changes", {})
        if state_changes:
            update_state(session_id, state_changes)
        # 开场只建立场景：LLM 可能在 state_changes 里提前/编造关键事件，
        # 新游戏必须从零事件开始，强制清空（位置/物品等保留）
        get_state(session_id).triggered_events.clear()

        # 开场场景信息（供前端弹场景切换窗）
        scene_obj = None
        if isinstance(state_changes, dict):
            _sn = state_changes.get("scene")
            if isinstance(_sn, dict) and _sn.get("name"):
                scene_obj = {"name": str(_sn.get("name")),
                             "desc": str(_sn.get("desc") or "")}

        # 挂到书架 + 立即保存快照
        add_session_to_novel(req.novel_id, session_id, "（游戏开始）")
        save_session(session_id, req.novel_id,
                     get_state(session_id).model_dump(), get_memory(session_id))

        return {
            "session_id": session_id,
            "story": result.get("story", ""),
            "choices": result.get("choices", []),
            "scene": scene_obj,
            "state": _enrich_state(get_state(session_id), req.novel_id),
        }
    except HTTPException:
        raise
    except Exception as e:
        # LLM 超时/限流/JSON 非法等：返回明确错误，前端 toast 展示，不留无提示 500
        logger.exception("游戏初始化失败: novel_id=%s", req.novel_id)
        raise HTTPException(
            status_code=502,
            detail=f"开场生成失败（{type(e).__name__}），请检查网络或稍后重试",
        )


@router.post("/resume")
async def resume_game(req: ResumeRequest):
    """恢复已有会话：从快照还原内存状态"""
    snap = load_session(req.session_id)
    if not snap:
        raise HTTPException(status_code=404, detail=f"会话不存在: {req.session_id}")

    novel_id = snap["novel_id"]
    game_state = snap["game_state"]
    short_mem = snap.get("short_term_memory") or []
    # 快照损坏时降级为空历史，避免脏数据拖垮恢复流程
    if not isinstance(short_mem, list):
        short_mem = []

    # 还原到内存
    restore_session(req.session_id, game_state, short_mem)

    # 返回恢复结果
    state = get_state(req.session_id)
    # 取短期记忆里最后一轮 DM 输出作为"上一段剧情"展示
    last_turn = short_mem[-1] if short_mem and isinstance(short_mem[-1], dict) else {}
    last_story = last_turn.get("dm", "") or ""

    # 再问 DM 一次获取当前的 choices（因为存快照时没存 choices）
    client = get_llm_client()
    try:
        from agents.dm import _build_prompt
        # 构造一个"继续"提示让 DM 给出当前可行选项
        cont_response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": DM_SYSTEM},
                {"role": "user", "content": _build_prompt(req.session_id, novel_id,
                    f"（玩家继续，给出{len(state.triggered_events)}个关键事件已发生：{state.triggered_events or '刚开始'}）请给出2-4个当前可选的下一步行动建议。")},
            ],
            response_format={"type": "json_object"},
            max_tokens=LLM_MAX_TOKENS,
        )
        cont_result = json.loads(cont_response.choices[0].message.content)
        choices = cont_result.get("choices", [])
    except Exception:
        # 恢复会话时拿不到选项不算故障：返回空数组，前端有自由输入框可兜住
        logger.warning("恢复会话时生成选项失败，返回空选项")
        choices = []

    return {
        "session_id": req.session_id,
        "novel_id": novel_id,
        "resumed": True,
        "last_story": str(last_story)[-300:] if last_story else "",
        "choices": choices,
        "state": _enrich_state(state, novel_id),
    }


@router.get("/sessions/{novel_id}")
async def get_sessions(novel_id: str):
    """获取某本小说下的所有会话"""
    return {"novel_id": novel_id, "sessions": list_sessions_for_novel(novel_id)}


@router.post("/action")
async def player_action(req: ActionRequest):
    """玩家动作，SSE流式返回（服务端分块，无需第二次LLM调用）"""

    def event_stream():
        def sse(obj):
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        try:
            # 记录旧位置：DM 在推送结果之后才更新状态，
            # 据此判断 DM 是否真的切换了场景（同名不弹场景窗）
            old_location = ""
            try:
                old_location = get_state(req.session_id).player_location
            except Exception:
                logger.exception("读取旧场景失败")

            npc_held = None   # NPC 台词延后到场景事件之后再推，保证弹窗先于一切语句
            npc_sent = False
            scene_emitted = False
            result_data = {}

            # 编排（Router →条件边→ NPC → DM）由 LangGraph 负责，
            # 这里只把图抛出的事件翻译成前端 SSE 协议
            for mode, chunk in stream_graph(req.session_id, req.novel_id, req.action):
                if mode == "updates":
                    # 节点增量产出形状为 {节点名: {字段: 值}}，DM 节点带回本轮完整结果
                    dm_update = chunk.get("dm") or {}
                    if "result" in dm_update:
                        result_data = dm_update["result"] or {}
                    continue

                ctype = chunk.get("type")
                if ctype == "npc":
                    npc_held = chunk
                    continue
                if ctype == "scene_change":
                    # dm_stream 在正文之前推送（已在 prompt 约束字段顺序 + 门控兜底）
                    sc = chunk.get("scene") or {}
                    name = str(sc.get("name") or "")
                    if name and name != old_location and not scene_emitted:
                        scene_emitted = True
                        yield sse({'type': 'scene_change',
                                   'name': name,
                                   'desc': str(sc.get("desc") or "")})
                elif ctype == "scene":
                    # 第一条正文之前补发 NPC 台词：弹窗 → 台词 → 叙事
                    if npc_held and not npc_sent:
                        yield sse(npc_held)
                        npc_sent = True
                    yield sse(chunk)
                else:
                    yield sse(chunk)

            # 无任何 scene 块时的最终兜底：NPC 台词不能丢
            if npc_held and not npc_sent:
                yield sse(npc_held)

            # choices 为空（JSON 被截断等）时如实下发空数组，由前端自由输入框兜住；
            # 不再伪造固定选项——那会把 DM 返回异常掩盖成"正常一轮"
            yield sse({'type': 'choices', 'options': result_data.get("choices") or []})

            state = get_state(req.session_id)
            yield sse({'type': 'state', 'state': _enrich_state(state, req.novel_id)})

            # === 自动保存 ===
            try:
                save_session(req.session_id, req.novel_id,
                             state.model_dump(), get_memory(req.session_id))
                update_session_meta(req.novel_id, req.session_id, req.action)
            except Exception as e:
                logger.warning("自动保存失败: %s", e)

            yield sse({'type': 'done'})

        except Exception as e:
            logger.exception("玩家动作处理异常")
            yield sse({'type': 'error', 'message': str(e)})
            yield sse({'type': 'done'})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
