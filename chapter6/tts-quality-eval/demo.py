"""实验 6-5：全自动 TTS 质量评估流水线 —— 一条命令跑通。

    python demo.py                # 默认 4 个配置 x 4 条语料，Whisper 回译 + LLM Rubric
    python demo.py --extra        # 额外加入 gpt-4o-mini-tts 配置
    python demo.py --gemini       # 评审改用 Gemini 多模态直接听音频（需 GEMINI_API_KEY）
    python demo.py --quick        # 只用前 2 条语料，快速冒烟

流程：多配置 TTS 合成 -> ffprobe 时长 -> Whisper 回译 -> CER/字准确率
      -> LLM Rubric 打分 -> 打印逐条明细 + 配置对比汇总表。
幂等：音频写入 output/ 并复用（除非 --fresh）。
"""

import argparse
import json
import os
import sys
import time
import traceback
from statistics import mean

import config
import pipeline

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def load_env():
    """极简 .env 加载（不引第三方依赖）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def audio_path(cfg_name: str, sample_id: str) -> str:
    return os.path.join(OUT_DIR, f"{cfg_name}__{sample_id}.mp3")


def evaluate_one(cfg, sample, use_gemini: bool, fresh: bool) -> dict:
    """对单个 (配置, 语料) 跑完整链路。任一步失败返回 error 记录，不抛出。"""
    rec = {"config": cfg.name, "sample": sample.id, "challenge": sample.challenge,
           "ok": False, "error": None}
    path = audio_path(cfg.name, sample.id)
    try:
        # 1) 合成（幂等：已存在且非 fresh 则复用）
        if fresh or not os.path.exists(path) or os.path.getsize(path) == 0:
            pipeline.synthesize(cfg, sample.text, path)
        # 2) 时长
        dur = pipeline.probe_duration(path)
        # 3) 回译
        hyp = pipeline.transcribe(path)
        # 4) CER / 字准确率
        er = pipeline.char_error_rate(sample.text, hyp)
        # 5) Rubric 打分
        if use_gemini:
            rub = pipeline.judge_gemini_audio(sample.text, sample.emotion, path)
        else:
            rub = pipeline.judge_rubric(sample.text, sample.emotion, hyp, dur, er.cer)
        rec.update({
            "ok": True, "duration": dur, "hypothesis": hyp,
            "cer": er.cer, "accuracy": er.accuracy,
            "speed": (er.ref_len / dur) if dur else 0.0,
            "scores": rub.scores, "reasons": rub.reasons,
        })
    except Exception as e:  # 单条失败不影响整表
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def fmt(x, nd=2):
    return f"{x:.{nd}f}" if isinstance(x, (int, float)) else str(x)


def print_detail(rec, sample_text):
    head = f"[{rec['config']} | {rec['sample']}] {rec['challenge']}"
    if not rec["ok"]:
        print(f"  {head}\n    !! 失败: {rec['error']}")
        return
    print(f"  {head}")
    print(f"    原文  : {sample_text}")
    print(f"    回译  : {rec['hypothesis']}")
    print(f"    时长  : {fmt(rec['duration'])}s   语速: {fmt(rec['speed'])} 字/秒"
          f"   CER: {fmt(rec['cer'],3)}   字准确率: {fmt(rec['accuracy']*100,1)}%")
    s, r = rec["scores"], rec["reasons"]
    for dim in pipeline.RUBRIC_DIMENSIONS:
        print(f"    {dim:<4}: {s.get(dim,'-')}/5  {r.get(dim,'')}")


def summarize(records):
    """按配置聚合：平均 CER、平均字准确率、各 Rubric 维度均分、成功数。"""
    by_cfg = {}
    for rec in records:
        by_cfg.setdefault(rec["config"], []).append(rec)
    rows = []
    for cfg_name, recs in by_cfg.items():
        ok = [r for r in recs if r["ok"]]
        row = {"config": cfg_name, "n_ok": len(ok), "n": len(recs)}
        if ok:
            row["cer"] = mean(r["cer"] for r in ok)
            row["accuracy"] = mean(r["accuracy"] for r in ok)
            for dim in pipeline.RUBRIC_DIMENSIONS:
                row[dim] = mean(r["scores"].get(dim, 0) for r in ok)
        rows.append(row)
    # 按整体分降序、CER 升序排序
    rows.sort(key=lambda x: (-x.get("整体", 0), x.get("cer", 1)))
    return rows


def print_table(rows):
    cols = ["整体", "清晰度", "自然度", "停顿节奏"]
    header = (f"{'配置':<18}{'成功':>6}{'字准确率':>10}{'CER':>8}"
              + "".join(f"{c:>9}" for c in cols))
    print(header)
    print("-" * 74)
    for r in rows:
        ok_str = f"{r['n_ok']}/{r['n']}"
        if not r.get("n_ok"):
            print(f"{r['config']:<18}{ok_str:>6}   (全部失败)")
            continue
        acc = f"{r['accuracy']*100:.1f}%"
        line = f"{r['config']:<18}{ok_str:>6}{acc:>10}{r['cer']:>8.3f}"
        line += "".join(f"{r.get(c,0):>9.2f}" for c in cols)
        print(line)


def main():
    ap = argparse.ArgumentParser(description="全自动 TTS 质量评估流水线（实验 6-5）")
    ap.add_argument("--extra", action="store_true", help="额外加入 gpt-4o-mini-tts 配置")
    ap.add_argument("--gemini", action="store_true", help="用 Gemini 多模态直接听音频评审")
    ap.add_argument("--quick", action="store_true", help="只用前 2 条语料快速冒烟")
    ap.add_argument("--fresh", action="store_true", help="忽略已有音频，全部重新合成")
    args = ap.parse_args()

    load_env()
    os.makedirs(OUT_DIR, exist_ok=True)

    if not os.environ.get("OPENAI_API_KEY", "").strip():
        print("错误：缺少 OPENAI_API_KEY。请 export 或写入 .env 后重试。", file=sys.stderr)
        sys.exit(1)

    configs = list(config.TTS_CONFIGS)
    if args.extra:
        configs += config.EXTRA_CONFIGS
    corpus = config.CORPUS[:2] if args.quick else config.CORPUS

    mode = "Gemini 多模态音频评审" if args.gemini else "Whisper 回译 + LLM Rubric"
    print("=" * 72)
    print(f"实验 6-5：全自动 TTS 质量评估流水线")
    print(f"评审模式：{mode}")
    print(f"配置数：{len(configs)}   语料数：{len(corpus)}   "
          f"共 {len(configs)*len(corpus)} 条待评估")
    print("=" * 72)

    records = []
    t0 = time.time()
    for cfg in configs:
        print(f"\n### 配置 {cfg.name}  (model={cfg.model}, voice={cfg.voice}, "
              f"speed={cfg.speed})")
        for sample in corpus:
            rec = evaluate_one(cfg, sample, args.gemini, args.fresh)
            print_detail(rec, sample.text)
            records.append(rec)

    rows = summarize(records)
    print("\n" + "=" * 72)
    print("配置对比汇总（按 整体 分降序）")
    print("=" * 72)
    print_table(rows)

    ok = sum(1 for r in records if r["ok"])
    print(f"\n完成：{ok}/{len(records)} 条成功，耗时 {time.time()-t0:.1f}s。")

    # 落盘结构化结果，便于二次分析
    out_json = os.path.join(OUT_DIR, "results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"records": records, "summary": rows}, f,
                  ensure_ascii=False, indent=2)
    print(f"明细结果已写入 {out_json}")


if __name__ == "__main__":
    main()
