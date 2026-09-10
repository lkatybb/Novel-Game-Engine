# -*- coding: utf-8 -*-
"""TU-10a｜SSE / state 接口契约测试（零依赖：仅标准库）

用途：为 TU-1（剧透收敛）提供可执行的接口契约断言，先红灯后转绿。
约束：不引入任何第三方依赖、不使用 pytest —— 纯 assert + sys.exit(0/1)。

运行前提：8888 端口已启动服务
    cd e:\\小说\\novel_game
    python -m uvicorn api.main:app --port 8888
运行方式：
    python test_contract.py

检查项：
    A9     离线确定性断言：直调 _enrich_state，锁死 order 不连续 / 缺 order 的事件清单
           （不依赖服务，故先于网络检查执行）
    PRE-1  测试样本文件存在
    PRE-2  8888 服务可达
    PRE-3  上传后能取到非空的关键事件清单（A4/A8 的判定依据）
    A1     SSE 事件类型集合 ⊆ 协议白名单
    A2     SSE 必含 {stage, scene, choices, state, done}
    A3     /action 整条 SSE 报文中 "trigger_condition" 出现 0 次
    A4     未触发事件名收敛：有未触发事件时恰好露 1 个，且不许一个都不露
    A5     SSE state 含 triggered / next_event / total，next_event 仅含 event_name+order
    A6     /start 的 state 满足 A5 + A8 口径（TU-1 三挂载点一致性）
    A7     /resume 的 state 满足 A5 + A8 口径（TU-1 三挂载点一致性）
    A8     存在未触发事件时 next_event 不得为 null，且是未触发中 order 最小者；
           未触发为空时 next_event 必须为 null

退出码：全部通过 0；任一失败 1（失败项在末尾汇总）
"""

import json
import sys
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8888"
SAMPLE = Path(__file__).resolve().parent / "data" / "novels" / "西游记-样本.txt"

# SSE 协议白名单 / 必含事件（API 契约，不得静默变化）
ALLOWED_EVENTS = {"stage", "scene_change", "scene", "npc", "choices", "state", "error", "done"}
REQUIRED_EVENTS = {"stage", "scene", "choices", "state", "done"}

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


def convergence_reason(state: dict, order_map: dict) -> str:
    """A4 口径：未触发事件名的收敛性，返回失败原因（通过则返回空串）。

    有未触发事件时必须恰好露出 1 个（即 next_event），不许一个都不露——
    把紧邻的那个也一起藏掉会让 β 方案静默退化成"全隐藏"。
    """
    triggered = state.get("triggered")
    if not isinstance(triggered, list):
        return "triggered 缺失/非数组，无法判定"
    state_text = json.dumps(state, ensure_ascii=False)
    untriggered = {n: o for n, o in order_map.items() if n not in triggered}
    shown = sorted(n for n in untriggered if n in state_text)
    want = 1 if untriggered else 0
    if len(shown) == want:
        return ""
    return (f"未触发事件名出现 {len(shown)} 个（{shown}），应为 {want} 个"
            f"（未触发事件共 {len(untriggered)} 个）")


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


def main():
    print("=" * 72)
    print("TU-10a/10d 接口契约测试（SSE / state 协议）")
    print("=" * 72)

    check_offline_next_event()   # A9：离线断言，先跑，不受服务状态影响

    if not check("PRE-1", SAMPLE.exists(), f"样本文件存在: {SAMPLE}"):
        return
    if not check("PRE-2", wait_server(), f"8888 服务可达: {BASE}"):
        return

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

    # ---------- /start ----------
    start_data = json.loads(post_json("/api/game/start", {"novel_id": novel_id}))
    session_id = start_data["session_id"]
    print(f"[INFO] 开局完成 session_id={session_id}")
    check_state_contract("A6", start_data["state"], "/start", order_map)

    # ---------- /action ----------
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
    else:
        triggered = state.get("triggered")

        # A4：收敛性——有未触发事件时 state 中必须恰好出现 1 个未触发事件名。
        # 一个都不露（把 next_event 也一起藏掉）会让 β 方案静默退化成"全隐藏"，判失败。
        reason = convergence_reason(state, order_map)
        check("A4", not reason, "SSE state 未触发事件名收敛" + ("" if not reason else "：" + reason))

        # A5：state 契约三字段（含 A8 口径）
        check_state_contract("A5", state, "/action SSE", order_map)

        # A8：正向断言——next_event 不得被藏掉，且必须是未触发中 order 最小者
        reason = next_event_reason(state, order_map)
        check("A8", not reason, "SSE state next_event 正向断言" + ("" if not reason else "：" + reason))

        # 诊断（不断言）：整条报文里未触发事件名的出现情况，供评审判断
        whole = [n for n in order_map if n not in (triggered or []) and n in sse_text]
        print(f"[INFO] 整条 SSE 报文中出现的未触发事件名 = {whole}")

    # ---------- /resume ----------
    resume_data = json.loads(post_json("/api/game/resume", {"session_id": session_id}))
    check_state_contract("A7", resume_data["state"], "/resume", order_map)

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
