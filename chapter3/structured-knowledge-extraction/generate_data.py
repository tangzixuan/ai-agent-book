"""
合成一个小样本「盗窃罪」判例数据集。

CAIL2018 数据量太大（数百万条），本实验自带一个可离线运行的小样本：
  - 每条案例包含一段自然语言 `fact`（判决书事实描述风格）；
  - 一个 `gold` 结构化因子字典（生成时的真值，用于评估抽取准确率）；
  - 一个 `label_months` 刑期（月），由一个「已知」的量刑公式加噪声生成。

刑期公式（真值，仅用于生成数据；建模阶段要从抽取结果里把它「学」回来）：
    months = -18 + 5.2*ln(金额)
             + 前科*11 + 入户*7 + 携带凶器*5 + 团伙*3
             - 自首*9  - 退赃*6  - 认罪认罚*3
             + 噪声N(0, 1.2)
    再裁剪到 [1, 120] 个月。

因此建模阶段学出的因子权重应当与上式方向一致：金额、前科为主要从重因子，
自首、退赃为主要从轻因子——这就是可验证的「真实结论」。

真实迁移：把本文件替换为读取 CAIL2018 的 `data_*.json`（每行含 `fact` 与
`meta.term_of_imprisonment`），其余流水线不变。
"""
import json
import math
import os
import random

random.seed(42)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUT_PATH = os.path.join(DATA_DIR, "cases.jsonl")

N_CASES = 48

# 真值量刑公式的系数
INTERCEPT = -18.0
LN_AMOUNT_COEF = 5.2
BOOL_COEF = {
    "prior_record": 11.0,
    "burglary": 7.0,
    "carry_weapon": 5.0,
    "accomplice": 3.0,
    "surrender": -9.0,
    "restitution": -6.0,
    "confession": -3.0,
}

NAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")
LOCATIONS = ["某小区", "某商场", "某写字楼", "某菜市场", "某手机专卖店", "某电动车停车棚"]
VICTIMS = ["被害人张某", "被害人李某", "被害人王某", "某超市", "某公司"]


def sample_case(i: int) -> dict:
    amount = int(round(random.uniform(1500, 400000), -1))  # 1500~40万元
    f = {
        "prior_record": random.random() < 0.5,
        "burglary": random.random() < 0.45,
        "carry_weapon": random.random() < 0.3,
        "accomplice": random.random() < 0.4,
        "surrender": random.random() < 0.45,
        "restitution": random.random() < 0.5,
        "confession": random.random() < 0.6,
    }

    # 依据真值公式计算刑期
    months = INTERCEPT + LN_AMOUNT_COEF * math.log(amount)
    for k, v in f.items():
        if v:
            months += BOOL_COEF[k]
    months += random.gauss(0, 1.2)
    months = int(max(1, min(120, round(months))))

    fact = build_fact(i, amount, f)
    gold = {"amount": amount, **f}
    return {"id": f"case_{i:03d}", "fact": fact, "gold": gold, "label_months": months}


def build_fact(i: int, amount: int, f: dict) -> str:
    name = "被告人" + random.choice(NAMES) + "某"
    prior = (
        "曾于前几年因盗窃罪被判处有期徒刑，刑满释放后不思悔改，系累犯。"
        if f["prior_record"]
        else "此前无违法犯罪记录。"
    )
    place = random.choice(LOCATIONS)
    if f["burglary"]:
        scene = f"翻窗进入被害人位于{place}的家中（入户）"
    else:
        scene = f"在{place}内"
    weapon = "，作案时随身携带一把匕首" if f["carry_weapon"] else ""
    accomplice = "伙同他人共同" if f["accomplice"] else "单独"
    victim = random.choice(VICTIMS)
    surrender = (
        "案发后主动到公安机关投案，并如实供述了上述事实，" if f["surrender"] else "后被公安机关抓获归案，"
    )
    restitution = (
        "已退赔全部赃款并取得被害人谅解。" if f["restitution"] else "赃款已被挥霍，未予退赔。"
    )
    confession = "被告人当庭表示认罪认罚。" if f["confession"] else "被告人当庭对指控予以否认。"

    return (
        f"{name}，男。{prior}"
        f"经审理查明：{name}{accomplice}{scene}{weapon}窃取{victim}财物，"
        f"经鉴定价值人民币{amount}元。{surrender}{restitution}{confession}"
    )


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    cases = [sample_case(i) for i in range(1, N_CASES + 1)]
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    months = [c["label_months"] for c in cases]
    print(f"已生成 {len(cases)} 条案例 -> {OUT_PATH}")
    print(f"刑期范围: {min(months)}~{max(months)} 个月，均值 {sum(months)/len(months):.1f}")


if __name__ == "__main__":
    main()
