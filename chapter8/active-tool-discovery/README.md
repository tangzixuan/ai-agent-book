# 实验 8-4：主动工具发现（Active Tool Discovery）

> 《深入理解 AI Agent》配套代码 · ★★★
>
> 在 **126 个跨领域工具**的工具库上，一次运行对比三种"工具发现"策略——**全量注入**、
> **检索预筛选**（一次性语义预选 top-n）、**主动发现**（执行中按需 `discover_tools`）——
> 并输出统一的对比表（准确率 / 注入 token / 延迟）。量化全量注入的 token 浪费，展示主动发现
> 如何用**嵌入向量相似度**把上百个工具收敛成几条精准候选，同时揭示"一次性预筛选"在多步跨领域
> 任务上的内在局限。**无 API key 也能跑**：`python demo.py --offline` 用本地嵌入 + mock 模型自检机制。

## 目的

当一个 Agent 拥有上百个工具时，常见做法是把全部工具的 JSON schema 一次性塞进 system prompt。
这会带来两个问题：

1. **token 浪费**：126 个工具的完整 schema 约 **1.16 万 token**，每一步推理都要重复计费。
2. **指令遵循退化**：措辞稍泛的任务下，模型会"广撒网"地把通用兜底工具（`web_search` /
   `google_search` / `universal_search`）和专用工具一起调用，甚至用通用搜索替代专用工具
   —— 即书中所说的"查股价却选了通用 web_search"。

**主动发现**只在 system 里保留少量基础工具 + 一个 `discover_tools(need)` 元工具。模型遇到能力缺口时，
用自然语言描述需求，系统用嵌入相似度从工具库检索 3-5 个最相关的专用工具，把它们的 schema 作为
**user message** 追加进对话（保护 system 前缀的 KV Cache），并更新状态栏可用工具列表。

## 机制

```
tools_library.py   126 个跨领域工具（finance/web/arxiv/github/geo/weather/media/... 共 17 个领域）
                   每个工具有真实 name/description/parameters；执行为轻量 mock（重点是"选对工具"）
                   其中故意混入 8 个"通用/近义"工具（web_search 等），它们的描述夸大自己无所不能
                   select_tools(size)：按 --tool-set-size 截取子集，演示"工具集越大全量注入越吃亏"
discovery.py       可插拔嵌入后端 + 工具向量索引；OpenAIEmbedder 用 text-embedding-3-small 生成向量
                   并缓存到 .cache/；search(need) = 把 need 向量化后与工具向量做余弦相似度返回 top-k
agent.py           三种策略的 ReAct 循环（文本协议：模型每步输出一个 JSON 工具调用）
                   - run_full_injection：126 个工具 schema 全部写进 system prompt
                   - run_retrieval_prefilter：按初始查询一次性检索 top-n 工具注入（书中"检索式预筛选"）
                   - run_active_discovery：基础工具 + discover_tools，执行中按需检索加载
offline_backend.py 离线后端：LocalEmbedder（本地哈希词袋嵌入）+ MockChatClient（脚本化 mock 模型），
                   让 --offline 无需任何 API key 即可跑通全流程（token/延迟真实，准确率仅反映启发式路由）
demo.py            对同一组任务分别跑所选策略，打印 token / 延迟 / 调用轨迹 / 是否精确选对，并汇总对比表
```

**为什么用"文本注入 + 文本解析"而不是 OpenAI 原生 function calling？**
原生 function-calling 接口对工具选择做了很强的约束优化，即使上百个工具也极少选错，无法体现书中
所述的"超长上下文指令遵循退化"。把 schema 当作纯文本塞进 prompt、让模型自己以 JSON 输出工具调用，
才是控制组的真实机制，也才能观察到退化。这也正是书中"把 schema 注入 system prompt（几万 token）"的写法。

**为什么嵌入检索能避免错选？** 通用工具 `web_search` 的描述"什么都能做"，语义被稀释；而专用工具
（如 `search_news`）描述聚焦。对一个聚焦的 `need`（"获取特斯拉最近的新闻"），聚焦的专用工具余弦相似度
更高、排在前面，通用工具往往进不了 top-k，于是根本不会被加载 —— 检索层天然起到了"精度过滤"作用。

**检索预筛选为什么不够？** 检索预筛选（`run_retrieval_prefilter`）只按**初始查询**做一次语义匹配、
一次性注入 top-n 工具。对"查股价 + 搜新闻"这类多步跨领域任务，初始查询的向量往往偏向第一个领域，
第二个子任务需要的专用工具可能挤不进 top-n，模型执行到一半才发现"想调用的工具根本没在清单里"——
这正是书中指出的一次性匹配的内在局限。主动发现把"发现"延后到执行中、按每个真实浮现的 `need` 分别检索，
从而补齐这一缺口（离线自检里可直接观察到：检索预筛选在半数多步任务上漏掉了第二个工具，见下表）。

