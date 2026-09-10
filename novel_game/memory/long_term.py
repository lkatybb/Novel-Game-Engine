"""长期记忆：ChromaDB向量检索"""

import logging
from typing import Optional

from pipeline.novel_parser import get_collection
from config import RETRIEVAL_TOP_K

logger = logging.getLogger(__name__)


def retrieve(novel_id: str, query: str, top_k: int = RETRIEVAL_TOP_K) -> list[dict]:
    """
    从ChromaDB检索与玩家动作相关的小说段落

    Args:
        novel_id: 小说ID
        query: 检索文本（玩家动作或对话内容）
        top_k: 返回段落数

    Returns:
        [{"text": "段落内容", "chunk_index": 0, "distance": 0.3}, ...]
    """
    try:
        collection = get_collection(novel_id)
        results = collection.query(
            query_texts=[query],
            n_results=top_k,
        )

        if not results["documents"] or not results["documents"][0]:
            return []

        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results["distances"][0] if "distances" in results else [0] * len(documents)

        return [
            {
                "text": doc,
                "chunk_index": meta.get("chunk_index", 0),
                "distance": dist,
            }
            for doc, meta, dist in zip(documents, metadatas, distances)
        ]

    except Exception as e:
        logger.error("ChromaDB检索失败: %s", e)
        return []


def format_context(retrieved: list[dict]) -> str:
    """将检索结果格式化为Prompt可用的上下文文本"""
    if not retrieved:
        return "（无相关记忆）"

    parts = []
    for i, item in enumerate(retrieved, 1):
        parts.append(f"[原著片段{i}] {item['text'][:200]}...")

    return "\n".join(parts)
