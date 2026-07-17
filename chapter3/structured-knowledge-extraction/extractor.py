"""
阶段 1：知识提取 Agent —— 用 LLM 从判例文本中抽取结构化因子。

输入：一段自然语言判决书事实描述。
输出：符合 schema 的结构化 JSON（金额 + 若干是非情节）。

要点：
  - 使用 response_format=json_object 强制模型输出 JSON；
  - 文本未提及的因子返回 null（对话 Agent 会据此判断"还缺什么信息"，从而追问）；
  - 提供 extract_dataset()，带磁盘缓存，避免每次重跑都重复调用 LLM 花钱。
"""
import json
import os

from config import MODEL, get_client
from schema import ALL_FACTORS, BOOL_FACTORS

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CACHE_PATH = os.path.join(DATA_DIR, "extracted.jsonl")

_FIELD_LINES = "\n".join(
    f'  - "{f.key}": {"金额数值(元, 整数)" if f.kind == "amount" else "true/false"}'
    f"  # {f.name_cn}"
    for f in ALL_FACTORS
)

SYSTEM_PROMPT = f"""你是一名协助司法数据分析的信息抽取助手。
请从给定的刑事判决书「事实」段落中，抽取以下结构化因子，并只输出一个 JSON 对象：
{_FIELD_LINES}

规则：
1. amount 为盗窃/涉案财物金额，单位为元，输出整数（去掉"元""人民币"等字样）。
2. 其余字段为布尔值：文本明确支持则 true，明确否定或表明相反情形则 false。
3. 如果某字段在文本中完全没有相关信息，则取值 null（不要臆测）。
4. 只输出 JSON，不要任何解释性文字。"""


def extract_one(fact_text: str, client=None) -> dict:
    """从单条判例文本抽取结构化因子。返回 dict，键与 schema 一致。"""
    client = client or get_client()
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"判决书事实段落：\n{fact_text}"},
        ],
    )
    raw = json.loads(resp.choices[0].message.content)
    return _normalize(raw)


def _normalize(raw: dict) -> dict:
    """把 LLM 输出规整成统一类型：amount->int|None，bool 字段->bool|None。"""
    out = {}
    amount = raw.get("amount")
    if isinstance(amount, str):
        amount = "".join(ch for ch in amount if ch.isdigit()) or None
    out["amount"] = int(amount) if amount not in (None, "") else None
    for f in BOOL_FACTORS:
        v = raw.get(f.key)
        out[f.key] = bool(v) if isinstance(v, bool) else (None if v is None else bool(v))
    return out


def load_dataset() -> list:
    """读取合成判例数据集。"""
    path = os.path.join(DATA_DIR, "cases.jsonl")
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def extract_dataset(use_cache: bool = True, verbose: bool = True) -> list:
    """对整个数据集做抽取，带缓存。

    返回 list，每项为 {id, fact, gold, label_months, extracted}。
    """
    cases = load_dataset()
    cache = {}
    if use_cache and os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    cache[rec["id"]] = rec["extracted"]

    client = get_client()
    results = []
    n_called = 0
    for c in cases:
        if c["id"] in cache:
            extracted = cache[c["id"]]
        else:
            extracted = extract_one(c["fact"], client=client)
            cache[c["id"]] = extracted
            n_called += 1
            if verbose:
                print(f"  抽取 {c['id']} ... 完成")
        results.append({**c, "extracted": extracted})

    # 落盘缓存
    with open(CACHE_PATH, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps({"id": r["id"], "extracted": r["extracted"]},
                                ensure_ascii=False) + "\n")
    if verbose:
        print(f"  本次实际调用 LLM {n_called} 次，其余命中缓存。")
    return results


def extraction_accuracy(results: list) -> dict:
    """把抽取结果和 gold 真值对比，报告每个字段的准确率（用于验证抽取质量）。"""
    fields = ["amount"] + [f.key for f in BOOL_FACTORS]
    correct = {k: 0 for k in fields}
    total = len(results)
    for r in results:
        gold, ext = r["gold"], r["extracted"]
        # amount 允许 1 元误差（一般应完全相等）
        if ext.get("amount") is not None and abs(ext["amount"] - gold["amount"]) <= 1:
            correct["amount"] += 1
        for f in BOOL_FACTORS:
            if ext.get(f.key) == gold[f.key]:
                correct[f.key] += 1
    return {k: correct[k] / total for k in fields}
