# 计划：大文件导入体验整改（人物分段提取 + 上传异步化）

> 前一批（W1~W5：私聊身份 / 时间线开局 / 好感度与主角属性 / 情景特效 / 书架排序）已全部提交并验收，报告见 `执行报告-20260911-W1-W5.md`。本计划是**新一批**，只做你点名的两件事。

## 零、问题与实测依据（都不是猜的）

| 事实 | 证据 |
|---|---|
| 上传硬上限 10MB（≈350 万字 UTF-8） | 前端 `static/app.js:298`、后端 `api/route_novel.py:28-30` |
| 入库吞吐实测 **528 字/秒** | 真机 `ingest()` 全链路：51,118 字 → 96.9s（81 段） |
| 上传期间**整个后端不响应** | `route_novel.py:23` 是 `async def`，第 51 行直接调同步 `ingest()` → 阻塞事件循环 |
| 人物/关系/人设/关键事件**只来自前 8000 字** | `pipeline/character_extractor.py:121`（`max_chars=8000`）、`:148`（`text_sample = novel_text[:max_chars]`），调用处 `route_novel.py:55` 未传 max_chars |

结论：你说"单本 ≤ 30~50 万字先够用"——那条**不改**（embedding 速度的量级不动）。本批只解决「只吃开头 8000 字」和「上传期间卡死 + 只有一条 toast」。

## 一、改动包（建议顺序，逐个独立提交）

### P0 恢复/开场提示常驻（**已完成**：已提交 `0b275b4`，8890 实测通过）
- `static/app.js`：`toast(msg, sticky=false)` + `hideToast()`；`startGame`/`resumeGame` 用 sticky（这两个接口内含 LLM 调用，实测 4~40s）
- `static/index.html`：`app.js?v=15` → `v=16`
- 验收已完成：8890 实测点存档 → toast 常驻「正在恢复上次进度…」→ 成功后「已恢复存档」→ 2.6s 自动消失；正文 3 页 / `0 / 15` / 无 console 报错

### P1 人物分段提取 + 合并汇总（治「只吃开头」）—— **已完成**：已提交 `4618635`，A16 组真机全绿
**新增常量** `config.py`（放在 L69-74「RAG 检索配置」块内）：
```python
EXTRACT_SEGMENT_CHARS = 200_000   # 分段粒度：每 20 万字一块（你的原话）
EXTRACT_SAMPLE_CHARS  = 8_000     # 每块取开头 8000 字做样本送 LLM（= 旧 max_chars，见下方 A16-5）
EXTRACT_MAX_EVENTS    = 30        # 全书关键事件目标总量（按段数均分）
EXTRACT_MAX_NODES     = 60        # 合并后关系图节点上限（按 weight 降序截断，见 D4）
```

> `EXTRACT_SAMPLE_CHARS` **必须等于旧 `max_chars`（8000）**，不能图"每段多抽点"改成 12_000：
> 改了就等于改了单段书的抽取口径，A16-5"单段书与旧口径一致"当场不成立。覆盖度的提升靠
> **"每段都抽"**，不靠"每段抽更多"；8000 本就是已验收过的抽取质量基线。

**重构** `pipeline/character_extractor.py`（现有 4 个 prompt 保留原文，只改调用方式）：
| 函数 | 职责 |
|---|---|
| `_split_segments(text)` | 按 20 万字切块，返回块列表 |
| `_sample_of(block)` | 取块首 8000 字（**块首**——首段即全书开头，主角/开局口径不变） |
| `_extract_graph(sample)` | 原第一步（人物关系图） |
| `_extract_profiles(sample, names)` | 原第二步（NPC 人设），名单由调用方给：**该段自己的人物、去掉主角**，最多 10 个。注意这比旧代码（取前 10 个节点、含主角）少一人：主角由玩家扮演，本来就不需要人设档案，也不再被当成可对话 NPC |
| `_extract_events(sample, quota)` | 原第三步（关键事件），新增"本段最多 quota 条"上限 |
| `_merge_graph(graphs)` | 节点按 id 去重（weight 取最大）、边按 (source,target) 去重（先出现者胜）；**收尾按 weight 降序截断到 `EXTRACT_MAX_NODES`，被截掉的节点其边一并丢弃（不留悬空边，D3 力导图会画不出来）** |
| `_merge_profiles(profiles)` | 同一角色多段都有人设 → **最早出现的段胜出**（首次出场定人设，规则唯一） |
| `extract_characters(novel_id, novel_text, on_progress=None)` | 主流程：逐段抽 → 合并 → `normalize_event_orders(events)` 稠密重编号 → 主角属性仍只用第 1 段样本（1 次调用） |

