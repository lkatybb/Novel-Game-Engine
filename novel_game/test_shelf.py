"""验收书架功能"""
import requests, pathlib, json, time

BASE = "http://127.0.0.1:8888"

# 先等服务起来
for _ in range(10):
    try:
        requests.get(BASE + "/api/novel/list", timeout=1)
        break
    except Exception:
        time.sleep(1)

# 1. 上传样本小说
txt = pathlib.Path("data/novels/西游记-样本.txt").read_bytes()
r = requests.post(BASE + "/api/novel/upload", files={"file": ("西游记-样本.txt", txt)})
print("1. 上传:", r.status_code, r.json() if r.status_code != 200 else "OK")
novel_id = r.json().get("novel_id")
print("   novel_id:", novel_id)

# 2. 开始游戏
r = requests.post(BASE + "/api/game/start", json={"novel_id": novel_id})
print("2. 开始游戏:", r.status_code)
session_id = r.json().get("session_id")
print("   session_id:", session_id)
print("   story 前50字:", r.json().get("story", "")[:50])

# 3. 模拟一轮动作（短请求，不跑完 SSE）
# 直接验证快照文件生成
from pathlib import Path
import os
time.sleep(1)
snap = Path(f"data/sessions/{session_id}.json")
print("3. 快照文件存在:", snap.exists())
if snap.exists():
    content = json.loads(snap.read_text(encoding="utf-8"))
    print("   game_state:", list(content["game_state"].keys()))
    print("   short_term 轮数:", len(content["short_term_memory"]))

# 4. 书架列表
r = requests.get(BASE + "/api/novel/list")
books = r.json()["novels"]
print("4. 书架列表:")
for b in books:
    print(f"   - {b['title']} (sessions: {len(b['sessions'])})")

# 5. 模拟 SSE 里的一轮动作（触发保存 + meta 更新）
import json as j
payload = j.dumps({"session_id": session_id, "novel_id": novel_id, "action": "我想四处看看"})
with requests.post(BASE + "/api/game/action", data=payload,
                   headers={"Content-Type": "application/json"}, stream=True, timeout=30) as resp:
    for line in resp.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            pass  # 吃 SSE，不关心内容

time.sleep(1)

# 6. 恢复会话
r = requests.post(BASE + "/api/game/resume", json={"session_id": session_id})
print("6. 恢复会话:", r.status_code)
resumed = r.json()
print("   resumed:", resumed.get("resumed"))
print("   novel_id:", resumed.get("novel_id"))
print("   state.location:", resumed.get("state", {}).get("player_location"))
print("   choices 数量:", len(resumed.get("choices", [])))

# 7. session 列表
r = requests.get(BASE + f"/api/game/sessions/{novel_id}")
sessions = r.json()["sessions"]
print("7. session 列表:", len(sessions), "个")
if sessions:
    print("   last_action:", sessions[0].get("last_action"))