## 运行

```bash
pip install -r requirements.txt

# 方式 A：离线机制自检（无需任何 key；token/延迟真实，准确率仅反映启发式路由）
python demo.py --offline

# 方式 B：真实模型（体现小模型"指令遵循退化"需要真实 LLM）
cp env.example .env    # 填入 OPENAI_API_KEY（chat 与 embeddings 都用 OpenAI）
# 兜底：若无 OPENAI_API_KEY 但设置了 OPENROUTER_API_KEY，chat 会自动改走 OpenRouter
#（模型映射到 openai/gpt-5.6-luna 等），工具检索退用本地哈希嵌入（OpenRouter 无 embeddings 接口）。
python demo.py                                   # 全部 8 任务 × 三种策略
python demo.py --strategies full,discovery       # 只跑其中两种策略对比
python demo.py --tasks finance+news,crypto+news  # 只跑指定任务（逗号分隔）
python demo.py --tasks 'opinion(诱导)'            # 含括号的任务 id 记得加引号
python demo.py --tool-set-size 20                # 缩小工具集，看全量注入的劣势如何随规模放大
python demo.py --query '查英伟达股价再搜点相关新闻' --offline   # 临时单条自然语言任务
python demo.py --offline --output results/offline.json         # 导出结构化结果
```

默认模型 `gpt-5.6-luna`，可用 `--model` 或 env 覆盖：`python demo.py --model gpt-5.6-luna`。
首次运行会为工具生成嵌入向量并缓存到 `.cache/`，之后复用。`python demo.py --help` 查看全部参数
（`--query / --tasks / --strategies / --tool-set-size / --top-k / --prefilter-n / --model / --embed-model / --max-steps / --offline / --output`）。

## 如何适配 / 扩展

- **换模型**：`MODEL=gpt-4.1-mini python demo.py`（chat 模型）；`EMBED_MODEL=text-embedding-3-large` 换嵌入模型
  （换嵌入模型会因签名变化自动重建 `.cache/` 索引）。
- **换供应商 / 网关**：chat 与 embeddings 都走 OpenAI SDK，`OpenAI()` 会自动读取环境变量 `OPENAI_BASE_URL`，
  因此指向任意 **OpenAI 兼容**的网关/代理只需 `OPENAI_BASE_URL=https://your-gateway/v1`（该端点需同时提供 chat 与 embeddings）。
- **换任务 / 输入**：编辑 `tools_library.py` 里的 `TASKS`（每条含 `prompt` 与判分用的能力槽位），或用
  `--tasks` 只跑其中几条，或用 `--query` 传一句临时需求；想扩充工具库同样在 `tools_library.py` 的 `ALL_TOOLS` 中增删。
- **离线自检**：`--offline` 用 `offline_backend.py` 的本地哈希嵌入 + 脚本化 mock 模型，无需任何 key，
  适合 CI、无网环境或快速验证流水线；它复现的是 token/延迟结构与"检索预筛选一次性漏工具"的机制，
  不复现"真实小模型的指令遵循退化"（后者见下方 gpt-4o-mini 真实结果）。

## 离线机制自检（本地嵌入 + mock 模型，`python demo.py --offline`）

下表为一次真实的 `--offline` 运行（8 任务 × 三策略）。**token/延迟是 tiktoken/wall-clock 真实测量**；
**准确率仅反映脚本化启发式路由，不代表真实模型能力**——mock 模型是"强路由器"，不会退化，所以全量注入也拿满分。

| 策略 | 精确选对 | 任务完成 | 平均注入 token | 总注入 token | 平均延迟(s) |
|---|---|---|---|---|---|
| 全量注入 | 8/8 | 8/8 | 11630 | 93040 | 0.008 |
| 检索预筛选 | 4/8 | 4/8 | 1030 | 8236 | 0.006 |
| 主动发现 | 8/8 | 8/8 | 974 | 7796 | 0.010 |

离线自检要传达的**两个真实、可复现的结构性结论**：

1. **token 随工具集规模放大而分化**：全量注入固定 11,630 token/任务；检索预筛选与主动发现按需只注入
   约 1,000 token（**~11.9× 精简**）。用 `--tool-set-size 20` 缩小工具集，差距收敛到 ~1.8×——印证"工具越多、
   全量注入越吃亏"。
2. **检索预筛选在多步跨领域任务上结构性漏工具**：一次性 top-10 检索在 8 个任务里有 4 个漏掉了第二个子任务
   所需的专用工具（如 `academic(诱导)` 的 top-10 里根本没有 `arxiv_search`），模型执行到一半调不到工具 →
   子任务失败；主动发现按每个真实浮现的 `need` 分别检索，8/8 补齐。

