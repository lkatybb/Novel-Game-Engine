"""Phase 4 API 验收脚本"""
import urllib.request
import json
import uuid

BASE = "http://127.0.0.1:8000"

# 1. 上传小说
boundary = uuid.uuid4().hex
with open("data/novels/西游记-样本.txt", "rb") as f:
    file_data = f.read()

body = (
    "--" + boundary + "\r\n"
    'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
    "Content-Type: text/plain\r\n\r\n"
).encode() + file_data + ("\r\n--" + boundary + "--\r\n").encode()

req = urllib.request.Request(
    BASE + "/api/novel/upload",
    data=body,
    headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
)
with urllib.request.urlopen(req, timeout=120) as resp:
    upload_result = json.loads(resp.read())
print("上传:", upload_result)

# 2. 开始游戏
data = json.dumps({"novel_id": upload_result["novel_id"]}).encode()
req2 = urllib.request.Request(
    BASE + "/api/game/start", data=data, headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(req2, timeout=120) as resp2:
    start_result = json.loads(resp2.read())
print("session:", start_result["session_id"])
print("story:", start_result["story"][:80])
print("choices:", start_result["choices"])

# 3. SSE 动作
data3 = json.dumps(
    {
        "session_id": start_result["session_id"],
        "novel_id": upload_result["novel_id"],
        "action": "和孙悟空说话",
    }
).encode()
req3 = urllib.request.Request(
    BASE + "/api/game/action", data=data3, headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(req3, timeout=120) as resp3:
    raw = resp3.read().decode("utf-8")
    for line in raw.split("\n\n"):
        if not line.startswith("data: "):
            continue
        chunk = json.loads(line[6:])
        t = chunk["type"]
        if t == "category":
            print("分类:", chunk["value"])
        elif t == "npc":
            print("NPC:", chunk["text"][:60])
        elif t == "result":
            print("选项:", chunk["data"].get("choices", []))
        elif t == "state":
            print("位置:", chunk["state"]["player_location"])
        elif t == "error":
            print("错误:", chunk["message"])
        elif t == "done":
            print("完成")
