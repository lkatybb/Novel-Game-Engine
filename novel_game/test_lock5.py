"""看DM返回的story和flag里有没有关键事件名"""
import pathlib, json
from pipeline.novel_parser import ingest
from pipeline.character_extractor import extract_characters, get_key_events
from memory.global_state import init_state, get_state
from agents.dm import dm_run, dm_update_memory

t = pathlib.Path("data/novels/西游记-样本.txt").read_text(encoding="utf-8")
r = ingest("西游记-样本.txt")
extract_characters(r["novel_id"], t)

session_id = "test_lock5"
init_state(session_id, r["novel_id"])

key_events = get_key_events(r["novel_id"])
event_names = [e["event_name"] for e in key_events]
print("关键事件名:", event_names)

result = dm_run(session_id, r["novel_id"], "劝孙悟空不要拜师菩提祖师，跟我们走")

print("\n=== DM返回 ===")
print("story:", result.get("story")[:200])
print("state_changes:", result.get("state_changes"))

# 手动检查
story_text = result.get("story", "")
flag = result.get("state_changes", {}).get("flag", "") if isinstance(result.get("state_changes"), dict) else ""
print("\n=== 事件匹配检查 ===")
for en in event_names:
    in_story = en in story_text
    in_flag = en in (flag or "")
    if in_story or in_flag:
        print(f"  ✓ {en}: story={in_story}, flag={in_flag}")
    else:
        print(f"  ✗ {en}: story={in_story}, flag={in_flag}")

print("\n已触发:", get_state(session_id).triggered_events)
