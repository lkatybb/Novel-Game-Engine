"""定位 get_untriggered_events 返回空的原因"""
import pathlib
from pipeline.novel_parser import ingest
from pipeline.character_extractor import extract_characters, get_key_events
from memory.global_state import init_state, get_state, get_untriggered_events

t = pathlib.Path("data/novels/西游记-样本.txt").read_text(encoding="utf-8")
r = ingest("西游记-样本.txt")
extract_characters(r["novel_id"], t)

session_id = "test_lock3"
init_state(session_id, r["novel_id"])

print("1. key_events 缓存数量:", len(get_key_events(r["novel_id"])))
print("2. session state novel_id:", get_state(session_id).novel_id)
print("3. r['novel_id']:", r["novel_id"])
print("4. 两者相等:", get_state(session_id).novel_id == r["novel_id"])

events = get_untriggered_events(r["novel_id"], session_id)
print("5. untriggered 数量:", len(events))
for e in events[:3]:
    print("   -", e["event_name"])
