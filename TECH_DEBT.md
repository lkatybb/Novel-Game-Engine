# 技术债登记

> 本文件登记**已知但本轮不修**的问题：要么是"已用缓解手段绕过、根因尚未定位"，要么是"缺陷已确认但修它会碰接口红线 / 需要产品口径裁定"。
>
> 与 `test_tech_debt.py` 的区别：那个文件是**回归测试**（跑起来验证行为），本文件是**清单**（记录为什么先不修、修的时候要付什么代价）。每条都标了"建议处理时机"，不是待办即做。

---

## 债务 1｜上游 502 的重试只是缓解，根因未定位

- **提交**：`c7b020c`「test: B8 开局对上游 502 做一次有界重试（消除非确定性）」
- **事实**：`test_contract.py` 的 B8 用例（删书后重新上传同一本小说 → 能入库 + 能开局）会**偶发**拿到 DeepSeek 侧 502，导致开局失败。当时的处置是在测试里加"有界重试一次"，让红灯不再随机出现。
- **为什么算债**：这只把**测试**的非确定性压下去了，没有回答"502 到底来自 DeepSeek 上游抖动，还是我们这边并发/超时参数设置不合理（`timeout=60s` + `max_retries=2`）"。生产路径上同一个 502 仍会直接变成一帧 `{"type":"error"}` 推给玩家。
- **修的时候要付什么**：需要抓一次真实的 502 响应体与请求 ID，判定责任方。若是上游抖动 → 属"不做兜底"范畴，接受并记录；若是我方参数问题 → 调 `config.py` 的超时/重试策略。
- **建议处理时机**：不阻塞交付。等下一次线上真出现玩家可感知的开局失败时再抓证据。

## 债务 2｜`state` 帧载荷冗余：同一份已触发事件名单发两遍

- **位置**：`api/route_game.py` 的 `_enrich_state`（`enriched = state.model_dump()` 之后手动挂 `enriched["triggered"] = triggered`）
- **事实**：`state.model_dump()` 本来就带 `triggered_events`（模型字段），随后又挂了内容**完全相同**的 `triggered`。于是每个 `state` 帧里同一份名单出现两次（`triggered_events` + `triggered`），`/start`、`/resume`、`/action` 三个挂载点都这样。
- **为什么先不修**：`triggered` 是**前端与契约测试正在消费的字段**（`app.js` 的时间线渲染、`test_contract.py` 的 A5、`test_tech_debt.py` 的 state 断言）。删掉任何一边都是改既有接口字段，属接口变更，需走评审；而它带来的开销是"每帧多一份字符串数组"，量级很小。
- **修的时候要付什么**：前后端 + 两个测试脚本**同一提交**改；要么只留 `triggered`（改模型序列化），要么只留 `triggered_events`（改前端与断言）。
- **建议处理时机**：下一次做"接口收口"时一起做，不为它单独开一次前后端联动改动。

## 债务 3｜关键事件 `order` 没做类型加固

- **位置**：`pipeline/character_extractor.py` 的 `normalize_event_orders`（排序键 `e.get("order", 999)`）
- **事实**：LLM 抽取的关键事件清单里 `order` 是模型自由给的。若某条给成字符串 `"3"`（而不是数字 `3`），`sorted` 会在 int 与 str 之间比较并抛 `TypeError`，整本书的入库/提取直接崩；若给成 `None`，`get("order", 999)` 拿到的仍是 `None`（默认值只在**键缺失**时生效），同样会在排序里炸。
- **已确认未加固**：函数只处理了"缺字段"和"跳号"，没有 `int()` 转换或 `isinstance` 校验。
- **为什么先不修**：需要先定处理口径 —— 遇到不可解析的 `order` 是"当成最大序号排到末尾"还是"整体丢弃该事件"？这属产品/契约口径，不是执行层能自定的。
- **建议处理时机**：与"关键事件抽取"相关的下一次迭代一起做；届时应同时在 `test_contract.py` 的 A10 组补一条"order 为字符串/None"的断言。

---

## 观察项（不构成债务，供评审参考）

1. **`memory/global_state.py` 的 `get_untriggered_events` 已失去生产消费者。** 现在只剩 `test_lock3.py` / `test_lock4.py` 两个开发期诊断脚本引用；DM Prompt 改用 `get_next_event`（只取一个事件）。保留而非删除，是因为删它会连带弄坏那两个脚本，超出清理范围。
2. **`get_next_event(session_id)` 与 `api/route_game._enrich_state` 的选取口径重复。** 两处都在算"未触发事件中 `order > max(已触发 order)` 的最小者"。合并需要给 `_enrich_state` 改签名（它拿的是 `state` 对象而非 `session_id`），属跨层重构，本轮不动。**改任何一处必须同步另一处**。
3. **模块级 `logger.info` 在默认启动下不可见。** `uvicorn` 默认 `LOGGING_CONFIG` 不含 `root`，`api/main.py` 与 `config.py` 都没有配 logging，root 停在 WARNING —— 所以业务模块里的 INFO 日志（如 DM 声明的 `triggered_events`、关键事件触发记录）必须显式 `logging.basicConfig(level=logging.INFO)` 才看得到。这不影响功能，但排查问题时容易误判为"日志没打"。
