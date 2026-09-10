"""人物提取：LLM从小说中提取人物关系JSON + NPC人设档案"""

import json
import logging
from pathlib import Path
from config import LLM_MODEL, get_llm_client, DATA_DIR

logger = logging.getLogger(__name__)

_CACHE_DIR = DATA_DIR / "character_cache"

# 内存缓存（优先查），miss 时查磁盘缓存文件
_cache: dict = {}


def _cache_path(novel_id: str) -> Path:
    return _CACHE_DIR / f"{novel_id}_characters.json"


def _load_from_disk(novel_id: str) -> dict | None:
    p = _cache_path(novel_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_to_disk(novel_id: str, data: dict):
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(novel_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _llm_call(system_prompt: str, user_prompt: str) -> str:
    """调用DeepSeek API"""
    client = get_llm_client()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=4096,
    )
    return response.choices[0].message.content


def extract_characters(novel_id: str, novel_text: str, max_chars: int = 8000) -> dict:
    """
    从小说文本中提取人物关系和NPC人设

    Args:
        novel_id: 小说ID
        novel_text: 小说全文（或前N字）
        max_chars: 最多分析的字符数

    Returns:
        {
            "graph": {"nodes": [...], "links": [...]},
            "npc_profiles": {"角色名": {personality, secret, speech_style, ...}, ...}
        }
    """
    # 检查缓存（内存 → 磁盘）
    if novel_id in _cache:
        return _cache[novel_id]
    disk_data = _load_from_disk(novel_id)
    if disk_data:
        _cache[novel_id] = disk_data
        logger.info("从磁盘加载人物缓存: novel_id=%s", novel_id)
        return disk_data

    # 截取前N字（人物通常在开头出场）
    text_sample = novel_text[:max_chars]

    # ---- 第一步：提取人物关系图 ----
    graph_prompt = """你是一个小说分析专家。分析以下小说文本，提取所有出现的人物，以及人物之间的关系。

输出JSON格式：
{
  "nodes": [
    {"id": "人物名", "weight": 1-100的数字表示戏份重要性, "group": "主角"|"配角"|"配角团"}
  ],
  "links": [
    {"source": "人物A", "target": "人物B", "relation": "关系描述（如师徒、父子、敌对）", "type": "亲情"|"师徒"|"敌对"|"感情"|"盟友"|"主仆"}
  ]
}

注意：
- weight根据出场频率和重要性打分
- 关系type用于后续分类筛选
- 只提取有名字的角色，不要提取泛称"""

    logger.info("开始提取人物关系...")
    graph_result = _llm_call(graph_prompt, text_sample)
    graph_data = json.loads(graph_result)

    # ---- 第二步：提取NPC人设档案 ----
    character_names = [n["id"] for n in graph_data.get("nodes", [])][:10]  # 最多取前10个角色

    npc_prompt = f"""你是小说分析专家。根据以下小说文本，为这些角色生成人设档案。

角色列表：{", ".join(character_names)}

为每个角色输出JSON：
{{
  "角色名": {{
    "personality": "性格特点描述（2-3句话）",
    "secret": "角色的秘密或隐藏背景（如有，没有则写'无'）",
    "speech_style": "说话风格描述（如：文雅、粗犷、口语化、自称XX）",
    "goal": "角色的核心目标或动机"
  }}
}}"""

    logger.info("开始提取NPC人设档案...")
    npc_result = _llm_call(npc_prompt, text_sample)
    npc_profiles = json.loads(npc_result)

    # ---- 第三步：提取关键事件清单（硬锁机制） ----
    key_events_prompt = """你是小说分析专家。分析以下小说文本，提取出**必须发生**的关键事件——这些事件是故事主线的骨架，无论玩家怎么行动，这些事件都必须在正确的时机发生。

判断标准：
- 去掉了这个事件，故事主线就不成立了
- 主角命运发生根本变化的时刻（如拜师、获得能力、遇到关键NPC）
- 标志性情节（如三打白骨精、大闹天宫、取经出发）
- 重要NPC的核心命运节点

不要提取：日常对话、琐碎互动、非主线支线。

输出JSON格式：
{
  "events": [
    {
      "event_name": "简短事件名（如'拜师菩提'）",
      "trigger_condition": "触发条件（如'悟空在斜月三星洞待了一段时间，祖师要教他真本事时'）",
      "order": 1,
      "key_characters": ["悟空", "菩提祖师"]
    }
  ]
}

要求：
- order 按事件在故事中**必须发生的先后顺序**编号（1=最先发生）
- 提取 5-15 个关键事件，不要太多也不要太少
- event_name 用简洁的中文短语，后续会直接用作 DM Prompt 中的事件标识"""

    logger.info("开始提取关键事件清单...")
    events_result = _llm_call(key_events_prompt, text_sample)
    key_events = json.loads(events_result).get("events", [])
    # 确保每个事件都有必要字段
    for e in key_events:
        e.setdefault("order", 999)
        e.setdefault("trigger_condition", "")
        e.setdefault("key_characters", [])

    # 缓存（内存 + 磁盘）
    result = {
        "graph": graph_data,
        "npc_profiles": npc_profiles,
        "key_events": key_events,
    }
    _cache[novel_id] = result
    _save_to_disk(novel_id, result)

    logger.info("人物提取完成: %d 个人物, %d 条关系, %d 个关键事件",
                len(graph_data.get("nodes", [])),
                len(graph_data.get("links", [])),
                len(key_events))

    return result


def _ensure_loaded(novel_id: str) -> bool:
    """确保缓存已就绪：内存 miss 时回退加载磁盘缓存（服务重启后内存为空）"""
    if novel_id in _cache:
        return True
    disk_data = _load_from_disk(novel_id)
    if disk_data:
        _cache[novel_id] = disk_data
        logger.info("从磁盘恢复人物缓存: novel_id=%s", novel_id)
        return True
    return False


def get_graph_data(novel_id: str) -> dict:
    """获取人物关系图数据（用于D3.js力导图）"""
    if not _ensure_loaded(novel_id):
        return {"nodes": [], "links": []}
    return _cache[novel_id].get("graph", {"nodes": [], "links": []})


def get_npc_profile(novel_id: str, character_name: str) -> dict:
    """
    获取指定NPC的人设档案

    Returns:
        {personality, secret, speech_style, goal} 或空dict
    """
    if not _ensure_loaded(novel_id):
        return {}
    profiles = _cache[novel_id].get("npc_profiles", {})
    return profiles.get(character_name, {})


def get_all_npcs(novel_id: str) -> dict:
    """获取所有NPC人设档案"""
    if not _ensure_loaded(novel_id):
        return {}
    return _cache[novel_id].get("npc_profiles", {})


def get_key_events(novel_id: str) -> list[dict]:
    """
    获取关键事件清单（硬锁机制使用）

    Returns:
        [{"event_name": "...", "trigger_condition": "...", "order": 1, "key_characters": [...]}, ...]
    """
    if not _ensure_loaded(novel_id):
        return []
    events = _cache[novel_id].get("key_events", [])
    # 过滤掉 LLM 漏抽 event_name 的残缺项，避免下游 KeyError
    return [e for e in events if e.get("event_name")]
