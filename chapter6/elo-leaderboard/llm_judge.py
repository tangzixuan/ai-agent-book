"""
LLM-as-judge pairwise battles with position-bias mitigation.

This is the only battle source that needs network access (an Anthropic API key
in ANTHROPIC_API_KEY); `simulate` and `arena` run fully offline.

The book (实验 6-6, 位置偏差 discussion) notes that an LLM judge systematically
favours whichever answer appears in a fixed slot (usually the first). The
standard mitigation, implemented here, is to judge each pair twice with the
answers swapped and only record a winner when both judgements agree; a
disagreement is counted as a tie. This cancels the position bias instead of
letting it leak into the ratings.

The resulting battle list uses the same {'model_a', 'model_b', 'winner'} schema
as the simulated and Chatbot Arena data, so it feeds straight into the Elo /
Bradley-Terry pipeline.
"""
import os
from typing import Dict, List, Optional

# Default candidate roster and judge (Claude models). Kept small because every
# battle costs several API calls (two responses + two swapped judgements).
DEFAULT_CANDIDATE_MODELS = ["claude-opus-4-8", "claude-haiku-4-5"]
DEFAULT_JUDGE_MODEL = "claude-opus-4-8"

DEFAULT_PROMPTS = [
    "用一句话解释什么是 Transformer 的自注意力机制。",
    "Write a haiku about distributed systems.",
    "给出快速排序的时间复杂度，并简要说明最坏情况。",
]

_JUDGE_SYSTEM = (
    "你是一个严格的评委。用户会给你一个问题和两个候选回答（回答 A 和回答 B）。"
    "请只根据回答质量判断哪个更好，忽略它们出现的顺序。"
    "只输出一个词：A、B 或 tie。"
)


def _get_client():
    """Create an Anthropic client, raising a clear error if the key is missing."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "The 'anthropic' package is required for the LLM-judge source. "
            "Install it with: pip install anthropic"
        ) from exc

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. The LLM-judge battle source needs an "
            "Anthropic API key; use --source simulate or --source arena to run "
            "the experiment fully offline."
        )
    return anthropic.Anthropic()


def _text(response) -> str:
    """Extract concatenated text from an Anthropic message response."""
    return "".join(block.text for block in response.content if block.type == "text").strip()


def generate_response(client, model: str, prompt: str, max_tokens: int = 1024) -> str:
    """Generate a single model answer for a prompt."""
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return _text(response)


def _judge_once(client, judge_model: str, prompt: str,
                answer_first: str, answer_second: str) -> str:
    """Ask the judge which slot is better; returns 'first', 'second' or 'tie'."""
    user = (
        f"问题：\n{prompt}\n\n"
        f"回答 A：\n{answer_first}\n\n"
        f"回答 B：\n{answer_second}\n\n"
        "哪个回答更好？只输出 A、B 或 tie。"
    )
    response = client.messages.create(
        model=judge_model,
        max_tokens=8,
        system=_JUDGE_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    verdict = _text(response).lower()
    if verdict.startswith("a"):
        return "first"
    if verdict.startswith("b"):
        return "second"
    return "tie"


def judge_pair(client, judge_model: str, prompt: str,
               answer_a: str, answer_b: str) -> str:
    """
    Judge a pair with position-bias mitigation (swap order, tie on disagreement).

    Returns 'model_a', 'model_b', or 'tie'.
    """
    # First pass: A in slot 1, B in slot 2.
    first_pass = _judge_once(client, judge_model, prompt, answer_a, answer_b)
    # Second pass: swap the slots so B is now in slot 1.
    second_pass = _judge_once(client, judge_model, prompt, answer_b, answer_a)

    # Translate both judgements into "which real model won", then require
    # agreement. Slot 1 in the first pass is A; slot 1 in the second pass is B.
    winner_first = {"first": "model_a", "second": "model_b", "tie": "tie"}[first_pass]
    winner_second = {"first": "model_b", "second": "model_a", "tie": "tie"}[second_pass]

    if winner_first == winner_second:
        return winner_first
    return "tie"  # inconsistent under swap -> position bias, count as tie


def run_llm_battles(candidate_models: Optional[List[str]] = None,
                    prompts: Optional[List[str]] = None,
                    judge_model: str = DEFAULT_JUDGE_MODEL) -> List[dict]:
    """
    Run LLM-judged battles between every model pair over every prompt.

    Args:
        candidate_models: Models to compare (default: DEFAULT_CANDIDATE_MODELS).
        prompts: Prompts to battle on (default: DEFAULT_PROMPTS).
        judge_model: Model used as the judge.

    Returns:
        List of battle dicts ({'model_a', 'model_b', 'winner'}).
    """
    candidate_models = candidate_models or DEFAULT_CANDIDATE_MODELS
    prompts = prompts or DEFAULT_PROMPTS
    if len(candidate_models) < 2:
        raise ValueError("Need at least 2 candidate models for LLM-judge battles")

    client = _get_client()
    battles: List[dict] = []

    for prompt in prompts:
        # Cache each model's answer per prompt so it is generated only once.
        answers: Dict[str, str] = {
            model: generate_response(client, model, prompt) for model in candidate_models
        }
        for i, model_a in enumerate(candidate_models):
            for model_b in candidate_models[i + 1:]:
                winner = judge_pair(
                    client, judge_model, prompt, answers[model_a], answers[model_b]
                )
                battles.append(
                    {"model_a": model_a, "model_b": model_b, "winner": winner}
                )

    return battles
