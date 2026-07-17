"""
阶段 3：对话式量刑建议 Agent。

它把阶段 2 学到的「判决经验模型」当成决策逻辑来用：
  1. 从用户的案情自然语言描述里抽取已知因子（复用阶段 1 的抽取器）；
  2. 对照因子重要性排序，找出"还缺失、但很重要"的因子，按重要性顺序生成引导性追问；
  3. 信息补全后，用模型预测刑期，并把预测拆解为各因子的贡献月数；
  4. 用 LLM 把这些"真实数字"组织成一段有依据、可解释的中文建议，
     显式引用关键因子及其权重/贡献，并附免责声明。

所有量刑数字都来自模型，LLM 只负责"把数字讲清楚"，不自行编造刑期。
"""
from config import MODEL, get_client
from extractor import extract_one
from schema import ALL_FACTORS, BOOL_FACTORS

_FACTOR_BY_KEY = {f.key: f for f in ALL_FACTORS}

DISCLAIMER = (
    "【免责声明】本回答由教学实验中的统计模型自动生成，仅用于演示"
    "『从数据中提取判决经验』这一技术，不构成任何法律意见。真实案件的量刑"
    "受法律条文、司法解释、地域与具体情节等大量因素影响，请务必咨询专业律师。"
)


class LegalAdvisorAgent:
    def __init__(self, model):
        self.model = model  # FactorModel 实例
        self.client = get_client()

    # --- 步骤 1：抽取用户案情中的已知因子 ---
    def extract_known(self, case_text: str) -> dict:
        return extract_one(case_text, client=self.client)

    # --- 步骤 2：找出缺失的重要因子并生成追问 ---
    def missing_important_questions(self, known: dict) -> list:
        """按模型重要性排序，返回仍缺失(值为 None)的因子的引导性问题。"""
        questions = []
        for item in self.model.meta["importance"]:
            key = item["feature"]
            factor_key = "amount" if key == "ln_amount" else key
            if known.get(factor_key) is None:
                q = _FACTOR_BY_KEY[factor_key].question
                questions.append({
                    "factor": factor_key,
                    "name_cn": _FACTOR_BY_KEY[factor_key].name_cn,
                    "weight": item["std_coef"],
                    "question": q,
                })
        return questions

    # --- 步骤 3+4：给出最终量刑建议（引用模型与关键因子） ---
    def advise(self, known: dict) -> str:
        exp = self.model.explain(known)

        # 组织给 LLM 的"事实依据"（全部来自模型的真实数字）
        # 只保留真正影响了刑期的项：金额调整 + 命中的情节
        top = [c for c in exp["contributions"] if abs(c["contribution_months"]) >= 0.05][:6]
        lines = [
            f"- 模型预测刑期：约 {exp['predicted_months']:.1f} 个月"
            f"（同类案件的基准刑期约 {exp['baseline_months']:.1f} 个月，再按下列本案因子加减）",
            "- 本案各因子对刑期的贡献（正=从重加刑，负=从轻减刑）：",
        ]
        for c in top:
            sign = "＋" if c["contribution_months"] >= 0 else "－"
            lines.append(
                f"    · {c['name_cn']}：{sign}{abs(c['contribution_months']):.1f} 个月"
            )
        evidence = "\n".join(lines)

        # 已知因子的可读描述
        known_desc = self._describe_known(known)

        system = (
            "你是一名严谨的司法数据分析助手。下面给出一个数据驱动模型对某盗窃案的"
            "量刑分析（数字均来自模型，不得改动）。请用中文写一段 150 字以内、条理"
            "清晰的量刑参考建议：先给出模型预测的大致刑期区间，再点明哪些因子对结果"
            "影响最大、方向如何（引用其贡献月数）。不要编造模型未给出的数字，也不要"
            "给出确定性承诺。不要重复免责声明（系统会另行附上）。"
        )
        user = f"已知案情因子：\n{known_desc}\n\n模型分析依据：\n{evidence}"

        resp = self.client.chat.completions.create(
            model=MODEL,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        advice = resp.choices[0].message.content.strip()
        return advice + "\n\n" + DISCLAIMER

    def _describe_known(self, known: dict) -> str:
        parts = []
        amt = known.get("amount")
        parts.append(f"盗窃金额：{amt} 元" if amt is not None else "盗窃金额：未知")
        for f in BOOL_FACTORS:
            v = known.get(f.key)
            tag = "是" if v is True else ("否" if v is False else "未知")
            parts.append(f"{f.name_cn}：{tag}")
        return "\n".join("  " + p for p in parts)
