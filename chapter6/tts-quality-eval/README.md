# 实验 6-5：全自动 TTS 质量评估流水线

配套《深入理解 AI Agent》第 6 章「实验 6-5 ★★：构建全自动 TTS 质量评估流水线」。

用多种 **TTS 配置**（不同 model / voice / speed）合成同一组带挑战性的参考文本，再用
**多模态 LLM-as-a-Judge** 的思路对合成语音按 **Rubric** 逐维度打分，最后汇总成一张
**配置对比表**，反映不同配置在准确性 / 自然度上的优劣。

## 目的

回答工程中的实际问题：*同一段文本，`tts-1` 和 `tts-1-hd` 有多大差距？换 voice、把
语速调到 1.5x 会牺牲多少质量？* 本 demo 把这类对比做成**一条命令跑通、可复现**的流水线。

## 评审维度与 Rubric

对每条合成语音，先测出客观特征（时长、语速、字错误率），再让评审模型按 1–5 分打分：

| 维度 | 含义 |
|------|------|
| 清晰度 | 转写是否与原文一致（漏字/错字/多字越多分越低，对应准确性维度） |
| 自然度 | 语速是否接近自然朗读（中文约 4–6 字/秒，过快/过慢都扣分） |
| 停顿节奏 | 结合语速与文本长度判断节奏是否合理（过快常意味吞字） |
| 整体 | 综合印象分 |

客观指标 **CER（字错误率）/ 字准确率**：把 Whisper 回译文本与原文归一化（去标点空白、
统一大小写）后做字符级编辑距离，`CER = 编辑距离 / 参考字数`，`字准确率 = 1 - CER`。
中文按**字级**计算（等价于书中 WER 的可懂度维度）。

## Provider 适配说明

- **TTS 合成**：OpenAI `tts-1` / `tts-1-hd`（可选 `gpt-4o-mini-tts`）。不同 model /
  voice / speed 组合即一个「配置」。读 `OPENAI_API_KEY`。
- **质量评审（默认）**：**OpenAI 闭环，无需额外 key**。用 Whisper（`whisper-1`）把合成
  语音回译成文本算 CER，再用 `gpt-4o-mini` 基于「转写文本 + 时长 + 语速 + CER」按 Rubric
  打分。转写时用简体中文提示语引导 Whisper 输出简体，避免繁体字形差异虚高 CER。
- **质量评审（可选，书中方案）**：`--gemini` 让 **Gemini 多模态直接「听」音频**打分
  （原文 + 音频 + Rubric 一起输入），需 `GEMINI_API_KEY`。默认 Gemini 模型名会过期，
  代码会先探测 `/models` 选一个当前可用的（如 `gemini-2.5-flash`）。

> 书中用 Gemini 2.5 Pro 直接听合成语音打分；本 demo 默认改用「Whisper 回译 + LLM
> Rubric」以便**零额外配置即可跑通**，同时保留 `--gemini` 开关复现书中方案。两者的
> 区别：Gemini 能直接感知音色/韵律/情感；回译方案只能基于可测特征做保守推断（见「局限」）。

## 文件

| 文件 | 说明 |
|------|------|
| `config.py` | 模型名与单价、TTS 配置集合、带挑战性的测试语料 |
| `pipeline.py` | 合成 / ffprobe 时长 / Whisper 回译 / CER 计算 / LLM Rubric / 可选 Gemini |
| `demo.py` | 入口：多配置 × 多语料跑全流程，打印逐条明细 + 对比汇总表 |
| `requirements.txt` / `env.example` | 依赖与环境变量示例 |

## 运行

```bash
pip install -r requirements.txt          # 只需 openai
brew install ffmpeg                        # 提供 ffprobe（时长探测）
export OPENAI_API_KEY=sk-...

python demo.py            # 默认：4 个配置 × 4 条语料，Whisper 回译 + LLM Rubric
python demo.py --quick   # 只用前 2 条语料，快速冒烟
python demo.py --extra   # 额外加入 gpt-4o-mini-tts 配置
python demo.py --gemini  # 评审改用 Gemini 多模态直接听音频（需 GEMINI_API_KEY）
python demo.py --fresh   # 忽略已有音频全部重合成
```

合成音频写入 `output/`（已被 `.gitignore` 忽略），结构化结果写入 `output/results.json`。
**幂等**：默认复用已存在的音频，重复运行不会重复合成。

## 测试语料

4 条覆盖不同挑战点：数字/百分比/日期、多音字（行/长/重/还）、长句新闻文体、
专有名词 + 感叹情感。可在 `config.py` 的 `CORPUS` 中增删。

## 健壮性

- 缺 `OPENAI_API_KEY` 立即清晰报错退出；缺 `ffprobe` 给出安装提示。
- 单个（配置, 语料）在合成/转写/评审任一步失败，只把该条记为失败，**不中断整表**，
  汇总表按成功条数聚合。
- OpenAI 客户端带自动重试（`max_retries=5`）缓解偶发网络抖动。
- ffprobe 调用检查返回码与输出可解析性。

## 局限

- **默认评审看不到音频本身**：只基于回译文本 + 客观特征推断，无法直接判断音色一致度、
  真实韵律与情感表达（书中的 Gemini 方案能，用 `--gemini` 复现）。因此「自然度/情感」
  维度是保守估计。音色一致性维度需参考语音，本 demo 未覆盖。
- CER 依赖 Whisper 转写质量，Whisper 自身错误会引入噪声；数字/专名可能因书写形式
  （阿拉伯数字 vs 中文数字）产生非发音性差异。
- Rubric 由 LLM 打分，存在评审模型偏好；分数用于**相对对比**而非绝对基准。
