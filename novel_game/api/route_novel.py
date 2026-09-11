"""小说上传接口（异步导入 + 进度查询）+ 书架列表"""

import logging
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from api.import_jobs import create_job, snapshot, start
from pipeline.novel_parser import delete_collection
from pipeline.character_extractor import drop_cache
from config import NOVELS_DIR
from memory.global_state import drop_state
from memory.session_store import (
    add_novel, delete_session_file, get_novel_meta, list_novels, remove_novel,
)
from memory.short_term import drop as drop_memory
from memory.session_memory import drop as drop_session_memory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/novel", tags=["novel"])


@router.post("/upload")
async def upload_novel(file: UploadFile = File(...)):
    """上传小说文件 → 建导入任务 → 立即返回 job_id（入库/人物提取在后台线程跑）"""
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

    original_title = file.filename.replace(".txt", "").replace(
        ".md", "") if file.filename else safe_name

    # 任务只活在内存里且同一时刻只允许一个，未抢到名额的回 409（文件刚落盘，删掉）
    job_id = create_job(file.filename or safe_name, original_title)
    if job_id is None:
        file_path.unlink(missing_ok=True)
        return JSONResponse(status_code=409,
                            content={"error": "已有导入任务正在进行，请等它完成后再上传"})

    start(job_id, file_path)
    logger.info("已提交导入任务: job_id=%s, file=%s, title=%s",
                job_id, safe_name, original_title)
    return {"job_id": job_id, "status": "running"}


@router.get("/import/{job_id}")
async def get_import_job(job_id: str):
    """查询导入任务进度；任务只在内存，服务重启后一律 404"""
    job = snapshot(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"导入任务不存在: {job_id}")
    return job


@router.get("/list")
async def get_bookshelf():
    """获取书架列表"""
    return {"novels": list_novels()}


@router.delete("/{novel_id}")
async def delete_novel(novel_id: str):
    """删除一本小说：正文 + 向量库 + 人物缓存 + 全部存档 + 书架记录 + 内存状态。

    清理范围一律取自 get_novel_meta() 与 config 常量，不拼路径模板。
    小说不存在时返回 404，不静默成功。
    """
    entry = get_novel_meta(novel_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"小说不存在: {novel_id}")

    (NOVELS_DIR / entry["filename"]).unlink(missing_ok=True)
    vector_deleted = delete_collection(novel_id)
    drop_cache(novel_id)

    session_ids = [s["session_id"] for s in entry.get("sessions", [])]
    for sid in session_ids:
        delete_session_file(sid)
        drop_state(sid)
        drop_memory(sid)
        drop_session_memory(sid)

    remove_novel(novel_id)
    logger.info("已删除小说: novel_id=%s, 连带存档 %d 条, 向量集合删除=%s",
                novel_id, len(session_ids), vector_deleted)

    return {
        "deleted": novel_id,
        "vector_deleted": vector_deleted,
        "sessions_deleted": len(session_ids),
    }
