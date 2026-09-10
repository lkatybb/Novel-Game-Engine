"""命令行测试脚本：上传小说 → 开始游戏 → 循环交互

编排与网页端共用同一张 LangGraph 图（agents/graph.py），保证 CLI 与 Web 行为一致。
"""

import logging
import pathlib

from pipeline.novel_parser import ingest
from pipeline.character_extractor import extract_characters
from memory.global_state import init_state, get_state
from agents.graph import run_graph

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def main():
    novel_file = "西游记-样本.txt"
    print(f"正在入库《{novel_file}》...")

    text = pathlib.Path(f"data/novels/{novel_file}").read_text(encoding="utf-8")
    r = ingest(novel_file)
    print(f"入库完成: {r}")

    extract_characters(r["novel_id"], text)
    print("人物提取完成\n")

    session_id = "cli_test"
    init_state(session_id, r["novel_id"])
    print("=" * 50)
    print("游戏开始！输入动作进行交互，输入 q 退出")
    print("=" * 50)

    while True:
        print()
        s = get_state(session_id)
        print(f"[位置: {s.player_location} | 关键事件: {s.triggered_events or '无'}]")
        action = input(">> ").strip()
        if not action or action.lower() == "q":
            print("游戏结束")
            break

        print("\n正在推演...")
        result = run_graph(session_id, r["novel_id"], action)

        if result["npc_dialogue"]:
            print(f"\n[NPC台词] {result['npc_dialogue']}")
        print(f"\n[剧情] {result['story']}")
        if result.get("choices"):
            print("\n[选项]")
            for i, choice in enumerate(result["choices"], 1):
                print(f"  {i}. {choice}")


if __name__ == "__main__":
    main()
