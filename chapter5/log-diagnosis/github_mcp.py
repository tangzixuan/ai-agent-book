"""
github_mcp.py —— GitHub Issue 创建（默认 mock）

默认 mock：把"创建 Issue"渲染成将要提交的 Issue 结构，打印并写入本地文件，
不联网、不需要 token。真实接入 GitHub MCP 的方式见 README。
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, List

_OUT = os.path.join(os.path.dirname(__file__), "output", "github_issues.json")

# 优先级 -> GitHub label 的映射
_PRIORITY_LABEL = {"P0": "priority:critical", "P1": "priority:high",
                   "P2": "priority:medium", "P3": "priority:low"}


def build_issue(problem: Dict[str, Any], test_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把一条诊断问题 + 关联回归测试用例，渲染成 GitHub Issue 结构。"""
    prio = problem.get("priority", "P2")
    module = problem.get("module", "unknown")
    related = [tc for tc in test_cases
               if tc.get("trajectory_id") in problem.get("trajectory_ids", [])]

    body_lines = [
        f"## 问题描述\n{problem.get('description', '')}",
        f"\n## 涉及模块\n`{module}`",
        f"\n## 优先级\n{prio}",
        f"\n## 改进建议\n{problem.get('suggestion', '')}",
        f"\n## 相关生产轨迹\n" + ", ".join(problem.get("trajectory_ids", []) or ["(无)"]),
    ]
    if related:
        body_lines.append("\n## 关联回归测试用例")
        for tc in related:
            body_lines.append(
                f"- `{tc.get('test_id')}` (轨迹 {tc.get('trajectory_id')} "
                f"第 {tc.get('focus_turn')} 轮): {tc.get('description', '')}")

    return {
        "title": f"[{prio}][{module}] {problem.get('title', problem.get('description', ''))[:60]}",
        "body": "\n".join(body_lines),
        "labels": [f"module:{module}", _PRIORITY_LABEL.get(prio, "priority:medium"),
                   "auto-diagnosis"],
        "assignees": [problem.get("suggested_assignee", "")] if problem.get("suggested_assignee") else [],
    }


def create_issues(problems: List[Dict[str, Any]], test_cases: List[Dict[str, Any]],
                  mock: bool = True) -> List[Dict[str, Any]]:
    """为每条问题创建 Issue。mock=True 时打印+落盘，不联网。"""
    issues = [build_issue(p, test_cases) for p in problems]

    if mock:
        os.makedirs(os.path.dirname(_OUT), exist_ok=True)
        payload = {"created_at": datetime.now().isoformat(),
                   "mode": "mock", "issues": issues}
        with open(_OUT, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n[github_mcp:mock] 已将 {len(issues)} 个 Issue 写入 {_OUT}")
        for i, iss in enumerate(issues, 1):
            print(f"\n----- Mock GitHub Issue #{i} -----")
            print(f"title  : {iss['title']}")
            print(f"labels : {iss['labels']}")
            print("body   :")
            for ln in iss["body"].splitlines():
                print("  " + ln)
    else:
        # 真实接入：见 README。此处不联网，保持安全默认。
        raise NotImplementedError(
            "真实 GitHub 创建需配置 MCP + token，见 README『接入真实 GitHub MCP』章节。")

    return issues
