import requests, pathlib, json, time
BASE = "http://127.0.0.1:8888"
for _ in range(10):
    try: requests.get(BASE + "/api/novel/list", timeout=1); break
    except: time.sleep(1)

txt = pathlib.Path("data/novels/西游记-样本.txt").read_bytes()
r = requests.post(BASE + "/api/novel/upload", files={"file": ("test2.txt", txt)})
novel_id = r.json()["novel_id"]
r = requests.post(BASE + "/api/game/start", json={"novel_id": novel_id})
d = r.json()
print("state keys:", list(d["state"].keys()))
print("_timeline 存在:", "_timeline" in d["state"])
print("triggered_events:", d["state"]["triggered_events"])
for t in d["state"].get("_timeline", [])[:3]:
    print(f"  {t['order']}. {t['event_name']}")
