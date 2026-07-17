"""
判决因子模式（schema）定义。

本实验聚焦「盗窃罪」这一罪名，定义一套贴合数据的模块化因子：
  - 一个数值因子：盗窃金额（元）
  - 若干是非（0/1）情节因子：自首、退赃、前科/累犯、入户盗窃、携带凶器、认罪认罚、团伙作案

这些因子既用于：
  (1) 让 LLM 从判例文本中做结构化抽取；
  (2) 构造数值特征向量喂给回归模型学习「因子重要性」；
  (3) 对话 Agent 引用因子及其权重给出量刑建议。

若要迁移到真实 CAIL2018 或其他罪名，只需扩展本文件中的因子列表，
其余流水线（抽取 / 建模 / 对话）代码无需改动。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Factor:
    key: str          # 程序内部字段名（英文）
    name_cn: str      # 中文名，用于展示与提问
    kind: str         # "amount"（数值，单位元） 或 "bool"（是非情节）
    direction: str    # "aggravating"（从重）/"mitigating"（从轻）/"amount"
    question: str     # 当该因子缺失时，向用户提出的引导性问题


# 唯一的数值因子：盗窃金额
AMOUNT = Factor(
    key="amount",
    name_cn="盗窃金额(元)",
    kind="amount",
    direction="amount",
    question="本案涉及的盗窃金额大约是多少元？",
)

# 是非情节因子（0/1）
BOOL_FACTORS = [
    Factor("prior_record", "前科/累犯", "bool", "aggravating",
           "被告人此前是否有盗窃等犯罪前科、是否构成累犯？"),
    Factor("burglary", "入户盗窃", "bool", "aggravating",
           "作案时是否属于入户盗窃（进入他人住宅内实施）？"),
    Factor("carry_weapon", "携带凶器盗窃", "bool", "aggravating",
           "作案时是否携带凶器（如匕首等）？"),
    Factor("accomplice", "团伙作案", "bool", "aggravating",
           "是否系伙同他人共同作案（团伙）？"),
    Factor("surrender", "自首", "bool", "mitigating",
           "案发后被告人是否主动投案自首？"),
    Factor("restitution", "退赃退赔", "bool", "mitigating",
           "是否已退赔全部赃款或取得被害人谅解？"),
    Factor("confession", "认罪认罚", "bool", "mitigating",
           "被告人到案后是否如实供述、认罪认罚？"),
]

ALL_FACTORS = [AMOUNT] + BOOL_FACTORS

# 模型使用的特征列顺序（amount 取自然对数以压缩量纲）
FEATURE_NAMES = ["ln_amount"] + [f.key for f in BOOL_FACTORS]

# 特征名 -> 中文展示名，供解释输出使用
FEATURE_NAME_CN = {"ln_amount": "盗窃金额(对数)"}
FEATURE_NAME_CN.update({f.key: f.name_cn for f in BOOL_FACTORS})
