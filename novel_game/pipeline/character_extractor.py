"""人物提取：LLM从小说中提取人物关系JSON + NPC人设档案"""

import json
import logging
from collections.abc import Callable
from pathlib import Path

from config import (
    CHARACTER_CACHE_DIR,
    EXTRACT_MAX_EVENTS,
    EXTRACT_MAX_NODES,
    EXTRACT_SAMPLE_CHARS,
    EXTRACT_SEGMENT_CHARS,
    LLM_MODEL,
    get_llm_client,
)

logger = logging.getLogger(__name__)

# 内存缓存（优先查），miss 时查磁盘缓存文件
_cache: dict = {}


def _cache_path(novel_id: str) -> Path:
    return CHARACTER_CACHE_DIR / f"{novel_id}_characters.json"


def _load_from_disk(novel_id: str) -> dict | None:
    p = _cache_path(novel_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_to_disk(novel_id: str, data: dict):
    CHARACTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(novel_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def drop_cache(novel_id: str):
    """删除小说的人物缓存：内存条目 + 磁盘文件（不存在则无操作）"""
    _cache.pop(novel_id, None)
    _cache_path(novel_id).unlink(missing_ok=True)


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


def normalize_event_orders(events: list[dict]) -> list[dict]:
    """把关键事件的 order 归一化为稠密 1..N（按声明 order 稳定排序后重新编号）。

    硬锁机制的门槛是 order <= max(已触发)+1（见 memory/global_state._accept_triggered），
    隐含假设 order 稠密。LLM 可能跳号（如 1/3/5）或缺字段（按末位处理，与原
    setdefault 999 语义一致），此时第一个未触发事件会被永久拒绝、时间线静默冻结。

    Returns: 新列表（不修改入参），每项带 order = 1..N
    """
    ordered = sorted(events, key=lambda e: e.get("order", 999))
    return [dict(e, order=i) for i, e in enumerate(ordered, start=1)]


_STAT_COUNT = 3            # 本书主角属性维度数量（多了顶栏与 Prompt 都吃不消）
_STAT_INIT_DEFAULT = 50    # init 缺失/非法时的中位起点（与"新角色好感度 50"同口径）


def _extract_player_stats(text_sample: str, protagonist: str) -> list[dict]:
    """第四步：提取本书主角的属性维度（该书的"智商/灵力/人脉"等）。

    属性由书决定，不是固定的四维：修仙文得「灵力/境界」，豪门文得「财富/人脉」，
    悬疑文得「线索/胆识」。非关键路径——失败返回空列表，游戏照常跑（属性面板留空），
    绝不用固定维度兜底。

    Returns: [{"name": str, "desc": str, "init": int 0~100}, ...]（最多 _STAT_COUNT 项）
    """
    who = f"主角「{protagonist}」" if protagonist else "主角"
    prompt = f"""你是小说分析专家。为这本小说的{who}设计 {_STAT_COUNT} 项可量化的属性。

要求：
- 必须贴合本书题材与主角身份（修仙文的「灵力/境界」，豪门文的「财富/人脉」，悬疑文的「线索/胆识」）
- 每项 2-4 个汉字，互不重复，能随剧情涨落
- init 是故事开头该属性的初始值（0~100 的整数）

输出JSON格式：
{{
  "stats": [
    {{"name": "属性名", "desc": "一句话说明（10-20字）", "init": 20}}
  ]
}}"""

    try:
        stats = json.loads(_llm_call(prompt, text_sample)).get("stats", [])
    except Exception as e:
        logger.warning("主角属性提取失败，本作不启用属性面板: %s", e)
        return []

    cleaned = []
    for s in stats if isinstance(stats, list) else []:
        if not isinstance(s, dict) or not str(s.get("name", "")).strip():
            continue
        try:
            init = int(s.get("init", _STAT_INIT_DEFAULT))
        except (TypeError, ValueError):
            init = _STAT_INIT_DEFAULT
        cleaned.append({
            "name": str(s["name"]).strip(),
            "desc": str(s.get("desc", "")).strip(),
            "init": max(0, min(100, init)),
        })
        if len(cleaned) >= _STAT_COUNT:
            break
    return cleaned


_EVENT_QUOTA_MAX = 15   # 单段事件条数上限（= 旧口径的"5-15 个"，单段书与旧行为一致）
_EVENT_QUOTA_MIN = 3    # 单段事件条数下限（段数再多也要给每段留出记录骨架的机会）
_PROFILE_LIMIT = 10     # 每段最多生成人设的角色数（= 旧口径的"最多取前10个"）


def _split_segments(text: str) -> list[str]:
    """按 EXTRACT_SEGMENT_CHARS 把全文切段；不足一段就是一段（单段书与旧口径一致）"""
    if len(text) <= EXTRACT_SEGMENT_CHARS:
        return [text]
    return [text[i:i + EXTRACT_SEGMENT_CHARS]
            for i in range(0, len(text), EXTRACT_SEGMENT_CHARS)]


def _sample_of(block: str) -> str:
    """取段首 EXTRACT_SAMPLE_CHARS 字做样本。

    覆盖度靠"每段都抽"，不靠"每段抽更多"：样本长度必须等于旧口径的 8000 字，
    否则单段书的结果会跟着变。
    """
    return block[:EXTRACT_SAMPLE_CHARS]


def _event_quota(segment_count: int) -> int:
    """每段的事件条数上限：全书目标量按段数均分，再夹到 [_EVENT_QUOTA_MIN, _EVENT_QUOTA_MAX]。

    单段书 = 15（与旧口径一致）；段数越多每段越少，全书总量不随篇幅线性膨胀。
    """
    return min(_EVENT_QUOTA_MAX,
               max(_EVENT_QUOTA_MIN, -(-EXTRACT_MAX_EVENTS // segment_count)))


def _weight_of(node: dict) -> float:
    """节点戏份权重：prompt 约定为 1-100 的数字，非数字按 0 处理（等价于最不重要）。

    必须按数值处理：截断与主角唯一化都拿它排序，把 88.5 这种小数当成 0 会让
    重要节点被误截掉。
    """
    weight = node.get("weight", 0)
    return weight if isinstance(weight, (int, float)) else 0


def _apply_protagonist_rule(nodes: list[dict], allow_protagonist: bool) -> str:
    """按主角口径就地改写 group，返回本段主角名（无则空串）。

    主角是玩家的身份来源，"玩家是谁"只能有一个答案：图上出现两个「主角」，
    NPC 台词与私聊拦截就会打架。
    - allow_protagonist=False（第 1 段之后）：本段所有「主角」降级为「配角」。
      主角只在初次登场那一段声明；后面的段落把主角和旁人混着标，再声明一次
      就会在合并后的图上多出一个同名主角。
    - allow_protagonist=True（第 1 段）：最多只留一个「主角」，取 weight 最高的那个，
      其余降级为「配角」（LLM 常把双主角都标成主角）。
    """
    leads = [n for n in nodes if n.get("id") and n.get("group") == "主角"]
    if not allow_protagonist:
        for n in leads:
            n["group"] = "配角"
        return ""
    if not leads:
        return ""
    keep = max(leads, key=_weight_of)
    for n in leads:
        if n is not keep:
            n["group"] = "配角"
    return str(keep["id"])


def _extract_graph(sample: str) -> dict:
    """第一步：提取人物关系图（nodes + links）"""
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
    graph_data = json.loads(_llm_call(graph_prompt, sample))
    return {
        "nodes": graph_data.get("nodes", []),
        "links": graph_data.get("links", []),
    }


def _extract_profiles(sample: str, names: list[str]) -> dict:
    """第二步：为本段出现的角色生成人设档案；名单为空则不发调用（省一次无用开销）"""
    if not names:
        return {}
    npc_prompt = f"""你是小说分析专家。根据以下小说文本，为这些角色生成人设档案。

角色列表：{", ".join(names)}

为每个角色输出JSON：
{{
  "角色名": {{
    "personality": "性格特点描述（2-3句话）",
    "secret": "角色的秘密或隐藏背景（如有，没有则写'无'）",
    "speech_style": "说话风格描述（如：文雅、粗犷、口语化、自称XX）",
    "goal": "角色的核心目标或动机"
  }}
}}"""

    logger.info("开始提取NPC人设档案（本段 %d 个角色）...", len(names))
    return json.loads(_llm_call(npc_prompt, sample))


def _extract_events(sample: str, quota: int) -> list[dict]:
    """第三步：提取关键事件清单（硬锁机制）。

    quota 是本段的事件条数上限：单段书 = 15（与旧口径一致），段数越多每段越少。
    order 在本段内成立即可，跨段的全局顺序由 _merge_segment_events 重新编号。
    """
    count_rule = ("提取 5-15 个关键事件，不要太多也不要太少" if quota >= _EVENT_QUOTA_MAX
                  else f"提取不超过 {quota} 个关键事件——本段只是全书的一部分，只挑真正的骨架")
    key_events_prompt = f"""你是小说分析专家。分析以下小说文本，提取出**必须发生**的关键事件——这些事件是故事主线的骨架，无论玩家怎么行动，这些事件都必须在正确的时机发生。

判断标准：
- 去掉了这个事件，故事主线就不成立了
- 主角命运发生根本变化的时刻（如拜师、获得能力、遇到关键NPC）
- 标志性情节（如三打白骨精、大闹天宫、取经出发）
- 重要NPC的核心命运节点

不要提取：日常对话、琐碎互动、非主线支线。

输出JSON格式：
{{
  "events": [
    {{
      "event_name": "简短事件名（如'拜师菩提'）",
      "trigger_condition": "触发条件（如'悟空在斜月三星洞待了一段时间，祖师要教他真本事时'）",
      "order": 1,
      "key_characters": ["悟空", "菩提祖师"]
    }}
  ]
}}

要求：
- order 按事件在本段中**必须发生的先后顺序**编号（1=本段最先发生）
- {count_rule}
- event_name 用简洁的中文短语，后续会直接用作 DM Prompt 中的事件标识"""

    logger.info("开始提取关键事件清单（本段上限 %d 条）...", quota)
    events = json.loads(_llm_call(key_events_prompt, sample)).get("events", [])
    # 补齐必要字段；order 交给 normalize_event_orders 归一化为稠密 1..N（硬锁门槛依赖稠密序）
    for e in events:
        e.setdefault("trigger_condition", "")
        e.setdefault("key_characters", [])
    return events


def _merge_graph(graphs: list[dict]) -> dict:
    """合并各段关系图：节点按 id 去重（weight 取最大、group 以首段为准），
    边按 (source, target) 去重（先出现者胜：同一对人物只留一条关系），
    最后按 weight 降序截断到 EXTRACT_MAX_NODES。

    被截断的节点，它的边一并丢弃——D3 的 forceLink 在图上找不到端点会整张图渲染失败。
    """
    nodes: dict[str, dict] = {}
    links: list[dict] = []
    seen_links: set[tuple[str, str]] = set()

    for graph in graphs:
        for n in graph.get("nodes", []):
            node_id = str(n.get("id", "")).strip()
            if not node_id:
                continue
            if node_id in nodes:
                nodes[node_id]["weight"] = max(nodes[node_id]["weight"], _weight_of(n))
            else:
                nodes[node_id] = {"id": node_id, "weight": _weight_of(n),
                                  "group": n.get("group") or ""}
        for link in graph.get("links", []):
            source = str(link.get("source", "")).strip()
            target = str(link.get("target", "")).strip()
            if not source or not target or (source, target) in seen_links:
                continue
            seen_links.add((source, target))
            links.append({"source": source, "target": target,
                          "relation": link.get("relation", ""), "type": link.get("type", "")})

    kept = sorted(nodes.values(), key=lambda n: (-n["weight"], n["id"]))[:EXTRACT_MAX_NODES]
    alive = {n["id"] for n in kept}
    return {"nodes": kept,
            "links": [l for l in links if l["source"] in alive and l["target"] in alive]}


def _merge_profiles(chunks: list[dict]) -> dict:
    """合并各段人设档案：同一角色多段都写了 → 最早出现的段胜出。

    人设是"人物初次登场时的设定"，后段再写一遍多半是顺着剧情重述，越靠后越可能
    掺进剧透（secret 字段尤其危险）。
    """
    merged: dict = {}
    for profiles in chunks:
        for name, profile in profiles.items():
            merged.setdefault(name, profile)
    return merged


def _merge_segment_events(chunks: list[list[dict]]) -> list[dict]:
    """按段序拼接事件并稠密重编号 1..N。

    LLM 每段都从 order=1 开始编号，直接拼会互相插队；所以先理清段内顺序，再按段序
    统一编号，最终 order 与"故事里第几段发生"一致。
    """
    merged: list[dict] = []
    for events in chunks:
        for e in normalize_event_orders(events):
            merged.append(dict(e, order=len(merged) + 1))
    return merged


def extract_characters(novel_id: str, novel_text: str,
                       on_progress: Callable[[int, int], None] | None = None) -> dict:
    """从小说全文中提取人物关系、NPC人设、关键事件与主角属性。

    大文件按 EXTRACT_SEGMENT_CHARS 分段，每段只取段首 EXTRACT_SAMPLE_CHARS 字做样本：
    只吃全书开头的话，100 万字的书有 99.2% 的内容永远进不了人物图。分段后每段都有
    自己的样本，中后段才出场的角色同样能进图。

    Args:
        novel_id: 小说ID
        novel_text: 小说全文
        on_progress: 每完成一段回调 (done, total)

    Returns:
        {
            "graph": {"nodes": [...], "links": [...]},
            "npc_profiles": {"角色名": {personality, secret, speech_style, ...}, ...},
            "key_events": [{"event_name", "trigger_condition", "order", "key_characters"}, ...],
            "player_stats": [{"name", "desc", "init"}, ...]
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

    segments = _split_segments(novel_text)
    quota = _event_quota(len(segments))
    logger.info("人物提取分段：%d 段，每段样本 %d 字，每段事件上限 %d 条",
                len(segments), EXTRACT_SAMPLE_CHARS, quota)

    graphs: list[dict] = []
    profile_chunks: list[dict] = []
    event_chunks: list[list[dict]] = []
    protagonist = ""

    for index, block in enumerate(segments):
        sample = _sample_of(block)

        graph = _extract_graph(sample)
        # 主角只在第 1 段声明，且第 1 段内也只留一个；必须在合并前就地改好
        lead = _apply_protagonist_rule(graph["nodes"], allow_protagonist=index == 0)
        if index == 0:
            protagonist = lead
        graphs.append(graph)

        # 人设名单用本段自己的角色，去掉主角（主角由玩家扮演，不需要人设档案）
        names = [n["id"] for n in graph["nodes"]
                 if n.get("id") and n.get("group") != "主角"][:_PROFILE_LIMIT]
        profile_chunks.append(_extract_profiles(sample, names))
        event_chunks.append(_extract_events(sample, quota))

        if on_progress:
            on_progress(index + 1, len(segments))

    graph_data = _merge_graph(graphs)
    npc_profiles = _merge_profiles(profile_chunks)
    key_events = _merge_segment_events(event_chunks)

    # ---- 第四步：提取本书主角的属性维度（属性由书决定，非固定四维；只用开头那段样本） ----
    logger.info("开始提取主角属性维度...")
    player_stats = _extract_player_stats(_sample_of(segments[0]), protagonist)

    # 缓存（内存 + 磁盘）
    result = {
        "graph": graph_data,
        "npc_profiles": npc_profiles,
        "key_events": key_events,
        "player_stats": player_stats,
    }
    _cache[novel_id] = result
    _save_to_disk(novel_id, result)

    logger.info("人物提取完成: %d 个人物, %d 条关系, %d 个关键事件, %d 项主角属性",
                len(graph_data["nodes"]),
                len(graph_data["links"]),
                len(key_events),
                len(player_stats))

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


def get_protagonist_names(novel_id: str) -> list[str]:
    """本作主角名列表（图里 group == "主角" 的节点），缓存缺失时为空列表。

    主角由玩家自己扮演，是"玩家身份"的唯一来源：既用于告诉 NPC 玩家是谁，
    也用于禁止把主角本人当成可对话的 NPC。
    """
    if not _ensure_loaded(novel_id):
        return []
    nodes = _cache[novel_id].get("graph", {}).get("nodes", [])
    return [n["id"] for n in nodes if n.get("id") and n.get("group") == "主角"]


def is_protagonist(novel_id: str, name: str) -> bool:
    """name 是否指本作主角。

    兼容 LLM 用正文简称回填的情况：图里是「孙悟空」，Router 会回「悟空」。
    """
    return bool(name) and any(name in p for p in get_protagonist_names(novel_id))


def get_player_stats(novel_id: str) -> list[dict]:
    """本书主角的属性维度（name/desc/init），老书或提取失败时为空列表。

    空列表是合法结果：属性功能整体关闭（面板留空、游戏照常跑），不临时造一套维度。
    """
    if not _ensure_loaded(novel_id):
        return []
    return [s for s in _cache[novel_id].get("player_stats", [])
            if isinstance(s, dict) and s.get("name")]


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
