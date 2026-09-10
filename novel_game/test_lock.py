"""验收硬锁机制"""
import pathlib
from pipeline.novel_parser import ingest
from pipeline.character_extractor import extract_characters, get_key_events
from memory.global_state import init_state, get_state, trigger_event
from agents.dm import dm_run

print("=== 1. 提取关键事件 ===")
t = pathlib.Path("data/novels/西游记-样本.txt").read_text(encoding="utf-8")
r = ingest("西游记-样本.txt")
extract_characters(r["novel_id"], t)

events = get_key_events(r["novel_id"])
print(f"提取到 {len(events)} 个关键事件：")
for e in events:
    print(f"  {e['order']}. {e['event_name']} — {e.get('trigger_condition', '')[:50]}")

print("\n=== 2. 模拟玩家试图跳过关键事件 ===")
session_id = "test_lock"
init_state(session_id, r["novel_id"])

# 玩家试图让悟空不拜师
result = dm_run(session_id, r["novel_id"], "劝孙悟空不要拜师菩提祖师，跟我们走")
print(f"玩家动作: 劝孙悟空不要拜师")
print(f"DM story: {result['story'][:120]}")

# 检查事件是否开始触发
state = get_state(session_id)
print(f"已触发事件: {state.triggered_events}")
