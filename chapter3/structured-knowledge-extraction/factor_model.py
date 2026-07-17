"""
阶段 2：因子重要性建模 —— 从(抽取因子 -> 刑期)样本里学出「判决经验模型」。

做法（可解释、非黑箱）：
  1. 把每条案例的结构化因子翻译成数值特征向量：
       - 金额取自然对数 ln(amount)（压缩量纲，符合"金额边际影响递减"直觉）；
       - 是非情节直接用 0/1。
  2. 拟合两套等价的线性回归：
       - 标准化模型：特征先 StandardScaler，再 LinearRegression。标准化后系数
         绝对值可横向比较 -> 用于「因子重要性排序」。
       - 原始尺度模型：直接在原始特征上 LinearRegression。其系数就是"该情节使
         刑期增/减多少个月"，用于「对单个案件做逐因子刑期拆解」（present 情节加上
         其月数、absent 情节不产生影响，直观且正确）。
  3. 报告拟合优度：训练集 R² 与 5 折交叉验证 R²。
  4. 保存一个结构化的「判决经验模型」(factor_model.json)，供对话 Agent 引用。

单案预测拆解（原始尺度）：
    预测刑期 = 基准刑期(中位金额、无任何情节) + 金额调整 + Σ(命中情节的月数)
"""
import json
import math
import os

import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler

from schema import BOOL_FACTORS, FEATURE_NAME_CN, FEATURE_NAMES

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
MODEL_PATH = os.path.join(DATA_DIR, "factor_model.json")


def factors_to_features(factors: dict) -> list:
    """把一个因子字典转成 FEATURE_NAMES 顺序的数值特征向量。

    缺失(None)的处理：金额缺失 -> 取 ln(1)=0（占位，调用方应避免对缺金额的样本训练）；
    布尔缺失 -> 0（视为"无此情节"）。
    """
    amount = factors.get("amount")
    ln_amount = math.log(amount) if amount else 0.0
    row = [ln_amount]
    for f in BOOL_FACTORS:
        v = factors.get(f.key)
        row.append(1.0 if v else 0.0)
    return row


class FactorModel:
    """线性回归封装：标准化模型给重要性排序，原始尺度模型给单案刑期拆解。"""

    def __init__(self, reg_raw, feature_names, ref_ln_amount, meta):
        self.reg_raw = reg_raw            # 原始尺度线性模型，用于预测与拆解
        self.feature_names = feature_names
        self.ref_ln_amount = ref_ln_amount  # 参考金额(训练集中位)的 ln 值
        self.meta = meta                  # 训练指标、系数表等

    def predict_months(self, factors: dict) -> float:
        x = np.array([factors_to_features(factors)])
        return float(self.reg_raw.predict(x)[0])

    def explain(self, factors: dict) -> dict:
        """把预测刑期拆成：基准 + 金额调整 + 各命中情节的月数（原始系数，直观）。

        - 基准刑期 = 无任何加/减情节、金额为训练集中位数时的预测；
        - 金额调整 = ln_amount 系数 ×(本案 ln金额 − 中位 ln金额)；
        - 布尔情节：命中(=1)时贡献其系数月数，未命中(=0)时贡献 0（不影响）。
        """
        coefs = dict(zip(self.feature_names, self.reg_raw.coef_))
        baseline = float(self.reg_raw.intercept_ + coefs["ln_amount"] * self.ref_ln_amount)

        contribs = []
        # 金额调整项
        amount = factors.get("amount")
        if amount:
            ln_amt = math.log(amount)
            contribs.append({
                "feature": "ln_amount",
                "name_cn": f"盗窃金额({amount}元)",
                "contribution_months": float(coefs["ln_amount"] * (ln_amt - self.ref_ln_amount)),
                "present": True,
            })
        # 布尔情节项
        for name in self.feature_names:
            if name == "ln_amount":
                continue
            present = bool(factors.get(name))
            contribs.append({
                "feature": name,
                "name_cn": FEATURE_NAME_CN[name],
                "contribution_months": float(coefs[name]) if present else 0.0,
                "present": present,
            })
        contribs.sort(key=lambda d: abs(d["contribution_months"]), reverse=True)
        predicted = baseline + sum(c["contribution_months"] for c in contribs)
        return {
            "predicted_months": float(predicted),
            "baseline_months": baseline,
            "contributions": contribs,
        }


def train(results: list, save: bool = True) -> FactorModel:
    """用抽取结果训练因子重要性模型。

    results: extract_dataset() 的返回，每项含 `extracted` 与 `label_months`。
    """
    X, y = [], []
    for r in results:
        ext = r["extracted"]
        if ext.get("amount") is None:  # 金额缺失的样本不参与训练
            continue
        X.append(factors_to_features(ext))
        y.append(r["label_months"])
    X = np.array(X)
    y = np.array(y, dtype=float)

    # 标准化模型：用于因子重要性排序
    scaler = StandardScaler().fit(X)
    Z = scaler.transform(X)
    reg_std = LinearRegression().fit(Z, y)

    # 原始尺度模型：用于单案刑期拆解（系数即"月/因子"）
    reg_raw = LinearRegression().fit(X, y)

    train_r2 = reg_raw.score(X, y)
    cv_r2 = cross_val_score(LinearRegression(), X, y, cv=5, scoring="r2").mean()

    raw_coefs = dict(zip(FEATURE_NAMES, reg_raw.coef_))
    ref_ln_amount = float(np.median(X[:, 0]))  # 参考金额=训练集金额中位数

    # 因子重要性 = |标准化系数|，降序；同时附上原始系数(月/因子)
    importance = sorted(
        [
            {
                "feature": name,
                "name_cn": FEATURE_NAME_CN[name],
                "std_coef": float(coef),                 # 标准化系数（可横向比较的权重）
                "raw_coef_months": float(raw_coefs[name]),  # 原始系数（月/单位）
                "effect": "从重(加刑)" if coef > 0 else "从轻(减刑)",
            }
            for name, coef in zip(FEATURE_NAMES, reg_std.coef_)
        ],
        key=lambda d: abs(d["std_coef"]),
        reverse=True,
    )

    meta = {
        "n_samples": int(len(y)),
        "train_r2": float(train_r2),
        "cv_r2": float(cv_r2),
        "baseline_intercept_months": float(reg_raw.intercept_),
        "reference_amount_yuan": float(math.exp(ref_ln_amount)),
        "importance": importance,
    }
    model = FactorModel(reg_raw, FEATURE_NAMES, ref_ln_amount, meta)

    if save:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(MODEL_PATH, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
    return model


def print_importance(model: FactorModel):
    m = model.meta
    print(f"  样本数: {m['n_samples']}  训练集 R²: {m['train_r2']:.3f}  "
          f"5 折交叉验证 R²: {m['cv_r2']:.3f}")
    print(f"  参考金额(中位): {m['reference_amount_yuan']:.0f} 元  "
          f"该参考金额、无任何情节时基准约 "
          f"{m['baseline_intercept_months'] + next(x['raw_coef_months'] for x in m['importance'] if x['feature']=='ln_amount') * math.log(m['reference_amount_yuan']):.1f} 个月")
    print("  因子重要性排序(标准化权重=可横向比较; 原始系数=月/因子):")
    for i, item in enumerate(m["importance"], 1):
        raw = item["raw_coef_months"]
        raw_str = (f"{raw:+.1f}月/情节" if item["feature"] != "ln_amount"
                   else f"{raw:+.1f}月/ln单位")
        print(f"    {i:>2}. {item['name_cn']:<12} 权重={item['std_coef']:+.2f}  "
              f"({raw_str})  {item['effect']}")
