"""TTS 质量评估流水线的核心步骤。

一条评估链路：
  合成(OpenAI TTS) -> 时长探测(ffprobe) -> 回译(Whisper) -> 计算 CER/字准确率
      -> LLM Rubric 打分(gpt-4o-mini) [可选: Gemini 音频评审]

所有对外函数都做了健壮性处理：单条失败抛出带上下文的异常，由 demo.py 捕获后
在汇总表里记为失败，而不会中断整表。
"""

import base64
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

import config

# ---------------------------------------------------------------------------
# 客户端（带自动重试，缓解偶发的网络抖动）。
# ---------------------------------------------------------------------------
_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "缺少 OPENAI_API_KEY。请 `export OPENAI_API_KEY=sk-...` 或写入 .env。"
            )
        _client = OpenAI(api_key=key, max_retries=5, timeout=60.0)
    return _client


# ---------------------------------------------------------------------------
# 1) TTS 合成
# ---------------------------------------------------------------------------
def synthesize(cfg: config.TTSConfig, text: str, out_path: str) -> None:
    """用指定配置合成语音并写入 out_path（mp3）。失败抛异常。"""
    kwargs = dict(model=cfg.model, voice=cfg.voice, input=text)
    if cfg.supports_speed() and abs(cfg.speed - 1.0) > 1e-6:
        kwargs["speed"] = cfg.speed
    resp = get_client().audio.speech.create(**kwargs)
    audio = resp.content
    if not audio:
        raise RuntimeError("TTS 返回空音频")
    with open(out_path, "wb") as f:
        f.write(audio)


# ---------------------------------------------------------------------------
# 2) 时长探测（ffprobe）
# ---------------------------------------------------------------------------
def probe_duration(path: str) -> float:
    """返回音频时长（秒）。ffprobe 缺失或出错时抛异常。"""
    if shutil.which("ffprobe") is None:
        raise RuntimeError("未找到 ffprobe，请安装 ffmpeg（macOS: brew install ffmpeg）。")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {proc.stderr.strip()}")
    out = proc.stdout.strip()
    try:
        return float(out)
    except ValueError:
        raise RuntimeError(f"ffprobe 输出无法解析为时长: {out!r}")


# ---------------------------------------------------------------------------
# 3) 回译（Whisper 转写）
# ---------------------------------------------------------------------------
# 用简体中文提示语引导 Whisper 输出简体，避免它偶尔返回繁体导致 CER 被字形差异
# 虚高（那是转写脚本选择问题，不是 TTS 发音错误）。
_ZH_PROMPT = "以下是普通话简体中文的句子。"


def transcribe(path: str) -> str:
    with open(path, "rb") as f:
        tr = get_client().audio.transcriptions.create(
            model=config.WHISPER_MODEL, file=f, language="zh", prompt=_ZH_PROMPT,
        )
    return tr.text or ""


# ---------------------------------------------------------------------------
# 4) 文本归一化 + 字错误率（中文用字级 CER，等价于书中所说 WER 的可懂度维度）
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    """去掉标点/空白，只保留 CJK / 字母 / 数字，并小写，便于逐字比较。"""
    text = text.lower()
    return "".join(ch for ch in text if ch.isalnum())


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein 距离（字符级）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,        # 删除
                cur[j - 1] + 1,     # 插入
                prev[j - 1] + (ca != cb),  # 替换
            ))
        prev = cur
    return prev[-1]


@dataclass
class ErrorRate:
    cer: float          # 字错误率 = 编辑距离 / 参考字数
    accuracy: float     # 字准确率 = 1 - cer（下限 0）
    edits: int
    ref_len: int


def char_error_rate(reference: str, hypothesis: str) -> ErrorRate:
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    if not ref:
        return ErrorRate(0.0, 1.0, 0, 0)
    dist = _edit_distance(ref, hyp)
    cer = dist / len(ref)
    return ErrorRate(cer=cer, accuracy=max(0.0, 1.0 - cer), edits=dist, ref_len=len(ref))


# ---------------------------------------------------------------------------
# 5) LLM Rubric 评审（默认，OpenAI 闭环）
# ---------------------------------------------------------------------------
RUBRIC_DIMENSIONS = ["清晰度", "自然度", "停顿节奏", "整体"]

_JUDGE_SYSTEM = """你是严格的 TTS（文本转语音）质量评审专家。
你将拿到：原始参考文本、该文本的期望情感、由 Whisper 对合成语音回译得到的转写文本，
以及从音频客观测得的时长、语速（字/秒）和字错误率（CER）。
请据此对合成语音质量按 Rubric 逐维度打分（1-5 的整数，5 最好）：

- 清晰度：转写与原文是否高度一致（漏字/错字/多字越多分越低；CER 越高分越低）。
- 自然度：语速是否接近自然朗读（中文自然朗读约 4-6 字/秒；过快>7 或过慢<3 都不自然）。
- 停顿节奏：结合语速与文本长度，判断停顿/节奏是否合理（过快通常意味着吞字、节奏差）。
- 整体：综合以上给出的总体印象分。

注意：你看不到音频本身，只能基于以上可测特征做保守、可解释的判断。
只输出 JSON，格式：
{"清晰度": {"score": int, "reason": str},
 "自然度": {"score": int, "reason": str},
 "停顿节奏": {"score": int, "reason": str},
 "整体": {"score": int, "reason": str}}
reason 用一句简短中文说明。"""


@dataclass
class RubricResult:
    scores: dict            # 维度 -> int
    reasons: dict           # 维度 -> str
    raw: str = ""


def judge_rubric(reference: str, emotion: str, hypothesis: str,
                 duration: float, cer: float) -> RubricResult:
    """用 gpt-4o-mini 按 Rubric 打分。返回结构化分数 + 点评。"""
    chars = len(normalize(reference))
    speed = chars / duration if duration > 0 else 0.0
    user = (
        f"原始参考文本：{reference}\n"
        f"期望情感：{emotion}\n"
        f"Whisper 回译文本：{hypothesis}\n"
        f"音频时长：{duration:.2f} 秒\n"
        f"语速：{speed:.2f} 字/秒（参考文本 {chars} 个有效字符）\n"
        f"字错误率 CER：{cer:.3f}\n"
    )
    resp = get_client().chat.completions.create(
        model=config.JUDGE_MODEL,
        messages=[{"role": "system", "content": _JUDGE_SYSTEM},
                  {"role": "user", "content": user}],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    data = json.loads(raw)
    scores, reasons = {}, {}
    for dim in RUBRIC_DIMENSIONS:
        item = data.get(dim, {})
        if isinstance(item, dict):
            scores[dim] = int(item.get("score", 0))
            reasons[dim] = str(item.get("reason", "")).strip()
        else:  # 兼容模型直接返回数字
            scores[dim] = int(item)
            reasons[dim] = ""
    return RubricResult(scores=scores, reasons=reasons, raw=raw)


# ---------------------------------------------------------------------------
# 6) 可选：Gemini 多模态音频评审（书中方案）。用 REST，避免额外 SDK 依赖。
# ---------------------------------------------------------------------------
def _resolve_gemini_model(api_key: str) -> str:
    """探测当前可用的 Gemini 模型，避免默认名过期。"""
    import urllib.request
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read())
        names = [m["name"].split("/")[-1] for m in data.get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        for want in (config.GEMINI_MODEL_DEFAULT, "gemini-flash-latest", "gemini-2.5-pro"):
            if want in names:
                return want
        # 退而求其次：任意 flash 模型
        for n in names:
            if "flash" in n and "tts" not in n and "image" not in n:
                return n
    except Exception:
        pass
    return config.GEMINI_MODEL_DEFAULT


def judge_gemini_audio(reference: str, emotion: str, audio_path: str) -> RubricResult:
    """把合成音频 + 原文 + Rubric 一起交给 Gemini 多模态直接「听」并打分。

    需要 GEMINI_API_KEY。默认关闭；--gemini 开启。失败抛异常由上层记为失败。
    """
    import urllib.request
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺少 GEMINI_API_KEY，无法使用 Gemini 音频评审。")
    model = _resolve_gemini_model(key)
    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()
    prompt = (
        "你是 TTS 质量评审专家。请直接聆听下面这段合成语音，对照原始文本与期望情感，"
        "按 1-5 分为四个维度打分并给出简短理由，只输出 JSON："
        '{"清晰度":{"score":int,"reason":str},"自然度":{"score":int,"reason":str},'
        '"停顿节奏":{"score":int,"reason":str},"整体":{"score":int,"reason":str}}\n'
        f"原始文本：{reference}\n期望情感：{emotion}"
    )
    body = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "audio/mp3", "data": audio_b64}},
        ]}],
        "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    scores, reasons = {}, {}
    for dim in RUBRIC_DIMENSIONS:
        item = parsed.get(dim, {})
        scores[dim] = int(item.get("score", 0)) if isinstance(item, dict) else int(item)
        reasons[dim] = str(item.get("reason", "")).strip() if isinstance(item, dict) else ""
    return RubricResult(scores=scores, reasons=reasons, raw=text)
