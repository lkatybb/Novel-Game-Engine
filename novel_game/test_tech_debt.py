"""全链路验收：债务清理后所有功能正常"""
import os
import requests, pathlib, json, time, sys

# 服务地址：默认 8888，可用 CONTRACT_BASE 覆盖（与 test_contract.py 同一先例）
BASE = os.environ.get("CONTRACT_BASE", "http://127.0.0.1:8888")

for i in range(10):
    try:
        requests.get(BASE + "/api/novel/list", timeout=1)
        break
    except Exception:
        time.sleep(1)
else:
    print("❌ 服务未启动")
    sys.exit(1)

passed = 0; failed = 0

def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  ✓ {name}")
        passed += 1
    else:
        print(f"  ✗ {name}  {detail}")
        failed += 1

# 1. 导入链
print("\n=== 1. 导入链 ===")
try:
    from config import get_llm_client
    c = get_llm_client()
    check("get_llm_client 单例", c is not None)

    from utils import read_text_auto
    check("utils.read_text_auto", read_text_auto(pathlib.Path("data/novels/西游记-样本.txt"))[:20] != "")

    from api.main import app
    check("FastAPI app 导入", app is not None)

    from agents import graph
    check(
        "LangGraph 编排层可用",
        callable(graph.stream_graph) and callable(graph.run_graph),
    )
except Exception as e:
    check("导入链", False, str(e))

# 2. 上传 + 入库（上传接口已异步化：POST 只拿 job_id，进度得轮询）
print("\n=== 2. 上传 + 入库 ===")
txt = pathlib.Path("data/novels/西游记-样本.txt").read_bytes()
r = requests.post(BASE + "/api/novel/upload", files={"file": ("西游记-样本.txt", txt)})
job_id = r.json().get("job_id")
check("上传 200", r.status_code == 200 and bool(job_id), f"body={r.text.strip()}")

job = {}
deadline = time.time() + 600
while time.time() < deadline:
    job = requests.get(BASE + f"/api/novel/import/{job_id}").json()
    if job.get("status") != "running":
        break
    time.sleep(1.5)
novel_id = job.get("novel_id")
check("novel_id 生成", job.get("status") == "done" and bool(novel_id),
      f"status={job.get('status')}, error={job.get('error')}")

# 3. 关键事件落盘
print("\n=== 3. 关键事件落盘 ===")
from pipeline.character_extractor import get_key_events
events = get_key_events(novel_id)
check("磁盘缓存加载", len(events) > 0)
check("缓存文件存在", (pathlib.Path(f"data/character_cache/{novel_id}_characters.json")).exists())

# 4. 书架
print("\n=== 4. 书架 ===")
r = requests.get(BASE + "/api/novel/list")
check("书架列表", r.status_code == 200 and len(r.json()["novels"]) > 0)

# 5. 文件校验
print("\n=== 5. 文件校验 ===")
r = requests.post(BASE + "/api/novel/upload", files={"file": ("bad.exe", b"hello")})
check("禁止 .exe", r.status_code == 200 and r.json().get("error", "").find("不支持") >= 0)

# 6. 开始 + 对话
print("\n=== 6. 开始 + 对话 ===")
r = requests.post(BASE + "/api/game/start", json={"novel_id": novel_id})
check("start 200", r.status_code == 200)
d = r.json()
check("choices > 0", len(d.get("choices", [])) > 0)
check("state 有 triggered/total", "triggered" in d.get("state", {}) and "total" in d.get("state", {}))
session_id = d["session_id"]

# 7. SSE 一轮
print("\n=== 7. SSE 一轮动作 ===")
import json as j
with requests.post(BASE + "/api/game/action",
                   data=j.dumps({"session_id": session_id, "novel_id": novel_id, "action": "我想四处看看"}),
                   headers={"Content-Type": "application/json"}, stream=True, timeout=30) as resp:
    chunks = [l for l in resp.iter_lines(decode_unicode=True) if l.startswith("data: ")]
check("SSE 返回数据块", len(chunks) > 0)
check("有 state 更新块", any('"type": "state"' in c for c in chunks))
check("有 done 块", any('"type": "done"' in c for c in chunks))

# 8. 恢复
print("\n=== 8. 恢复会话 ===")
r = requests.post(BASE + "/api/game/resume", json={"session_id": session_id})
check("resume 200", r.status_code == 200)
check("恢复返回 state", "state" in r.json())

print(f"\n{'='*40}")
print(f"结果: {passed} 通过 / {failed} 失败")
sys.exit(0 if failed == 0 else 1)