**主角口径（关键）**：`get_protagonist_names()` 决定"玩家是谁"与"哪些 NPC 禁止私聊"（W1/W1b 已锁死），且 `is_protagonist()` 是**子串匹配**（`name in p`）。多段合并会让 `group=="主角"` 的节点变多，把真 NPC 也禁掉，所以：

> 1. **只有第 1 段能声明主角，后续段落里的 `主角` 一律降级为 `配角`；第 1 段没声明就保持空（与现状同语义）。**
> 2. **第 1 段内部也最多只保留 1 个 `主角` 节点**：按 weight 降序取最高者，其余降级为 `配角`。多主角会让 `get_protagonist_names()` 长度 >1，直接破坏 A14-1 与"禁私聊"口径。
> 3. 第 1 段的"主角唯一化"要在**合并之前**对该段 graph 做完，这样合并后节点列表里主角仍在原位——`_extract_player_stats` 用 `next(...)` 取第一个 `主角` 节点，才不会和 `get_protagonist_names()[0]` 打架。
> 4. **不要改 `is_protagonist()` 的子串匹配语义**。改成精确匹配会挂 A14-2（`test_contract.py:616` 明确断言 Router 回的简称「悟空」要认成主角「孙悟空」），简称回填是既有行为，不是 bug。

**事件配额公式**（保证"每段都有事件"且总量可控）：
`quota = min(15, max(3, ceil(EXTRACT_MAX_EVENTS / 段数)))`

| 段数 | 每段上限 | 全书上限 |
|---|---|---|
| 1（≤20 万字） | 15 | 15 —— **与现有口径一致**（样本同为前 8000 字；事件上界同为 15，旧 prompt 写的是"5-15 个"） |
| 2（30~40 万字） | 15 | 30 |
| 5（100 万字） | 6 | 30 |
| 18（350 万字） | 3 | 54 |

LLM 调用数 = `3 × 段数 + 1`（20 万字书 4 次；100 万字书 16 次）。

**影响面**：`route_novel.py:55`、`play.py:25` 的调用写法不变（仍传全文，`max_chars` 参数删除）。**老书不迁移**，沿用已有 `character_cache`；只有新上传走新口径（与 N5「只对新书生效」同口径）。

**结构契约红线（P1 必须逐字保持，最重要的一条）**：`extract_characters()` 的返回值被 6 个 getter 消费，调用点散在 **7 个文件**——`api/route_game.py`、`agents/dm.py`、`agents/graph.py`、`agents/npc.py`、`api/route_graph.py`、`memory/global_state.py`、`pipeline/prompts.py`。分段合并只允许改**内容**（节点/边/事件变多），**绝不允许改这 4 个 key 的名字、层级和元素字段**：

```
graph        = {"nodes": [{"id", "weight", "group"}], "links": [{"source", "target", "relation", "type"}]}
npc_profiles = {"角色名": {"personality", "secret", "speech_style", "goal"}}
key_events   = [{"event_name", "trigger_condition", "order", "key_characters"}]
player_stats = [{"name", "desc", "init"}]
```

这是运行期硬依赖：破坏它不是"某处报错"，而是 DM / NPC / 身份 / 时间线 / 百科 / 力导图**全线崩**。合并层只做"拼装 + 去重 + 截断"，不做任何重命名或包装。

