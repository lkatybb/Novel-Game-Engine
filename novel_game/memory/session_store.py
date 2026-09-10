"""书架持久化：JSON 文件存储小说列表 + session 快照

data/
  bookshelf.json          # [{novel_id, filename, title, uploaded_at, sessions: [{session_id, last_action, updated_at}]}]
  sessions/
    sess_xxx.json         # {session_id, novel_id, game_state, short_term_memory, updated_at}
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from config import DATA_DIR, SESSIONS_DIR

logger = logging.getLogger(__name__)

BOOKSHELF_FILE = DATA_DIR / "bookshelf.json"


def _read_json(path: Path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("读取 %s 失败，返回默认值", path)
        return default


def _write_json(path: Path, data):
    """原子写入：先写临时文件再 replace，防止写入中途崩溃导致 JSON 损坏"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)   # 同卷原子替换，Windows/Linux 均支持


# ============ 书架元数据 ============

def add_novel(novel_id: str, filename: str, title: str = ""):
    """上传小说后记录元数据"""
    bookshelf = _read_json(BOOKSHELF_FILE, [])
    # 已存在就跳过（同一个 novel_id）
    if any(b["novel_id"] == novel_id for b in bookshelf):
        return
    bookshelf.append({
        "novel_id": novel_id,
        "filename": filename,
        "title": title or Path(filename).stem,
        "uploaded_at": datetime.now().isoformat(),
        "sessions": [],  # 后面 start_game 时往里加 session_id
    })
    _write_json(BOOKSHELF_FILE, bookshelf)


def list_novels() -> list[dict]:
    """返回书架列表（按上传时间倒序）"""
    bookshelf = _read_json(BOOKSHELF_FILE, [])
    bookshelf.sort(key=lambda b: b.get("uploaded_at", ""), reverse=True)
    return bookshelf


def get_novel_meta(novel_id: str) -> dict | None:
    """获取单本小说元数据"""
    for b in _read_json(BOOKSHELF_FILE, []):
        if b["novel_id"] == novel_id:
            return b
    return None


def add_session_to_novel(novel_id: str, session_id: str, last_action: str = ""):
    """start_game 时把新 session 挂到小说下"""
    bookshelf = _read_json(BOOKSHELF_FILE, [])
    for b in bookshelf:
        if b["novel_id"] == novel_id:
            # 避免重复
            if not any(s["session_id"] == session_id for s in b["sessions"]):
                b["sessions"].append({
                    "session_id": session_id,
                    "last_action": last_action,
                    "updated_at": datetime.now().isoformat(),
                })
            _write_json(BOOKSHELF_FILE, bookshelf)
            return


def update_session_meta(novel_id: str, session_id: str, last_action: str = ""):
    """每轮对话后更新 session 的最后活动时间"""
    bookshelf = _read_json(BOOKSHELF_FILE, [])
    for b in bookshelf:
        if b["novel_id"] == novel_id:
            for s in b["sessions"]:
                if s["session_id"] == session_id:
                    s["last_action"] = last_action[:60] if last_action else s.get("last_action", "")
                    s["updated_at"] = datetime.now().isoformat()
            _write_json(BOOKSHELF_FILE, bookshelf)
            return


def remove_session_from_novel(novel_id: str, session_id: str):
    """删除 session 时清理书架上的记录"""
    bookshelf = _read_json(BOOKSHELF_FILE, [])
    for b in bookshelf:
        if b["novel_id"] == novel_id:
            b["sessions"] = [s for s in b["sessions"] if s["session_id"] != session_id]
    _write_json(BOOKSHELF_FILE, bookshelf)


def remove_novel(novel_id: str) -> dict | None:
    """从书架删掉一本书，返回被删条目（含 filename / sessions，供调用方清理残留）

    Returns: 被删的书架条目；该书不在书架上时返回 None
    """
    bookshelf = _read_json(BOOKSHELF_FILE, [])
    removed = next((b for b in bookshelf if b["novel_id"] == novel_id), None)
    if removed is None:
        return None
    _write_json(BOOKSHELF_FILE, [b for b in bookshelf if b["novel_id"] != novel_id])
    return removed


# ============ Session 快照 ============

def save_session(session_id: str, novel_id: str, game_state: dict, short_term_memory: list[dict]):
    """保存 session 快照（每轮对话后调用）"""
    data = {
        "session_id": session_id,
        "novel_id": novel_id,
        "game_state": game_state,
        "short_term_memory": short_term_memory,
        "updated_at": datetime.now().isoformat(),
    }
    _write_json(SESSIONS_DIR / f"{session_id}.json", data)


def load_session(session_id: str) -> dict | None:
    """加载 session 快照"""
    return _read_json(SESSIONS_DIR / f"{session_id}.json", None)


def delete_session_file(session_id: str):
    """删除 session 文件"""
    p = SESSIONS_DIR / f"{session_id}.json"
    if p.exists():
        p.unlink()


def list_sessions_for_novel(novel_id: str) -> list[dict]:
    """获取某本小说下所有 session（按更新时间倒序）"""
    meta = get_novel_meta(novel_id)
    if not meta:
        return []
    sessions = sorted(meta.get("sessions", []),
                      key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions
