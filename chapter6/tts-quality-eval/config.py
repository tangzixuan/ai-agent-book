"""实验 6-5：全自动 TTS 质量评估流水线 —— 配置与测试语料。

本模块集中管理：
  - 用到的 OpenAI 模型名与计费单价（仅供参考成本估算）；
  - 多个 TTS「配置」（model / voice / speed 的组合，作为待对比的对象）；
  - 一组带挑战性的参考文本（数字 / 多音字 / 长句 / 专有名词 + 情感）。
"""

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 模型名（均为 OpenAI，读 OPENAI_API_KEY）。
# ---------------------------------------------------------------------------
WHISPER_MODEL = "whisper-1"        # 语音转写（回译），用于计算 WER/字准确率
JUDGE_MODEL = "gpt-4o-mini"        # LLM Rubric 评审模型

# 可选的 Gemini 音频评审（书中方案）。默认模型名会过期，这里用当前可用的名字，
# 并在运行时通过 REST /models 探测校正。仅当 --gemini 开启时才会用到。
GEMINI_MODEL_DEFAULT = "gemini-2.5-flash"

# 计费单价（美元），仅用于打印粗略成本，不影响评分。数值随官方调整可能变化。
PRICE = {
    "tts-1": 15.0 / 1_000_000,          # $ / 字符
    "tts-1-hd": 30.0 / 1_000_000,       # $ / 字符
    "gpt-4o-mini-tts": 12.0 / 1_000_000,
    "whisper-1": 0.006 / 60,            # $ / 秒
}


@dataclass
class TTSConfig:
    """一个待评估的 TTS 配置。name 需在整表内唯一。"""

    name: str
    model: str
    voice: str
    speed: float = 1.0

    def supports_speed(self) -> bool:
        # gpt-4o-mini-tts 不支持 speed 参数。
        return self.model in ("tts-1", "tts-1-hd")


# 默认对比的配置集合：覆盖 model（tts-1 vs tts-1-hd）、voice、speed 三个维度，
# 便于观察不同配置在准确性/自然度上的差异。
TTS_CONFIGS = [
    TTSConfig("tts1-alloy-1.0", model="tts-1", voice="alloy", speed=1.0),
    TTSConfig("tts1hd-alloy-1.0", model="tts-1-hd", voice="alloy", speed=1.0),
    TTSConfig("tts1-nova-1.0", model="tts-1", voice="nova", speed=1.0),
    TTSConfig("tts1-alloy-1.5", model="tts-1", voice="alloy", speed=1.5),
]

# 可选加入（--extra 开启）：gpt-4o-mini-tts。默认不加入以保证一定跑通。
EXTRA_CONFIGS = [
    TTSConfig("4omini-nova-1.0", model="gpt-4o-mini-tts", voice="nova", speed=1.0),
]


@dataclass
class Sample:
    """一条参考文本 + 期望情感标签（供 Rubric 情感维度参考）。"""

    id: str
    text: str
    challenge: str      # 该样本主要考察的挑战点
    emotion: str = "中性"


# 多样化测试语料：数字/日期、多音字、长句、专有名词+情感。
CORPUS = [
    Sample(
        id="num",
        text="2026年第三季度营收增长了37.5%，同比提升12个百分点。",
        challenge="数字/百分比/日期",
        emotion="中性",
    ),
    Sample(
        id="polyphone",
        text="银行行长正在重新调整这件事的重点，长此以往，还得还清所有欠款。",
        challenge="多音字（行/长/重/还）",
        emotion="中性",
    ),
    Sample(
        id="long",
        text="据报道，随着人工智能技术的快速发展，越来越多的企业开始将大语言模型"
             "应用于客户服务、内容创作和数据分析等场景，从而显著提升了运营效率。",
        challenge="长句/新闻文体",
        emotion="中性",
    ),
    Sample(
        id="emotion",
        text="太棒了！OpenAI 刚刚发布的新模型在 GAIA 基准测试上表现惊人！",
        challenge="专有名词 + 感叹情感",
        emotion="兴奋",
    ),
]
