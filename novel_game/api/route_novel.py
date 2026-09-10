"""小说上传接口 + 书架列表"""

import logging
import uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File
from pipeline.novel_parser import ingest
from pipeline.character_extractor import extract_characters
from config import NOVELS_DIR
from utils import read_text_auto
from memory.session_store import add_novel, list_novels

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/novel", tags=["novel"])


@router.post("/upload")
async def upload_novel(file: UploadFile = File(...)):
    logger.info("上传请求: filename=%s, content_type=%s, size=%s",
                file.filename, file.content_type, file.size)

    # 1. 文件大小校验（10MB 上限）
    MAX_SIZE = 10 * 1024 * 1024
    if file.size and file.size > MAX_SIZE:
        return {"error": f"文件过大（{file.size} 字节），上限 {MAX_SIZE // 1024 // 1024}MB"}

    # 2. 文件类型校验（仅允许 .txt / .md）
    if file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in (".txt", ".md"):
            return {"error": f"不支持的文件类型 {ext}，仅允许 .txt / .md"}

    # 3. 读取内容
    await file.seek(0)
    content = await file.read()
    logger.info("读取到 %d 字节 (file.size=%s)", len(content), file.size)

    if not content:
        return {"error": "文件内容为空"}

    safe_name = f"novel_{uuid.uuid4().hex[:8]}.txt"
    file_path = NOVELS_DIR / safe_name
    file_path.write_bytes(content)
    logger.info("已保存到 %s", file_path)

    result = ingest(safe_name)

    # 人物提取为非关键路径：LLM 失败时降级（关系图/NPC人设不可用），不阻断上传
    try:
        extract_characters(result["novel_id"], read_text_auto(file_path))
    except Exception as e:
        logger.exception("人物提取失败，小说已上传但关系图/NPC人设暂不可用: %s", e)

    # 记录到书架
    original_title = file.filename.replace(".txt", "").replace(
        ".md", "") if file.filename else safe_name
    add_novel(result["novel_id"], safe_name, original_title)
    logger.info("已添加到书架: novel_id=%s, title=%s",
                result["novel_id"], original_title)

    return result


@router.get("/list")
async def get_bookshelf():
    """获取书架列表"""
    return {"novels": list_novels()}
