# -*- coding: utf-8 -*-
"""TU-10a｜SSE / state 接口契约测试（零依赖：仅标准库）

用途：为 TU-1（剧透收敛）提供可执行的接口契约断言，先红灯后转绿。
约束：不引入任何第三方依赖、不使用 pytest —— 纯 assert + sys.exit(0/1)。

运行前提：8888 端口已启动服务
    cd e:\\小说\\novel_game
    python -m uvicorn api.main:app --port 8888
运行方式：
    python test_contract.py

验证层次（环境变量 CONTRACT_SCOPE，默认 full）——快层只是"不调用"，不做简化断言：
    full   A9/A10/A11 + PRE-1~3 + A6 + A1-A5/A8 + A7 + B9-B12 + B0-B7/B13/B6a/B6b + B8
    delete A9/A10/A11 + PRE-1~3 + B9-B12 + B0-B7/B13/B6a/B6b
           （跳过 A6、/action 组、/resume 的 A7、以及最贵的 B8 重传）
    非法取值以 exit 2 报错退出，不静默回落 full。

检查项：
    A9     离线确定性断言：直调 _enrich_state，锁死 order 不连续 / 缺 order 的事件清单
           （不依赖服务，故先于网络检查执行）
    A10    离线确定性断言：关键事件 order 在提取出口归一化为稠密 1..N，
           且归一化后的清单能通过硬锁门槛
    A11    离线双向断言：DM 自由文本容器命中事件名 → A4 必须通过（Mask 生效）；
           结构性字段泄露未触发事件名 → A4 必须失败（鉴别力保留）
    PRE-1  测试样本文件存在
    PRE-2  契约测试目标服务可达（地址取 CONTRACT_BASE，默认 8888）
    PRE-3  上传后能取到非空的关键事件清单（A4/A8 的判定依据）
    A1     SSE 事件类型集合 ⊆ 协议白名单
    A2     SSE 必含 {stage, scene, choices, state, done}
    A3     /action 整条 SSE 报文中 "trigger_condition" 出现 0 次
    A4     未触发事件名收敛：结构性字段里恰好露 1 个（DM 自由文本容器已 Mask）
    A5     SSE state 含 triggered / next_event / total，next_event 仅含 event_name+order
    A6     /start 的 state 满足 A5 + A8 口径（TU-1 三挂载点一致性）
    A7     /resume 的 state 满足 A5 + A8 口径（TU-1 三挂载点一致性）
    A8     存在未触发事件时 next_event 不得为 null，且是未触发中 order 最小者；
           未触发为空时 next_event 必须为 null
    B0/B1-B5/B7  TU-5 删小说残留：素材就位、正文、人物缓存、该校全部存档快照、书架、chroma 集合
    B6a    离线单元：drop_cache / drop_state / short_term.drop 清内存三容器
    B6b    在线代理：被删小说的 /graph 返回空；被连带删除的存档发 /action 收到 error 帧
    B2/B13 TU-5 删小说返回 vector_deleted=True；重复删除 404
    B8     删除后重新上传同一本小说 -> 能入库 + 能开局（并顺手删掉，兼作端到端复验）
    B9-B12 TU-4 删存档：200 / 快照消失 / 书架不含该 sid 且同书其他存档仍在 / 重复删除 404
           / 删除后 resume 404

退出码：全部通过 0；任一失败 1（失败项在末尾汇总）
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# 服务地址：默认 8888（M2/M3 复审位），可用环境变量覆盖，如
#   $env:CONTRACT_BASE='http://127.0.0.1:8889'
BASE = os.environ.get("CONTRACT_BASE", "http://127.0.0.1:8888")
ROOT = Path(__file__).resolve().parent

# 验证层次（沿用 CONTRACT_BASE 的环境变量先例）：
#   full   = 默认，行为与引入分层前完全一致：A9/A10/A11 + PRE-1~3 + A6 + A1-A5/A8
#            + A7 + B9-B12 + B0-B7/B13/B6a/B6b + B8
#   delete = 删除能力快层（迭代替换用）：A9/A10/A11 + PRE-1~3 + B9-B12
#            + B0-B7/B13/B6a/B6b，跳过 A6、/action 组（A1-A5/A8）、/resume 的 A7 与最贵的 B8
# 两层共用同一套断言函数、同一个 check() 口径；快层只是"不调用"，不是"简化断言"。
SCOPE = os.environ.get("CONTRACT_SCOPE", "full")
if SCOPE not in ("delete", "full"):
    print(f"[FATAL] CONTRACT_SCOPE 取值非法: {SCOPE!r}，只允许 delete | full")
    sys.exit(2)
SAMPLE = Path(__file__).resolve().parent / "data" / "novels" / "西游记-样本.txt"

# SSE 协议白名单 / 必含事件（API 契约，不得静默变化）
ALLOWED_EVENTS = {"stage", "scene_change", "scene", "npc", "choices", "state", "error", "done"}
REQUIRED_EVENTS = {"stage", "scene", "choices", "state", "done"}

# state 里由 DM 自由填写文本的容器：事件名可能被写进位置/见闻/行囊，
# A4 断言前必须 Mask 掉，否则模型措辞一变就随机变红（不可作为回归闸门）
DM_TEXT_FIELDS = ("player_location", "flags", "inventory")

failures = []


def check(cid: str, ok: bool, detail: str = "") -> bool:
    """记录一项检查结果（不中断，跑完所有项后统一汇总）"""
    print(f"[{'OK' if ok else 'NG'}] {cid} {detail}")
    if not ok:
        failures.append(cid)
    return ok


def wait_server(timeout: int = 30) -> bool:
    """等待服务就绪（服务由操作者预先在 8888 端口启动）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(BASE + "/api/novel/list", timeout=5) as resp:
                resp.read()
            return True
        except OSError:   # 连接被拒/超时：服务尚未就绪，继续等待
            time.sleep(1)
    return False