**验收**：离线断言（`test_contract.py` 新增 A16 组，monkeypatch `_llm_call`，秒级、确定性）
- A16-1 抽样确实来自多个窗口（记录每次调用看到的文本 → 断言覆盖到第 2 块及以后）
- A16-2 节点/边去重 + weight 取最大；后续段的「主角」被降级，且**第 1 段多主角也只留 1 个**（`get_protagonist_names()` 长度恒 ≤ 1）
- A16-3 同一角色多段人设 → 最早段胜出
- A16-4 事件 order 跨段按段序稠密 1..N
- A16-5 **单块小说（n=1）与旧口径逐项一致**：样本 = 前 8000 字、事件上限 = 15、主角属性只调 1 次
- A16-6 节点数超 `EXTRACT_MAX_NODES` 时按 weight 截断，且**无悬空边**（每条 link 的两端都在 nodes 里）
- A16-7 合并结果的 4 个 key 与元素字段与旧结构逐字一致（结构契约红线的机器化守卫，防 P1 手滑改字段名）
- 真机：西游记样本重传 1 次（关系图/人设/事件仍可用）；**可选**（约 7 分钟）造 21 万字双段样本，放一个只在第 15 万字出现的角色，断言它出现在关系图里

### P2 上传异步化 + 进度查询（治「卡死 + 只有一条 toast」）—— **已完成，待用户过目后提交**（代码 + 两个测试脚本全绿，见文末「执行记录」）
**新增** `api/import_jobs.py`（api 层胶水，不新建目录，不产生新的跨层依赖）：
```python
JOBS: dict[str, dict]           # job_id -> {status, stage, progress, novel_id, chunk_count, title, error, ...}
create_job(filename, title)     # 同一时刻只允许 1 个 running 任务，已有则拒绝
start(job_id, file_path, title) # threading.Thread(daemon=True) 跑 worker
worker(): ingest(on_progress) -> extract_characters(on_progress) -> add_novel()
```
进度映射：向量入库 5→70%（按批 50 段回调），人物提取 70→99%（按段回调），完成 100%。已结束任务只在内存保留最近 20 条（不落盘，避免再造孤儿数据）。

**改** `api/route_novel.py`
- `POST /api/novel/upload`：大小/类型校验**保持同步**（失败仍返回 `{"error": ...}`，现有断言不变）→ 存文件 → 建任务起线程 → 立即返回 `{"job_id": "job_xxx", "status": "running"}`
- 新增 `GET /api/novel/import/{job_id}`：返回任务快照；未知 job_id → 404

**改** `pipeline/novel_parser.py`：`ingest(novel_file, on_progress=None)` 每批回调 `(done, total)`（`play.py` 不传 → 行为不变）
**改** `pipeline/character_extractor.py`：`extract_characters(..., on_progress=None)` 每段回调

**改** `static/app.js::uploadNovel`
- POST 拿 job_id → 写入 `localStorage`（**刷新页面也能继续显示进度**，导入可能几十分钟）→ 每 1.5s 轮询 → sticky toast 显示「向量入库 42% / 人物提取 3、5」→ done：清 localStorage、`loadLibrary()` 刷新书架、`startGame(novel_id, title)`；error：`toast('上传失败：…')`
- 页面初始化时若 localStorage 有 job_id → 自动恢复轮询

**契约测试适配**
- `test_contract.py::upload()` 助手改成「POST + 轮询到 done」，返回完成载荷 JSON 文本 → 3 个调用点（L850/L901/L1000）**一行都不用改**
- 新增 C 组 `check_import_job()`：C1 返回 job_id 而非 novel_id｜C2 未知 job_id → 404｜C3 `.exe` 仍同步 `{"error"}`｜C4 轮询到 `status=done` 且 `chunk_count>0`｜C5 **导入进行中 `/api/novel/list` 仍 200 且 <1s**（证明事件循环不再被阻塞）｜C6 已有 running 任务时再上传 → **409**（对应 D6）
- `test_tech_debt.py` L53-56：改为「上传 + 轮询」

### P3 文档对齐（不碰代码）—— **已完成，待用户过目后提交**

