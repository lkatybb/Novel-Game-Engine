"""人物关系图接口"""

from fastapi import APIRouter
from pipeline.character_extractor import get_graph_data

router = APIRouter(prefix="/api/novel", tags=["graph"])


@router.get("/{novel_id}/graph")
async def get_graph(novel_id: str):
    return get_graph_data(novel_id)
