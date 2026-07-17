# 实验 5-8：生产日志的智能诊断系统

配套《深入理解 AI Agent》第 5 章「代码作为生成式能力 —— Agent 执行日志自动分析和问题诊断」。

## 目的

生产环境的 Agent 会产生大量**轨迹日志**（trajectory）。从中识别问题、定位根因、构建回归测试成本很高。
本实验让一个诊断 Agent 自动完成这条流水线：

**读轨迹集合 + 架构文档 + PRD → 定位问题、生成结构化报告 → 生成回归测试用例 → 重放框架真正执行验证 → (mock) 通过 MCP 对接 GitHub 创建 Issue。**

## 诊断流水线

```
data/trajectories.jsonl  (含已知问题的生产轨迹)
data/architecture.md     (系统架构)          ┐
data/PRD.md              (产品需求)          ├─► [LLM] diagnose()      结构化问题报告(优先级/模块/描述/建议)
                                             ┘        │
                                                      ▼
                                            [LLM] gen_test_cases()   回归测试用例(引用轨迹ID+交互轮次)
                                                      │
                                                      ▼
                                            replay.py 重放框架  ── 对同一输入重放被测系统并断言
                                              (A) 未修复系统 → FAIL(复现bug)
                                              (B) 修复后系统 → PASS(验证修复)
                                                      │
                                                      ▼
                                            github_mcp.py  (mock) 渲染并打印/落盘 GitHub Issue
```

- `diagnoser.py`：诊断 Agent，两次真实调用 OpenAI（gpt-4o-mini，JSON 模式）。
- `sut.py`：被测系统的**确定性仿真器**。`fixed=False` 复现线上 bug，`fixed=True` 模拟修复后行为。
- `replay.py`：回归测试重放框架。取轨迹输入 → 重放 `sut` → 在新轨迹上求值断言（内置 4 种断言 DSL）。
- `github_mcp.py`：GitHub Issue 创建，默认 mock（打印 + 写 `output/github_issues.json`）。

## 预置的已知问题（Agent 应能定位）

| 轨迹 | 问题 | 违反 PRD | 定位模块 |
|------|------|----------|----------|
| T-1001 / T-1002 | 退款前**跳过**了强制的 `verify_refund_eligibility` 校验 | R1 (P0) | order_service |
| T-1002 | `process_refund` **反复失败**、无退避、且最终误报成功 | R2 (P0) | payment_service |
| T-1003 | `check_stock` 延迟 8300ms **超时未降级** | R3 (P1) | inventory_service |
| T-1004 | 正常轨迹（对照组，无问题） | — | — |

## 运行

```bash
pip install -r requirements.txt
cp env.example .env      # 填入 OPENAI_API_KEY（模型默认 gpt-4o-mini）
python demo.py
```

`demo.py` 一次跑完：读轨迹 → 诊断报告 → 回归测试用例 → 重放执行(通过/失败) → (mock) GitHub Issue。

## 真实运行输出（节选）

诊断阶段，Agent 定位到全部 3 个预置问题：

```
[问题 1] 未进行退款资格校验
  优先级 : P0    模块: order_service    PRD: R1
  轨迹   : ['T-1001', 'T-1002']  关键轮次: [3]
[问题 2] 支付重试机制未正确实现
  优先级 : P0    模块: payment_service    PRD: R2
[问题 3] 库存查询延迟未降级处理
  优先级 : P1    模块: inventory_service    PRD: R3
```

回归测试用例被重放框架**真正执行**（先复现 bug、再验证修复）：

```
(A) 对『线上未修复』系统重放 —— 期望复现 bug（FAIL）
    [FAIL] RT-001 (T-1001)  工具 verify_refund_eligibility 缺失
    [FAIL] RT-002 (T-1002)  process_refund 调用 3 次, 失败 3 次, 末次失败
    [FAIL] RT-003 (T-1003)  check_stock 最大延迟 8300ms, 阈值 5000ms
(B) 对『修复后』系统重放 —— 期望修复被验证（PASS）
    [PASS] RT-001 (T-1001)  工具 verify_refund_eligibility 出现
    [PASS] RT-002 (T-1002)  process_refund 调用 2 次, 失败 1 次, 末次成功
    [PASS] RT-003 (T-1003)  check_stock 最大延迟 400ms, 阈值 5000ms
  小结：复现 bug 3/3 条；修复后通过 3/3 条。
```

mock GitHub Issue 打印并写入 `output/github_issues.json`，示例：

```
title  : [P0][order_service] 未进行退款资格校验
labels : ['module:order_service', 'priority:critical', 'auto-diagnosis']
body   : ## 问题描述 ... ## 关联回归测试用例 - RT-001 (轨迹 T-1001 第 3 轮) ...
```

## 回归测试断言 DSL（replay 框架内置）

Agent 生成的测试用例须使用以下断言之一，框架可自动求值：

- `step_present` `{tool}`：某工具必须出现（如强制前置校验）。
- `tool_succeeds` `{tool}`：某工具最终成功、且不存在"多次失败后误报成功"。
- `latency_under` `{tool, threshold_ms}`：某工具单次延迟低于阈值。
- `final_status_is` `{value}`：任务最终状态等于给定值。

## 接入真实 GitHub MCP（需 token，本实验不联网创建）

本实验默认 mock。接真实 GitHub 的方式：

1. 准备一个 GitHub Personal Access Token（`repo` 权限），写入 `.env` 的 `GITHUB_TOKEN`。
2. 启动官方 GitHub MCP Server（如 `npx -y @modelcontextprotocol/server-github`），通过环境变量把 token 传入。
3. 在 Agent 侧把 `github_mcp.create_issues(..., mock=False)` 改为经 MCP 客户端调用 `create_issue` 工具，
   传入 `build_issue()` 生成的 `title / body / labels / assignees`。
4. 出于安全，本仓库不内置联网创建逻辑（`mock=False` 会抛 `NotImplementedError` 提示改造点）。

## 局限

- 被测系统 `sut.py` 是**确定性仿真**，用于让回归测试可真正重放、给出稳定的通过/失败；真实场景下重放需对接实际系统或录制/回放的依赖桩。
- 诊断质量取决于 LLM；gpt-4o-mini 在本数据集稳定定位 3 个预置问题，更复杂的隐性问题可能需要更强模型或多轮反思。
- GitHub 创建默认 mock，不联网。
- 轨迹格式为简化示意，生产环境轨迹字段更丰富（token 用量、子 Agent 调用树等）。
