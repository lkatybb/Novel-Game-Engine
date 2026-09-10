"""人物关系图接口"""

from fastapi import APIRouter
from pipeline.character_extractor import get_graph_data, get_key_events, get_npc_profile
from memory.long_term import retrieve

router = APIRouter(prefix="/api/novel", tags=["graph"])


@router.get("/{novel_id}/graph")
async def get_graph(novel_id: str):
    return get_graph_data(novel_id)


@router.get("/{novel_id}/character/{name}")
async def get_character_codex(novel_id: str, name: str):
    """角色百科（只读）：人设档案 + 关联关键事件 + 原著片段出处"""
    events = [e for e in get_key_events(novel_id) if name in e.get("key_characters", [])]
    return {
        "name": name,
        "profile": get_npc_profile(novel_id, name),
        "events": events,
        "excerpts": retrieve(novel_id, name, top_k=3),
    }
