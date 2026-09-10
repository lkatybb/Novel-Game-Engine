"""诊断脚本：抓取 /api/game/action 的原始 SSE 事件序列（用完即删）"""
import json
import sys
import requests

BASE = "http://127.0.0.1:8888"

# 1. 拿一本已有小说
novels = requests.get(f"{BASE}/api/novel/list", timeout=10).json()["novels"]
if not novels:
    sys.exit("书架为空，请先上传小说")
novel = novels[0]
print(f"小说: {novel['title']}  id={novel['novel_id'][:16]}...")

# 2. 开新局
start = requests.post(f"{BASE}/api/game/start",
                      json={"novel_id": novel["novel_id"]}, timeout=120).json()
sid = start["session_id"]
print(f"session: {sid}")
print(f"开场 choices 数量: {len(start.get('choices', []))}")

# 3. 发一个动作，抓原始 SSE 行
action = (start.get("choices") or ["四处看看"])[0]
print(f"发送动作: {action}\n--- 原始 SSE 事件 ---")
resp = requests.post(f"{BASE}/api/game/action",
                     json={"session_id": sid, "novel_id": novel["novel_id"], "action": action},
                     timeout=180, stream=True)
for raw in resp.iter_lines(decode_unicode=True):
    if not raw or not raw.startswith("data:"):
        continue
    try:
        evt = json.loads(raw[5:].strip())
    except json.JSONDecodeError:
        print("[无法解析]", raw[:120])
        continue
    t = evt.get("type")
    if t == "scene":
        print("scene     :", repr(evt.get("text", ""))[:60])
    elif t == "choices":
        print("choices   :", evt.get("options"))
    elif t == "state":
        st = evt.get("state", {})
        print("state     : location =", st.get("location"),
              "| triggered =", len(st.get("triggered", [])), "/", st.get("total"),
              "| next =", st.get("next_event"))
    else:
        print(f"{t:10}:", json.dumps(evt, ensure_ascii=False)[:120])
