# -*- coding: utf -*-
import sys
sys.path.insert(0, '.')
from agents.dm import _extract_scene_so_far as ex

full = '{"state_changes": {"scene": {"name": "\u5730\u4e0b\u505c\u8f66\u573a", "desc": "\u706f\u5149\u660f\u6697\uff0c\u811a\u6b65\u58f0\u56de\u8361"}, "location": "B1"}, "story": "\u4f60\u63a8\u5f00\u95e8\u3002", "choices": ["a"]}'

# 模拟逐字流式，检查状态演进
steps = []
gate_at = None
first_story_emission_possible = None
for i in range(1, len(full) + 1):
    head = full[:i]
    st, sc = ex(head)
    if st == 'present' and gate_at is None:
        gate_at = i
    if gate_at and '"story"' in head:
        first_story_emission_possible = i
        break
print('present at char', gate_at, '->', ex(full[:gate_at]))
print('story key appears at', first_story_emission_possible, '-> scene confirmed BEFORE story:', gate_at < full.index('"story"'))

# 无 scene（state_changes 闭合）
no_scene = '{"state_changes": {"location": "\u75c5\u623f", "flag": "x"}, "story": "\u4f60\u70b9\u70b9\u5934\u3002"}'
print('absent mid:', ex(no_scene[:30]))
print('absent closed:', ex(no_scene[:no_scene.index('"story"')]))

# scene:null
print('null:', ex('{"state_changes": {"scene": null}, "story": "x"'))

# state_changes 为 null
print('sc-null:', ex('{"state_changes": null, "story": "x"'))

# scene 有 name 无 desc（流式截断）
print('name-only:', ex('{"state_changes": {"scene": {"name": "\u5929\u53f0"}'))
# name 字符串未闭合
print('unclosed name:', ex('{"state_changes": {"scene": {"name": "\u5929'))
# 空 name
print('empty name:', ex('{"state_changes": {"scene": {"name": ""}'))

# 完整终态
print('final:', ex(full))