> 实际只动了 `README.md` / `ARCHITECTURE.md` / `DEV_GUIDE.md` 三份（与本行原写一致），`PRD.md` / `TECH_DEBT.md` 未动。
- `README.md`：§API（上传改异步 + 进度接口）、§核心设计 增 1 小节「人物分段提取汇总」、§配置项（**4 个**新常量：`EXTRACT_SEGMENT_CHARS` / `EXTRACT_SAMPLE_CHARS` / `EXTRACT_MAX_EVENTS` / `EXTRACT_MAX_NODES`）、§项目结构（`api/import_jobs.py`）、§当前状态
- `ARCHITECTURE.md`：§二 项目结构补 `api/import_jobs.py` 职责与 `character_extractor` 分段口径
- `DEV_GUIDE.md`：仅文首「现状」段补一行
- `PRD.md` / `TECH_DEBT.md`：**不动**（产品口径没变；若要记一条"上传不可中断/不持久化"的技术债，请示下）

## 二、验收手段（沿用上一批已跑通的路子）
1. 8890 起服务 → `CONTRACT_SCOPE=full python test_contract.py`（现 66 项 + 新增 A16/C 组，须全绿）
2. 8888 起服务 → `$env:PYTHONIOENCODING="utf-8"; python test_tech_debt.py`（须 18 通过 / 0 失败）
   - **必须带 `PYTHONIOENCODING=utf-8`**：该脚本用 `print("✓")`，GBK 控制台下会抛 `UnicodeEncodeError` 并以 exit=1 结束，看起来像逻辑失败，实际只是编码问题
   - 该脚本**不自清理**，跑完必须手动 `DELETE /api/novel/{id}` 把 `novel_xxx` 删掉，否则书架残留
3. 前端：CDP 无头 Edge 真机走一遍「上传 → 进度 → 自动进游戏」，并贴截图/DOM 断言
4. 测试产生的书一律 `DELETE /api/novel/{id}` 走代码路径清干净；不手改 `data/`
5. 8000（用户预览）只在最后重启一次，让新前端生效 ✅ 已重启：旧 PID 19768 停，新 PID **55224**

## 三、决策点（D1~D9 口径已确认，下表为最终口径）

| # | 决策 | 最终口径 |
|---|---|---|
| D1 | 关键事件是否也分段抽 | **分**（每段按配额抽、跨段按段序编号），否则时间线仍只反映开头 |
| D2 | 每段抽样位置 | **取块首 8000 字**（= 旧 `max_chars`，保证单段书口径不变；块首规则唯一，不做"首中尾各取一段"的复杂版）。覆盖度靠"每段都抽"，不靠"每段抽更多" |
| D3 | 主角唯一性 | **只有第 1 段能声明主角**，后续段落降级为配角；**且第 1 段内最多只留 1 个主角节点（按 weight 取最高）**。`is_protagonist()` 的子串匹配**不动**（A14-2 依赖它认简称） |
| D4 | 关系图节点上限 | **合并后按 weight 降序截断到 `EXTRACT_MAX_NODES = 60`**，被截节点的边一并丢弃（不留悬空边）。理由：`route_graph.py:12` 是**一次性全量**返回给 D3 力导图，100 万字以上不设上限会直接失控 |
| D5 | NPC 人设冲突 | **最早出现的段胜出** |
| D6 | 并发导入 | **同一时刻只允许一个导入任务**，第二个返回 **HTTP 409** + `{"error": ...}`（409 比 200+error 语义准确，前端也好分支） |
| D7 | 刷新页面 | **前端 localStorage 记住 job_id 继续显示进度**（后端任务不落盘，服务重启任务丢失）。**补一条**：重启后前端轮询会拿到 404，此时必须 `toast('导入任务已失效，请重新上传')` 并清掉 localStorage，不能静默转圈 |
| D8 | 陈旧脚本 | `diag.py` / `test_api.py` / `test_upload.py` / `test_shelf.py` / `test_lock*.py` 都会因上传接口变化失效——**只适配验收套件**（`test_contract.py` / `test_tech_debt.py`），这些陈旧脚本不动 |
| D9 | P0（提示常驻） | ✅ **已执行**：提交 `0b275b4`（`index.html` 当时为 `v=16`，P2 已一并升到 `v=17`） |
| — | 基线（已实测核对） | ✅ `test_contract.py` = **66 通过 / 0 失败 / 9 INFO**；`test_tech_debt.py` = **18 通过 / 0 失败**（须带 `PYTHONIOENCODING=utf-8`）。与本文档原记数字一致 |

