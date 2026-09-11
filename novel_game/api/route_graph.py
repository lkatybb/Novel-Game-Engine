"""人物关系图接口"""

from fastapi import APIRouter
from config import RELATION_GRAPH_MIN_WEIGHT
from pipeline.character_extractor import get_graph_data, get_key_events, get_npc_profile
from memory.long_term import retrieve

router = APIRouter(prefix="/api/novel", tags=["graph"])


def _visible_graph(graph: dict) -> dict:
    """过滤关系图展示数据，并移除指向隐藏节点的边。"""
    nodes = [
        node for node in graph.get("nodes", [])
        if isinstance(node.get("weight"), (int, float))
        and node["weight"] >= RELATION_GRAPH_MIN_WEIGHT
    ]
    visible_ids = {node.get("id") for node in nodes}
    links = [
        link for link in graph.get("links", [])
        if link.get("source") in visible_ids and link.get("target") in visible_ids
    ]
    return {"nodes": nodes, "links": links}


@router.get("/{novel_id}/graph")
async def get_graph(novel_id: str):
    return _visible_graph(get_graph_data(novel_id))


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