## 结论（基于一次真实运行，gpt-4o-mini，2026-07）

> 说明：下表是**全量注入 vs 主动发现**两策略的真实 LLM 运行（检索预筛选为本次改版新增策略，其真实-模型
> 数据需自行 `python demo.py` 复现，此处未臆造）。token 数因嵌入检索命中的工具不同，与离线自检略有差异，均为真实测量。

| 任务 | 全量注入 token | 主动发现 token | 全量精确? | 发现精确? |
|---|---|---|---|---|
| finance+news | 11630 | 686 | ✅ | ✅ |
| arxiv+download | 11630 | 1115 | ✅ | ✅ |
| github+viz | 11630 | 1036 | ✅ | ✅ |
| weather+calendar | 11630 | 1063 | ✅ | ✅ |
| forex+weather | 11630 | 742 | ✅ | ✅ |
| crypto+news | 11630 | 1015 | ✅ | ✅ |
| opinion(诱导) | 11630 | 657 | ❌ | ✅ |
| academic(诱导) | 11630 | 660 | ⚠️ | ⚠️ |
| **合计** | **93040** | **6974** | **6/8** | **7/8** |

1. **token 大幅节省（稳定、量化）**：全量注入每个任务固定注入 **11,630 token**；主动发现按需加载后
   仅 **657~1,115 token**，**平均精简约 13×**（8 个任务合计 93,040 → 6,974）。这是最稳健的收益。

2. **主动发现按需加载了正确工具、任务完成**：例如 `weather+calendar` 任务，模型两次调用
   `discover_tools`（"查询北京天气" → 命中 `get_weather_forecast`；"在日历添加活动" → 命中
   `create_calendar_event`），再依次调用这两个专用工具完成任务。

3. **"诱导任务"上，全量注入错选/滥用了通用工具，而主动发现选对了专用工具**：
   - `opinion(诱导)`：「特斯拉最近的新闻舆论风向」措辞偏泛。
     - **全量注入**广撒网调用了 **7 个工具**：`search_news, web_search, search_news, search_news,
       get_top_headlines, google_search, web_search` —— 把通用的 `web_search / google_search` 也一并
       用上（正是书中所说的"错选通用工具"）。
     - **主动发现**：检索返回 `search_news / get_news_by_source / get_analyst_ratings / search_tweets`
       （**没有** `web_search`），模型只调用了 `search_news + search_tweets` 两个专用工具，**干净利落**。

4. **如实说明的边界**：
   - 在**措辞明确**的 6 个任务上，gpt-4o-mini 即便面对 1.16 万 token 的工具墙也**没有选错**（两种模式都精确）。
     可见现代模型在"清晰任务 + 清晰工具名"下相当鲁棒 —— 此时主动发现的价值主要体现在 **token 节省**。
   - 退化主要出现在**措辞偏泛**的任务上（`opinion` / `academic`），此时模型倾向于"广撒网"顺手抓通用工具。
   - `academic(诱导)` 上主动发现**只是缓解、未完全消除**：因为该 `need` 很宽泛，嵌入检索偶尔会把
     `google_search` 也排进 top-k；但全量注入错选了 3 个通用工具（`web_search/google_search/universal_search`），
     主动发现只误用了 1 个，仍明显更精准、且少调用工具。
   - 用 env 换成更小/更弱的模型（如 `gpt-3.5-turbo`），token 结论完全一致；选择精度差异会更明显，
     但弱模型对 `discover_tools` 多步流程的遵循也更差，可能出现"忘记发现第二个能力"的情况——
     这说明主动发现同时也对模型的多步指令遵循提出了要求。

> 一句话：**主动工具发现的稳健收益是 token（~13×）；在措辞含糊、通用工具易被误用的场景下，
> 它还能通过"嵌入检索预筛选"过滤掉夸大其词的通用工具，显著提升工具选择精度。**

## 文件

- `tools_library.py` — 126 个工具定义 + `select_tools` 子集截取 + mock 执行 + 8 个评测任务与判分标准
- `discovery.py` — 可插拔嵌入后端（`OpenAIEmbedder`）+ 工具向量索引与相似度检索（`discover_tools`/预筛选的后端）
- `agent.py` — 三种策略（全量注入 / 检索预筛选 / 主动发现）的 ReAct 循环与 token 统计
- `offline_backend.py` — 离线后端：`LocalEmbedder` + `MockChatClient`，支撑 `--offline` 无 key 自检
- `demo.py` — 一键多策略对比演示（含 CLI：`--query/--tasks/--strategies/--tool-set-size/--offline/--output` 等）
- `requirements.txt` / `env.example`
