"""验证关键事件是否注入 DM prompt"""
import pathlib
from pipeline.novel_parser import ingest
from pipeline.character_extractor import extract_characters
from memory.global_state import init_state
from agents.dm import _build_prompt

t = pathlib.Path("data/novels/西游记-样本.txt").read_text(encoding="utf-8")
r = ingest("西游记-样本.txt")
extract_characters(r["novel_id"], t)

session_id = "test_lock2"
init_state(session_id, r["novel_id"])

prompt = _build_prompt(session_id, r["novel_id"], "劝孙悟空不要拜师")
# 找到 [必须发生的关键事件] 这一段
idx = prompt.find("[必须发生的关键事件]")
if idx >= 0:
    print("✓ 关键事件已注入 prompt")
    print(prompt[idx:idx+500])
else:
    print("✗ 关键事件未注入！")
    print("prompt 长度:", len(prompt))
    print("untriggered 事件检查一下...")

# 再走一轮触发某个事件
from agents.dm import dm_run
print("\n=== 第二轮 ===")
result = dm_run(session_id, r["novel_id"], "劝孙悟空不要拜师菩提祖师，跟我们走")
print("story:", result["story"][:150])
prompt2 = _build_prompt(session_id, r["novel_id"], "继续劝")
idx2 = prompt2.find("[必须发生的关键事件]")
print("prompt 中关键事件位置:", idx2)
if idx2 >= 0:
    print(prompt2[idx2:idx2+500])

from memory.global_state import get_state
print("已触发:", get_state(session_id).triggered_events)
