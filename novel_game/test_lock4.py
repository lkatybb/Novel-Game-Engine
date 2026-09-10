"""直接在 dm_run 链路上调试"""
import pathlib
from pipeline.novel_parser import ingest
from pipeline.character_extractor import extract_characters, get_key_events
from memory.global_state import init_state, get_state, get_untriggered_events, format_state

t = pathlib.Path("data/novels/西游记-样本.txt").read_text(encoding="utf-8")
r = ingest("西游记-样本.txt")
extract_characters(r["novel_id"], t)

session_id = "test_lock4"
init_state(session_id, r["novel_id"])

# 模拟 _build_prompt 里的 import 顺序
from memory.short_term import format_memory
from memory.global_state import format_state, get_untriggered_events
from memory.long_term import retrieve, format_context

short_mem = format_memory(session_id)
state_text = format_state(session_id)
retrieved = retrieve(r["novel_id"], "测试")
long_mem = format_context(retrieved)

print("state_text:", state_text[:200])
print()

# 直接调用
untriggered = get_untriggered_events(r["novel_id"], session_id)
print("untriggered 数量:", len(untriggered))

# 然后手动跑 dm_run
from agents.dm import dm_run
result = dm_run(session_id, r["novel_id"], "劝孙悟空不要拜师")

# 第二轮看 state
print("\n=== 第二轮 ===")
prompt_check = get_untriggered_events(r["novel_id"], session_id)
print("untriggered 还剩:", len(prompt_check))

from memory.global_state import get_state
print("已触发:", get_state(session_id).triggered_events)
