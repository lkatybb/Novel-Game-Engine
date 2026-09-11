"""小说解析：TXT文件 → 章节切片 → ChromaDB入库"""

import re
import uuid
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.errors import NotFoundError

from config import CHROMA_DIR, CHUNK_SIZE, CHUNK_OVERLAP, NOVELS_DIR, get_embedding_function
from utils import read_text_auto

logger = logging.getLogger(__name__)


# 模块级单例
_client: Optional[chromadb.ClientAPI] = None


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _client


def _split_text(text: str) -> list[str]:
    """将小说文本按段落切片，每片CHUNK_SIZE字，重叠CHUNK_OVERLAP字"""
    # 先按空行分段
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        return []

    # 合并过短的段落，切分过长的段落
    chunks = []
    buffer = ""
    for para in paragraphs:
        # 如果单段超过CHUNK_SIZE，直接切
        if len(para) > CHUNK_SIZE:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            for i in range(0, len(para), CHUNK_SIZE - CHUNK_OVERLAP):
                chunk = para[i:i + CHUNK_SIZE]
                if len(chunk) > 50:  # 过滤太短的碎片
                    chunks.append(chunk)
            continue

        # 合并到buffer
        if len(buffer) + len(para) > CHUNK_SIZE:
            chunks.append(buffer)
            # 保留重叠部分
            buffer = buffer[-CHUNK_OVERLAP:] + para
        else:
            buffer = buffer + "\n" + para if buffer else para

    if buffer:
        chunks.append(buffer)

    return chunks


def ingest(novel_file: str, on_progress: Callable[[int, int], None] | None = None) -> dict:
    """
    读取小说文件，切片，Embedding入ChromaDB

    Args:
        novel_file: 文件名（在data/novels/目录下）或绝对路径
        on_progress: 每批入库后回调 (已完成批数, 总批数)；不传则不回调

    Returns:
        {novel_id, chunk_count, title}
    """
    # 定位文件
    file_path = Path(novel_file)
    if not file_path.is_absolute():
        file_path = NOVELS_DIR / novel_file
    if not file_path.exists():
        raise FileNotFoundError(f"小说文件不存在: {file_path}")

    # 读取文本（自动检测编码，兼容UTF-8和GBK）
    text = read_text_auto(file_path)
    title = file_path.stem

    # 切片
    chunks = _split_text(text)
    logger.info("小说《%s》切分为 %d 段", title, len(chunks))

    if not chunks:
        raise ValueError("小说内容为空或切片失败")

    # 生成novel_id
    novel_id = f"novel_{uuid.uuid4().hex[:8]}"

    # 入ChromaDB（使用中文Embedding模型 bge-base-zh，首次需下载模型）
    client = _get_client()
    collection = client.get_or_create_collection(
        name=f"{novel_id}_chapters",
        metadata={"hnsw:space": "cosine"},
        embedding_function=get_embedding_function(),
    )

    # 批量入库
    batch_size = 50
    batch_total = (len(chunks) + batch_size - 1) // batch_size
    for batch_no, i in enumerate(range(0, len(chunks), batch_size), start=1):
        batch = chunks[i:i + batch_size]
        collection.add(
            ids=[f"{novel_id}_chunk_{i + j}" for j in range(len(batch))],
            documents=batch,
            metadatas=[{"novel_id": novel_id, "chunk_index": i + j, "title": title} for j in range(len(batch))],
        )
        if on_progress:
            on_progress(batch_no, batch_total)

    logger.info("入库完成: novel_id=%s, %d段", novel_id, len(chunks))

    return {
        "novel_id": novel_id,
        "title": title,
        "chunk_count": len(chunks),
    }


def get_collection(novel_id: str) -> chromadb.Collection:
    """获取指定小说的ChromaDB集合（embedding函数必须与入库时一致）"""
    client = _get_client()
    return client.get_or_create_collection(
        name=f"{novel_id}_chapters",
        metadata={"hnsw:space": "cosine"},
        embedding_function=get_embedding_function(),
    )


def delete_collection(novel_id: str) -> bool:
    """删除小说的向量集合（唯一合法的删向量方式）。

    Returns:
        True  = 集合确实被删除
        False = 集合本就不存在（幂等，不报错）
    """
    try:
        _get_client().delete_collection(name=f"{novel_id}_chapters")
        return True
    except NotFoundError:
        return False
