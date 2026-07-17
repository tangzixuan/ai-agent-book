"""
demo.py —— 实验 5-8：生产日志的智能诊断系统（全流程演示）

流水线：
  读轨迹集合 + 架构 + PRD
    -> [LLM] 诊断：定位问题、结构化报告(优先级/模块/描述/建议)
    -> [LLM] 生成回归测试用例(引用轨迹ID+交互轮次)
    -> 重放框架真正执行：先复现 bug(FAIL)，再验证修复(PASS)
    -> (mock) 通过 MCP 对接 GitHub 创建 Issue

运行：
  cp env.example .env && 填入 OPENAI_API_KEY
  python demo.py
"""

import json
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from diagnoser import Diagnoser
import replay
import github_mcp

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


def _read(name):
    with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
        return f.read()


def _hr(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    if not os.getenv("OPENAI_API_KEY"):
        print("错误：未设置 OPENAI_API_KEY，请 cp env.example .env 后填入。")
        sys.exit(1)

    # ---------- 0. 读取输入 ----------
    architecture = _read("architecture.md")
    prd = _read("PRD.md")
    trajectories = list(replay.load_trajectories().values())
    _hr(f"步骤 0｜读取输入：{len(trajectories)} 条生产轨迹 + 架构文档 + PRD")
    for t in trajectories:
        print(f"  - {t['trajectory_id']}: {t['task']}（{len(t['turns'])} 轮）")

    agent = Diagnoser()

    # ---------- 1. 诊断：定位问题 ----------
    _hr("步骤 1｜Agent 诊断（真实调用 OpenAI）：定位问题并生成结构化报告")
    problems = agent.diagnose(architecture, prd, trajectories)
    if not problems:
        print("未诊断出问题（异常）。")
        sys.exit(2)
    for i, p in enumerate(problems, 1):
        print(f"\n[问题 {i}] {p.get('title', '')}")
        print(f"  优先级 : {p.get('priority')}    模块: {p.get('module')}    PRD: {p.get('prd_ref')}")
        print(f"  轨迹   : {p.get('trajectory_ids')}  关键轮次: {p.get('focus_turns')}")
        print(f"  描述   : {p.get('description')}")
        print(f"  建议   : {p.get('suggestion')}")

    # ---------- 2. 生成回归测试用例 ----------
    _hr("步骤 2｜Agent 生成回归测试用例（真实调用 OpenAI）：引用轨迹ID + 交互轮次")
    test_cases = agent.gen_test_cases(problems)
    for tc in test_cases:
        print(f"  {tc.get('test_id')}  轨迹={tc.get('trajectory_id')} "
              f"轮次={tc.get('focus_turn')}  断言={json.dumps(tc.get('assertion'), ensure_ascii=False)}")
        print(f"      说明: {tc.get('description')}")

    # ---------- 3. 重放框架真正执行 ----------
    _hr("步骤 3｜重放框架真正执行测试用例")
    print("(A) 对『线上未修复』系统重放 —— 期望复现 bug（FAIL）")
    buggy = replay.run_suite(test_cases, fixed=False)
    for r in buggy:
        flag = "PASS" if r["passed"] else "FAIL"
        print(f"    [{flag}] {r['test_id']}  ({r.get('trajectory_id')})  {r['detail']}")

    print("\n(B) 对『修复后』系统重放 —— 期望修复被验证（PASS）")
    fixed = replay.run_suite(test_cases, fixed=True)
    for r in fixed:
        flag = "PASS" if r["passed"] else "FAIL"
        print(f"    [{flag}] {r['test_id']}  ({r.get('trajectory_id')})  {r['detail']}")

    reproduced = sum(1 for r in buggy if not r["passed"])
    verified = sum(1 for r in fixed if r["passed"])
    print(f"\n  小结：复现 bug {reproduced}/{len(buggy)} 条；修复后通过 {verified}/{len(fixed)} 条。")

    # ---------- 4. mock GitHub Issue ----------
    _hr("步骤 4｜通过 MCP 对接 GitHub 创建 Issue（默认 mock，不联网）")
    github_mcp.create_issues(problems, test_cases, mock=True)

    _hr("完成｜读轨迹 -> 诊断报告 -> 回归测试用例 -> (mock) GitHub Issue 全流程跑通")


if __name__ == "__main__":
    main()