def post_json(path: str, payload: dict, timeout: int = 300) -> str:
    """POST JSON，返回响应原始文本"""
    req = Request(BASE + path, data=json.dumps(payload).encode("utf-8"),
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def post_sse(path: str, payload: dict, timeout: int = 300):
    """POST 并读完整条 SSE 流。

    Returns: (events, raw_text) —— events 为解析出的 data 帧对象列表，
             raw_text 为整条报文原文（用于 A3 全文扫描）
    """
    req = Request(BASE + path, data=json.dumps(payload).encode("utf-8"),
                  headers={"Content-Type": "application/json"})
    lines = []
    with urlopen(req, timeout=timeout) as resp:
        for line in resp:
            lines.append(line.decode("utf-8"))

    events = []
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            events.append(json.loads(line[5:].strip()))
        except json.JSONDecodeError:
            continue
    return events, "".join(lines)


def get_json(path: str, timeout: int = 60) -> dict:
    """GET 并解析 JSON"""
    with urlopen(BASE + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def request_json(method: str, path: str, payload: dict | None = None, timeout: int = 300):
    """任意方法请求，返回 (status_code, body)。

    非 2xx 不抛异常，便于断言状态码（B11/B12 要断言 404）。
    """
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def novel_entry(novel_id: str) -> dict | None:
    """从书架接口取单本条目（残留断言一律以服务端数据为准，不读本地副本）"""
    for nv in get_json("/api/novel/list").get("novels", []):
        if nv["novel_id"] == novel_id:
            return nv
    return None


def shelf_ids() -> list[str]:
    """书架上的 novel_id 列表（隔离纪律要求的前后对比）"""
    return [nv["novel_id"] for nv in get_json("/api/novel/list").get("novels", [])]


def upload(path: Path, timeout: int = 600) -> str:
    """以 multipart/form-data 上传小说样本，返回响应原始文本"""
    boundary = uuid.uuid4().hex
    header = (f"--{boundary}\r\n"
              f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
              "Content-Type: text/plain\r\n\r\n").encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = Request(BASE + "/api/novel/upload", data=header + path.read_bytes() + footer,
                  headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def convergence_reason(state: dict, order_map: dict):
    """A4 口径：未触发事件名的收敛性。

    只扫结构性字段（triggered / next_event / total 等），先 Mask 掉三个 DM 自由文本
    容器（player_location / flags / inventory）——它们的文字由模型生成，随时可能恰好
    写出某个事件名（如见闻里出现"已拜师菩提"），拿它们做断言会让整个闸门随机变红。

    Returns: (失败原因, 被 Mask 的容器命中情况 {容器名: [事件名]})——通过时原因为空串
    """
    triggered = state.get("triggered")
    if not isinstance(triggered, list):
        return "triggered 缺失/非数组，无法判定", {}
    untriggered = {n: o for n, o in order_map.items() if n not in triggered}
    structural_text = json.dumps({k: v for k, v in state.items() if k not in DM_TEXT_FIELDS},
                                 ensure_ascii=False)
    masked_hits = {}
    for key in DM_TEXT_FIELDS:
        if key not in state:
            continue
        text = json.dumps(state[key], ensure_ascii=False)
        hit = sorted(n for n in untriggered if n in text)
        if hit:
            masked_hits[key] = hit
    shown = sorted(n for n in untriggered if n in structural_text)
    want = 1 if untriggered else 0
    if len(shown) == want:
        return "", masked_hits
    return (f"未触发事件名出现 {len(shown)} 个（{shown}），应为 {want} 个"
            f"（未触发事件共 {len(untriggered)} 个）"), masked_hits


def next_event_reason(state: dict, order_map: dict) -> str:
    """A8 口径：校验 next_event 的取值，返回失败原因（通过则返回空串）。

    存在未触发事件时，next_event 必须是"未触发事件中 order 最小者"——不能为 null
    （否则 β 方案会静默退化成"全隐藏"），也不能指向更靠后的事件。
    """
    triggered = state.get("triggered")
    if not isinstance(triggered, list):
        return "triggered 缺失/非数组，无法判定 next_event"
    untriggered = {n: o for n, o in order_map.items() if n not in triggered}
    ne = state.get("next_event")
    if not untriggered:
        return "" if ne is None else f"关键事件已全部触发，next_event 应为 null，实为 {ne!r}"
    if not isinstance(ne, dict):
        return f"仍有 {len(untriggered)} 个未触发事件，next_event 不得为 {ne!r}"
    want = min(untriggered, key=lambda n: untriggered[n])
    if ne.get("event_name") != want or ne.get("order") != untriggered[want]:
        return (f"next_event = {ne!r}，应为 {{'event_name': {want!r}, "
                f"'order': {untriggered[want]}}}（未触发中 order 最小者）")
    return ""


def check_state_contract(cid: str, state: dict, label: str, order_map: dict) -> bool:
    """A5 + A8 口径：triggered / next_event / total 三字段齐全、不含 trigger_condition，
    且 next_event 取值正确"""
    why = []
    for key in ("triggered", "next_event", "total"):
        if key not in state:
            why.append(f"缺少字段 {key}")
    if "triggered" in state and not isinstance(state["triggered"], list):
        why.append("triggered 不是数组")
    if "total" in state and not isinstance(state["total"], int):
        why.append("total 不是整数")
    if "next_event" in state and state["next_event"] is not None:
        ne = state["next_event"]
        if not isinstance(ne, dict):
            why.append("next_event 不是对象")
        else:
            extra = set(ne.keys()) - {"event_name", "order"}
            if extra:
                why.append(f"next_event 含多余字段 {sorted(extra)}")
    if "trigger_condition" in json.dumps(state, ensure_ascii=False):
        why.append("state 内出现 trigger_condition")
    reason = next_event_reason(state, order_map)
    if reason:
        why.append(reason)
    detail = f"{label} state 契约" + ("" if not why else "：" + "；".join(why))
    return check(cid, not why, detail)


def check_offline_next_event():
    """A9：离线确定性断言——直调 _enrich_state，不依赖 8888 服务。

    真实抽取结果的 order 可能不连续（LLM 输出不稳定）或缺字段（提取器
    setdefault 999），此时 next_event 绝不允许静默变 null。
    """
    from api.route_game import _enrich_state
    from models import GameState
    import pipeline.character_extractor as ce

    original = ce.get_key_events
    try:
        # 场景 1：order 有间隙（1 已触发，剩 3、5）→ 应露出 order 3
        ce.get_key_events = lambda novel_id: [
            {"event_name": "事件A", "order": 1},
            {"event_name": "事件B", "order": 3},
            {"event_name": "事件C", "order": 5},
        ]
        got = _enrich_state(GameState(novel_id="x", triggered_events=["事件A"]), "x")["next_event"]
        check("A9-1", got == {"event_name": "事件B", "order": 3},
              f"order 有间隙（3/5）→ next_event = {got!r}")

        # 场景 2：order 缺失（提取器 setdefault 999）→ 仍须露出该事件
        ce.get_key_events = lambda novel_id: [
            {"event_name": "事件A", "order": 1},
            {"event_name": "事件B"},
        ]
        got = _enrich_state(GameState(novel_id="x", triggered_events=["事件A"]), "x")["next_event"]
        check("A9-2", got is not None, f"order 缺失 → next_event = {got!r}")
    finally:
        ce.get_key_events = original


def check_offline_order_contract():
    """A10：离线确定性断言——关键事件 order 在提取出口被归一化为稠密 1..N。

    硬锁门槛 order <= max(已触发)+1 假设 order 稠密：LLM 跳号（1/3/5）或缺字段时，
    第一个未触发事件会被永久拒绝且无任何报错（时间线静默冻结）。
    """
    import memory.global_state as global_state
    from pipeline.character_extractor import normalize_event_orders

    sparse = [{"event_name": "事件A", "order": 1},
              {"event_name": "事件B", "order": 3},
              {"event_name": "事件C", "order": 5}]

    # A10-1：跳号 1/3/5 → 稠密 1/2/3，且保持声明顺序
    got = normalize_event_orders(sparse)
    check("A10-1",
          [e["order"] for e in got] == [1, 2, 3]
          and [e["event_name"] for e in got] == ["事件A", "事件B", "事件C"],
          f"跳号 1/3/5 → {[(e['event_name'], e['order']) for e in got]}")

    # A10-2：缺 order 视为末位（与原 setdefault 999 语义一致）
    got = normalize_event_orders([{"event_name": "事件A", "order": 1},
                                  {"event_name": "事件B"},
                                  {"event_name": "事件C", "order": 3}])
    check("A10-2",
          [e["order"] for e in got] == [1, 2, 3]
          and [e["event_name"] for e in got] == ["事件A", "事件C", "事件B"],
          f"缺 order 视为末位 → {[(e['event_name'], e['order']) for e in got]}")

    # A10-3：归一化后的清单能通过硬锁门槛（已触发 order 1 → 下一个事件可入库）
    normalized = normalize_event_orders(sparse)
    original = global_state.get_key_events
    try:
        global_state.get_key_events = lambda novel_id: normalized
        global_state.init_state("a10-probe", "x")
        global_state.update_state("a10-probe", {"triggered_events": ["事件A"]})
        global_state.update_state("a10-probe", {"triggered_events": ["事件B"]})
        accepted = list(global_state.get_state("a10-probe").triggered_events)
    finally:
        global_state.get_key_events = original
    check("A10-3", accepted == ["事件A", "事件B"],
          f"已触发 order 1 后，下一个事件被硬锁门槛接受 = {accepted}")


def check_a11_masking():
    """A11：锁死 A4 的 Mask 范围（M2 条件 C-1）。

    A4 靠 Mask 掉 DM 自由文本容器才变稳定，扫面被缩小了；若没有断言守着，今后把 Mask
    范围放大（乃至 Mask 整个 state）会让 A4 静默变成永真。故双向锁死：
      A11-1 三个容器全部命中未触发事件名 → A4 必须通过，且 masked_hits 必须非空
            （不判 masked_hits 的话，空 state 也能让这条断言成立 → 等于没测）
      A11-2 再往结构面塞一个未触发事件名 → A4 必须失败（鉴别力保留）
    """
    order_map = {"石猴出世": 1, "发现水帘洞": 2, "称美猴王": 3, "拜师菩提": 4}
    base = {"triggered": ["石猴出世"],
            "next_event": {"event_name": "发现水帘洞", "order": 2},
            "total": 4}

    # A11-1：三个 DM 自由文本容器全部写入未触发事件名
    dirty = dict(base,
                 player_location="称美猴王的花果山",
                 flags={"已拜师菩提，待祖师问名": True},
                 inventory=["拜师菩提祖师的信物"])
    reason, masked_hits = convergence_reason(dirty, order_map)
    check("A11-1", reason == "" and bool(masked_hits),
          f"三容器全命中 → A4 原因 = {reason!r}，Mask 命中 = {masked_hits or '空(断言将失效)'}")

    # A11-2：同一 state 再往结构面塞入未触发事件名
    leaky = dict(dirty, _timeline=[{"event_name": "称美猴王"}, {"event_name": "拜师菩提"}])
    reason2, _ = convergence_reason(leaky, order_map)
    check("A11-2", reason2 != "", f"结构性泄露 → A4 原因 = {reason2!r}")


def check_session_delete(novel_id: str, kept_session_id: str):
    """B9-B12：TU-4 删除存档的零残留、幂等与"不存在即报错"语义。

    隔离纪律：只操作本次运行自己开的 session，绝不 DELETE 测试前已存在的条目。
    """
    from config import SESSIONS_DIR

    sid = json.loads(post_json("/api/game/start", {"novel_id": novel_id}))["session_id"]
    print(f"[INFO] TU-4 另开一条待删存档 session_id={sid}")

    status, body = request_json("DELETE", f"/api/game/sessions/{sid}")
    check("B9-0", status == 200, f"DELETE 存档 -> HTTP {status}，body = {body}")

    path = SESSIONS_DIR / f"{sid}.json"
    check("B9", not path.exists(), f"磁盘快照已删除: {path.name}")

    entry = novel_entry(novel_id) or {}
    sids = [s["session_id"] for s in entry.get("sessions", [])]
    check("B10", sid not in sids and kept_session_id in sids,
          f"书架 sessions = {sids}（应含 {kept_session_id}、不含 {sid}）")

    status2, _ = request_json("DELETE", f"/api/game/sessions/{sid}")
    check("B11", status2 == 404, f"重复 DELETE -> HTTP {status2}（应为 404，不得静默成功）")

    status3, _ = request_json("POST", "/api/game/resume", {"session_id": sid})
    check("B12", status3 == 404, f"删除后 resume -> HTTP {status3}（应为 404）")


def collection_names() -> list[str]:
    """chroma 现存集合名。

    R-3：list_collections() 返回的是 Collection 对象序列，"名字 in 列表"恒为 False，
    必须取 .name，否则 B7 永远绿。
    """
    from pipeline.novel_parser import _get_client
    return [c.name for c in _get_client().list_collections()]


def action_types(session_id: str, novel_id: str, action: str = "我环顾四周，仔细打量眼前的环境。"):
    """发一个动作并返回 (帧类型, error 消息)。

    R-4：/action 的失败是 HTTP 200 + SSE {'type':'error'} 帧，不能断 HTTP 状态码。
    """
    events, _ = post_sse("/api/game/action",
                         {"session_id": session_id, "novel_id": novel_id, "action": action})
    types = [e.get("type") for e in events]
    return types, [e.get("message") for e in events if e.get("type") == "error"]


def check_drop_containers(novel_id: str, session_id: str):
    """B6a：离线单元断言——drop_cache / drop_state / short_term.drop 必须清掉内存条目。

    跨进程读不到服务端的内存，所以在测试进程里塞脏数据再调这三个函数，做真鉴别。
    """
    import memory.global_state as global_state
    import memory.short_term as short_term
    import pipeline.character_extractor as character_extractor

    character_extractor._cache[novel_id] = {"graph": {"nodes": [], "links": []}}
    global_state._states[session_id] = "dirty"
    short_term._memory[session_id] = "dirty"
    try:
        character_extractor.drop_cache(novel_id)
        global_state.drop_state(session_id)
        short_term.drop(session_id)
    except AttributeError as e:      # 红灯期：清理函数尚未实现
        check("B6a", False, f"内存清理函数缺失: {e}")
        return
    finally:
        character_extractor._cache.pop(novel_id, None)
        global_state._states.pop(session_id, None)
        short_term._memory.pop(session_id, None)
    check("B6a",
          novel_id not in character_extractor._cache
          and session_id not in global_state._states
          and session_id not in short_term._memory,
          "内存三容器（_cache / _states / _memory）均已清除")


def check_novel_delete():
    """B1-B8 + B6a/B6b：TU-5 删除小说的零残留、幂等与"不存在即报错"语义。

    隔离纪律：只上传并删除本次运行自建的小说；不碰测试前已存在的条目，也不动受 git
    管理的夹具 data/novels/西游记-样本.txt（上传是复制成副本）。
    """
    from config import CHARACTER_CACHE_DIR, NOVELS_DIR, SESSIONS_DIR

    novel_id = json.loads(upload(SAMPLE))["novel_id"]
    sid1 = json.loads(post_json("/api/game/start", {"novel_id": novel_id}))["session_id"]
    sid2 = json.loads(post_json("/api/game/start", {"novel_id": novel_id}))["session_id"]
    print(f"[INFO] TU-5 待删小说 novel_id={novel_id}，存档 = {[sid1, sid2]}")

    entry = novel_entry(novel_id) or {}
    novel_file = NOVELS_DIR / str(entry.get("filename", ""))
    cache_file = CHARACTER_CACHE_DIR / f"{novel_id}_characters.json"
    check("B0", novel_file.exists() and len(entry.get("sessions", [])) == 2,
          f"删除前素材就位: {novel_file.name}，书架存档 {len(entry.get('sessions', []))} 条")

    check_drop_containers(novel_id, sid1)

    status, body = request_json("DELETE", f"/api/novel/{novel_id}")
    check("B2", status == 200 and body.get("vector_deleted") is True,
          f"DELETE 小说 -> HTTP {status}，body = {body}（首次删除 vector_deleted 应为 True）")

    check("B1", not novel_file.exists(), f"正文文件已删除: {novel_file.name}")
    check("B3", not cache_file.exists(), f"人物缓存已删除: {cache_file.name}")
    snaps = [SESSIONS_DIR / f"{s}.json" for s in (sid1, sid2)]
    check("B4", not any(p.exists() for p in snaps),
          f"该书全部存档快照已删除: {[p.name for p in snaps]}")
    check("B5", novel_entry(novel_id) is None, "书架已无该条目")
    check("B7", f"{novel_id}_chapters" not in collection_names(),
          f"chroma 已无 {novel_id}_chapters 集合")

    # B6b：在线行为代理（跨进程只能看行为）
    graph = get_json(f"/api/novel/{novel_id}/graph")
    check("B6b-1", not graph.get("nodes") and not graph.get("links"),
          f"关系图接口返回空: nodes={len(graph.get('nodes', []))}, links={len(graph.get('links', []))}")
    types, errs = action_types(sid1, novel_id)
    check("B6b-2", "error" in types,
          f"被连带删除的存档发 /action -> 帧类型 {types}，error = {errs}")

    # B11 同口径的 404 语义（小说维度）
    status2, _ = request_json("DELETE", f"/api/novel/{novel_id}")
    check("B13", status2 == 404, f"重复 DELETE 小说 -> HTTP {status2}（应为 404，不得静默成功）")


def check_reupload():
    """B8：删除后重新上传同一本小说 -> 能入库 + 能开局（残留不阻断）；完成后顺手删掉。"""
    data = json.loads(upload(SAMPLE))
    novel_id = data["novel_id"]
    check("B8-1", bool(novel_id) and data.get("chunk_count", 0) > 0,
          f"重新上传成功: novel_id={novel_id}, chunk_count={data.get('chunk_count')}")

    # /start 偶发 502 = DM 的 LLM 调用上游失败（限流/超时），与本 TU 的删除语义无关；
    # 允许一次有界重试，持久失败（例如真的开不了局）仍然判红。
    for attempt in (1, 2):
        try:
            start = json.loads(post_json("/api/game/start", {"novel_id": novel_id}))
            break
        except HTTPError as e:
            if attempt == 2 or e.code != 502:
                raise
            print("[INFO] /start 返回 502（上游 LLM 失败），5 秒后重试一次")
            time.sleep(5)
    check("B8-2", bool(start.get("story") or start.get("choices")),
          f"重新开局成功: session_id={start.get('session_id')}")

    status, body = request_json("DELETE", f"/api/novel/{novel_id}")
    check("B8-3", status == 200, f"清理性删除重传小说 -> HTTP {status}，body = {body}")


def check_sse_contract(novel_id: str, session_id: str, order_map: dict):
    """A1-A5 / A8：/action 的 SSE 协议与防剧透契约（full 层专用）"""
    events, sse_text = post_sse("/api/game/action", {
        "session_id": session_id, "novel_id": novel_id,
        "action": "我环顾四周，仔细打量眼前的环境。",
    })
    types = [e.get("type") for e in events]
    print(f"[INFO] SSE 帧数 = {len(events)}，事件类型序列 = {types}")

    # A1：事件类型集合 ⊆ 白名单
    illegal = sorted({t for t in types if t not in ALLOWED_EVENTS})
    check("A1", not illegal, f"事件类型集合 {sorted(set(types))}；越界类型 = {illegal or '无'}")

    # A2：必含事件齐全
    missing = sorted(REQUIRED_EVENTS - set(types))
    check("A2", not missing, f"缺失必含事件 = {missing or '无'}")

    # A3：整条报文 trigger_condition 零出现
    leak = sse_text.count("trigger_condition")
    check("A3", leak == 0, f"整条 SSE 报文中 trigger_condition 出现 {leak} 次")

    # 取 state 事件（取最后一个；协议中 state 在 done 之前下发一次）
    state = next((e["state"] for e in reversed(events)
                  if e.get("type") == "state" and isinstance(e.get("state"), dict)), None)
    if state is None:
        for cid in ("A4", "A5", "A8"):
            check(cid, False, "SSE 中未取到 state 事件，无法判定")
        return

    triggered = state.get("triggered")

    # A4：收敛性——有未触发事件时 state 中必须恰好出现 1 个未触发事件名。
    # 一个都不露（把 next_event 也一起藏掉）会让 β 方案静默退化成"全隐藏"，判失败。
    reason, masked_hits = convergence_reason(state, order_map)
    check("A4", not reason, "SSE state 未触发事件名收敛" + ("" if not reason else "：" + reason))
    print(f"[INFO] 已 Mask 的 DM 自由文本容器命中未触发事件名 = {masked_hits or '无'}"
          f"（仅观测，不参与断言）")

    # A5：state 契约三字段（含 A8 口径）
    check_state_contract("A5", state, "/action SSE", order_map)

    # A8：正向断言——next_event 不得被藏掉，且必须是未触发中 order 最小者
    reason = next_event_reason(state, order_map)
    check("A8", not reason, "SSE state next_event 正向断言" + ("" if not reason else "：" + reason))

    # 诊断（不断言）：整条报文里未触发事件名的出现情况，供评审判断
    whole = [n for n in order_map if n not in (triggered or []) and n in sse_text]
    print(f"[INFO] 整条 SSE 报文中出现的未触发事件名 = {whole}")


def check_resume_contract(session_id: str, order_map: dict):
    """A7：/resume 的 state 契约（full 层专用）"""
    resume_data = json.loads(post_json("/api/game/resume", {"session_id": session_id}))
    check_state_contract("A7", resume_data["state"], "/resume", order_map)


def main():
    print("=" * 72)
    print(f"TU-10a/10d 接口契约测试（SSE / state 协议）｜CONTRACT_SCOPE={SCOPE}")
    print("=" * 72)

    check_offline_next_event()      # A9：离线断言，先跑，不受服务状态影响
    check_offline_order_contract()  # A10：order 稠密契约，同样离线
    check_a11_masking()             # A11：A4 的 Mask 范围双向锁死（M2 条件 C-1）

    if not check("PRE-1", SAMPLE.exists(), f"样本文件存在: {SAMPLE}"):
        return
    if not check("PRE-2", wait_server(), f"服务可达: {BASE}"):
        return

    print(f"[INFO] 运行开始 bookshelf = {shelf_ids()}")
    novel_id = json.loads(upload(SAMPLE))["novel_id"]
    print(f"[INFO] 上传完成 novel_id={novel_id}")
    # 关键事件清单取自人物缓存文件（A4 需要完整事件名与 order）
    cache_path = (Path(__file__).resolve().parent / "data" / "character_cache"
                  / f"{novel_id}_characters.json")
    key_events = []
    if cache_path.exists():
        key_events = json.loads(cache_path.read_text(encoding="utf-8")).get("key_events", [])
    order_map = {e["event_name"]: e.get("order", 999) for e in key_events if e.get("event_name")}
    if not check("PRE-3", len(order_map) > 0, f"关键事件数 = {len(order_map)}（A4 判定依据）"):
        return

    # ---------- /start（两层都需要：delete 层要用 session_id 做 B10 的"同书其他存档仍在"基准）----------
    start_data = json.loads(post_json("/api/game/start", {"novel_id": novel_id}))
    session_id = start_data["session_id"]
    print(f"[INFO] 开局完成 session_id={session_id}")

    if SCOPE == "full":
        check_state_contract("A6", start_data["state"], "/start", order_map)
        check_sse_contract(novel_id, session_id, order_map)   # A1-A5 / A8
        check_resume_contract(session_id, order_map)          # A7
    else:
        print("[INFO] CONTRACT_SCOPE=delete：跳过 A6、/action（A1-A5/A8）与 /resume（A7）")

    # ================= TU-4：删除存档（B9-B12）=================
    check_session_delete(novel_id, session_id)

    # ================= TU-5：删除小说（B1-B8 / B6a / B6b）=================
    check_novel_delete()
    if SCOPE == "full":
        check_reupload()
    else:
        print("[INFO] CONTRACT_SCOPE=delete：跳过 B8（重传，最贵的一项）")

    print(f"[INFO] 运行结束 bookshelf = {shelf_ids()}")

    print("-" * 72)
    if failures:
        print(f"失败项 {len(failures)} 个：{failures}")
    else:
        print("全部检查通过")
    print("-" * 72)


if __name__ == "__main__":
    try:
        main()
        assert not failures, f"契约检查未通过：{failures}"
    except AssertionError as e:
        print(f"断言失败: {e}")
        sys.exit(1)
    sys.exit(0)