### 决策点状态

D1~D9 口径**已全部确认**（D2 改为 8000、D3 加严为主角唯一、D4 改为 weight 截断 60、D6 改 409、D7 补 404 处理），本文档已同步为无缺口版本；P1 / P2 / P3 已按该口径执行完毕。

### 执行记录（P1 / P2 / P3 实况，2026-09-11）

| 项 | 状态 |
|---|---|
| P0 | ✅ 已提交 `0b275b4` |
| P1 | ✅ 已提交 `4618635`（A16 组真机全绿） |
| P2 | ✅ 代码完成 + 全部验证通过，**未提交**（等用户过目） |
| P3 | ✅ 文档完成，**未提交**（等用户过目） |

**P2 验收结果（本次实跑）**：契约全量 **79 通过 / 0 失败 / 10 INFO**（基线 66 + A16 + C 组 6）｜技术债 **18 通过 / 0 失败**｜CDP 无头 Edge 真机 **11 通过 / 0 失败**（上传→常驻进度→自动进游戏，3 张截图）｜C5：导入进行中 `/api/novel/list` = 200 / 0.019s。

**10 万字真机复核（用户指定）**：走新异步通道导入 100,000 字 → 上传返回 **0.03s**、总耗时 **210.3s**、进度单调 5→21→37→53→70→99→100%、`chunk_count=178`；导入期间采样 `/api/novel/list` 391 次，**最慢 0.401s / 平均 0.036s / 0 异常**（对照改造前「整段导入期间后端不响应」）。复核书已 DELETE。

**收尾**：测试残留 6 本书已 DELETE（书架恢复为 `novel_ca80a1b0` / `novel_6a14d1ae`）｜临时文件与 Edge 临时 profile 全清｜8000 预览已重启为 PID `55224`，前端 `?v=17`，`/api/novel/import/none` → 404 证明新代码生效。

**与本文档的两处实现偏差**（行为无差别，仅为记录）：
1. `start()` 实际签名为 `start(job_id, file_path)`，`title` 从 `JOBS[job_id]` 读取（避免同一参数传两份）。
2. 409 用 `JSONResponse` 返回 `{"error": ...}`（`HTTPException` 只会给 `{"detail": ...}`）；`app.js::api()` 取值改为 `err.detail ?? err.error` 以兼容两种体。

**遗留风险（已报告用户，未改代码）**：① 关系图按 weight 截断到 60 节点时**未给主角留豁免位**，主角可能被截掉（严格按 D4 口径）；② 边去重「先出现者胜」与早前面试话术「后段覆盖前段」相反（以 D 表为准）；③ 本文档 L88「第 15 万字」表述与 D2（每段只取块首 8000 字）不自洽，正确表述是「第 20 万字且落在该段块首 8000 字内」，P1 为**抽样式覆盖（8000/200000 = 4%）**，非全覆盖。

**已裁定（2026-09-11）**：`TECH_DEBT.md` **不补记**「导入任务不落盘 / 不可中断 / 不可续传」——导入幂等（重传即可），且 D7 已用「重启后 404 → 提示重新上传」兜住体验，属明确边界而非待修债务，因此保留在本节「不做 / 排除」中即可。`TECH_DEBT.md` 全文未改动。

**仍待清理确认**：`novel_game/_tmp_split_test.js` 是首次提交（`7bfdd14`）即被 git 跟踪的历史临时脚本，非本批产物，是否需要清除由用户决定。

## 四、不做 / 排除
- 不做 embedding 提速、不改切片参数（你没让做，且 110 分钟量级不变）
- 不做断点续传 / 任务落盘 / 迁移脚本
- 不动 `data/`、`chroma_db/` 运行数据
- 不动 `调试记录.md`、`我的新需求.md`、`image.png`
- 不新增任何依赖
