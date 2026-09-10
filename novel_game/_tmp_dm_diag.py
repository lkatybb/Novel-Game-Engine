# -*- coding: utf-8 -*-
"""诊断：直接消费 dm_stream，看门控事件序列与最终 result.story。"""
import sys
sys.path.insert(0, '.')
import requests
from agents.dm import dm_stream

BASE = "http://127.0.0.1:8888"
novels = requests.get(f"{BASE}/api/novel/list", timeout=30).json()
novel_id = novels["novels"][0]["novel_id"]
start = requests.post(f"{BASE}/api/game/start", json={"novel_id": novel_id}, timeout=120).json()
sid = start["session_id"]

action = "我盯着护士的眼睛，直接质问她：晨晨到底在哪里？"
seq = []
result = None
scene_total = 0
for c in dm_stream(sid, novel_id, action + "\n（NPC回应: 晨晨？先生，请您先冷静一下。）"):
    t = c.get("type")
    if t == "scene":
        scene_total += len(c.get("text", ""))
        seq.append(f"scene(+{len(c.get('text', ''))})")
    elif t == "scene_change":
        seq.append(f"scene_change({c.get('scene')})")
    elif t == "result":
        result = c.get("data", {})
        seq.append("result")
    else:
        seq.append(t)

print("chunk seq:", seq)
print("scene chars emitted:", scene_total)
if result is not None:
    print("result keys:", list(result.keys()))
    print("story len:", len(result.get("story") or ""))
    print("story head:", (result.get("story") or "")[:80])
    print("state_changes:", result.get("state_changes"))
