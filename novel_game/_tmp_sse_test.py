# -*- coding: utf-8 -*-
"""诊断：对话动作后用 resume 快照读 last_story，确认 DM 是否产出正文。"""
import json
import requests

BASE = "http://127.0.0.1:8888"
novel_id = requests.get(f"{BASE}/api/novel/list", timeout=30).json()["novels"][0]["novel_id"]
sid = requests.post(f"{BASE}/api/game/start", json={"novel_id": novel_id}, timeout=120).json()["session_id"]

payload = {"session_id": sid, "novel_id": novel_id, "action": "我拔下输液管，冲出医院，打车直奔尘肺寄宿学校去找晨晨。"}
types = []
change = None
with requests.post(f"{BASE}/api/game/action", json=payload, stream=True, timeout=180) as r:
    for raw in r.iter_lines():
        if raw and raw.startswith(b"data:"):
            evt = json.loads(raw.decode("utf-8")[5:].strip())
            t = evt.get("type")
            if t == "npc":
                types.append(f"npc(len={len(evt.get('text') or '')})")
            elif t == "scene":
                types.append("scene(+%d)" % len(evt.get('text') or ''))
            else:
                types.append(t)
                if t == "scene_change":
                    change = {"name": evt.get("name"), "desc": evt.get("desc")}
print("SSE first 6:", types[:6])
print("scene_change:", change, "| before any scene/npc:",
      (types.index("scene_change") < next((i for i,x in enumerate(types) if x.startswith(("scene","npc"))), 10**9)) if change else "NO_SCENE_CHANGE")
print("total scene chunks:", sum(1 for x in types if x.startswith("scene(")))

res = requests.post(f"{BASE}/api/game/resume", json={"session_id": sid}, timeout=60).json()
story = res.get("last_story") or ""
print("resume last_story len:", len(story))
print("last_story head:", story[:120].replace("\n", " "))
