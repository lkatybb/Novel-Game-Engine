"""小说导入任务：把耗时的「向量入库 + 人物提取」挪到后台线程，接口立即返回 job_id。

上传接口是 async 函数，直接调同步的 ingest() 会占住事件循环——导入那几分钟里
整个后端（连书架列表）都不响应。这里用线程承接，接口只做校验和建任务。

任务只活在内存里：服务重启即失效，前端拿到 404 就提示重新上传（不落盘，
避免再造一批没人清理的孤儿数据）。本模块只被 api 层调用，不反向依赖 api.route_*。
"""

import logging
import threading
import uuid
from pathlib import Path

from memory.session_store import add_novel
from pipeline.character_extractor import extract_characters
from pipeline.novel_parser import ingest
from utils import read_text_auto

logger = logging.getLogger(__name__)

# 进度区间：向量入库 5→70%，人物提取 70→99%，完成 100%
_INGEST_PROGRESS_START = 5
_INGEST_PROGRESS_END = 70
_EXTRACT_PROGRESS_END = 99
_KEEP_FINISHED = 20          # 已结束任务的内存保留条数

JOBS: dict[str, dict] = {}
_lock = threading.Lock()
_running: str | None = None  # 同一时刻只允许一个导入任务（导入吃满 CPU，并发只会互相拖慢）


def create_job(filename: str, title: str) -> str | None:
    """建任务并占住名额；已有任务在跑时返回 None（调用方回 409）"""
    global _running
    with _lock:
        if _running is not None:
            return None
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        JOBS[job_id] = {
            "job_id": job_id, "status": "running", "stage": "排队中", "progress": 0,
            "novel_id": None, "chunk_count": 0, "filename": filename, "title": title,
            "error": None, "degraded": False, "degraded_reason": None,
        }
        _running = job_id
    return job_id


def start(job_id: str, file_path: Path) -> None:
    """起后台线程执行导入；daemon 保证服务退出时不被导入线程拖住"""
    threading.Thread(target=_worker, args=(job_id, file_path), daemon=True).start()


def snapshot(job_id: str) -> dict | None:
    """任务快照；未知 job_id 返回 None（调用方回 404）"""
    with _lock:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def _update(job_id: str, **fields) -> None:
    with _lock:
        JOBS[job_id].update(fields)


def _worker(job_id: str, file_path: Path) -> None:
    """后台线程主体：入库 → 人物提取 → 上架"""
    global _running
    title = JOBS[job_id]["title"]
    try:
        _update(job_id, stage="向量入库", progress=_INGEST_PROGRESS_START)
        result = ingest(
            file_path.name,
            on_progress=lambda done, total: _update(
                job_id, progress=_INGEST_PROGRESS_START + (
                    _INGEST_PROGRESS_END - _INGEST_PROGRESS_START) * done // total),
        )
        _update(job_id, stage="人物提取", progress=_INGEST_PROGRESS_END,
                novel_id=result["novel_id"], chunk_count=result["chunk_count"])

        # 人物提取为非关键路径：LLM 失败时降级（关系图/NPC人设不可用），不阻断导入
        try:
            extract_characters(
                result["novel_id"], read_text_auto(file_path),
                on_progress=lambda done, total: _update(
                    job_id, progress=_INGEST_PROGRESS_END + (
                        _EXTRACT_PROGRESS_END - _INGEST_PROGRESS_END) * done // total),
            )
        except Exception as e:
            logger.exception("人物提取失败，小说已入库但关系图/NPC人设暂不可用: %s", e)
            _update(job_id, degraded=True, degraded_reason=str(e))

        add_novel(result["novel_id"], file_path.name, title)
        logger.info("导入完成: job_id=%s, novel_id=%s, %d段",
                    job_id, result["novel_id"], result["chunk_count"])
        _update(job_id, status="done", stage="完成", progress=100)
    except Exception as e:
        logger.exception("导入失败: job_id=%s", job_id)
        _update(job_id, status="error", error=str(e))
    finally:
        with _lock:
            _running = None
            _trim_finished()


def _trim_finished() -> None:
    """只保留最近 _KEEP_FINISHED 条已结束任务（调用方须已持锁）"""
    finished = [jid for jid, job in JOBS.items() if job["status"] != "running"]
    for jid in finished[:-_KEEP_FINISHED]:
        del JOBS[jid]
