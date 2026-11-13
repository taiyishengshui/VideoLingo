import json
import os
import re
from core.utils import *

# --------------------------------------------------------------- override
# Prompts are overridable via text files in prompts/ (project root):
#   summary.md / shared.md / faith.md / express.md
# Loading rule: file exists (non-empty) -> use the file; otherwise the
# built-in default below. Files may contain {{placeholders}} matching the
# built-in template variables (format_map, missing keys left as-is).
# The "术语 & 提示词" page edits these files; deleting a file restores the
# default. A .dist file is regenerated on demand for "恢复默认" comparisons.

_PROMPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


def _load_override(name: str) -> str | None:
    path = os.path.join(_PROMPTS_DIR, f"{name}.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content or None
    except OSError:
        return None


_PLACEHOLDER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _parse_template(template: str, variables: dict) -> str:
    """Stack-based renderer, handles nesting correctly.

    A single regex pass fails on nested cases like
    ``{{"src": "{{src_lang}} term"}}``: it matches the outer block first
    (``{"src": "{{src_lang}} term"}`` spans ``}} ... {{``) and collapses it
    before the inner variable is substituted. So we scan char by char:

    - ``{{identifier}}`` and the identifier is known  -> substitute
    - ``{{ ... }}`` anything else                      -> collapse to ``{ ... }``
      (literal JSON example), BUT the inner part is re-rendered first, so a
      variable nested inside survives: ``{{"{{src_lang}} term"}}`` ->
      ``{"English term"}``
    - unbalanced ``{{`` -> left untouched (user typo, must not crash a run)
    """
    out = []
    i, n = 0, len(template)
    while i < n:
        if template.startswith("{{", i):
            # find the matching closing }} — scan forward, tracking depth of
            # nested {{ }} so the OUTER one wins
            depth = 1
            j = i + 2
            while j < n:
                if template.startswith("{{", j):
                    depth += 1
                    j += 2
                elif template.startswith("}}", j):
                    depth -= 1
                    if depth == 0:
                        break
                    j += 2
                else:
                    j += 1
            if depth != 0:  # unbalanced — leave the typo alone
                out.append(template[i:i + 2])
                i += 2
                continue
            inner = template[i + 2:j]
            stripped = inner.strip()
            if _PLACEHOLDER_RE.match(stripped):
                # bare identifier placeholder
                if stripped in variables:
                    out.append(str(variables[stripped]))
                else:  # unknown var — keep literal, users define their own
                    out.append(template[i:j + 2])
            else:
                # escaped literal (JSON example etc.) — collapse to single
                # braces, but re-render the inside so nested vars fill
                out.append("{" + _parse_template(inner, variables) + "}")
            i = j + 2
        else:
            out.append(template[i])
            i += 1
    return "".join(out)


def render_prompt(name: str, variables: dict, template: str | None = None,
                  src_lang: str | None = None, tgt_lang: str | None = None) -> str:
    """THE prompt rendering path: override file if present, else the
    built-in default template. ``template`` bypasses both (UI preview of
    unsaved editor content); ``src_lang``/``tgt_lang`` override the config
    values (UI preview with editable language defaults).

    Placeholder rules:
    - {{var}}        -> replaced with variables[var] when known
    - {{unknown}}    -> stays literally (users may define their own)
    - {{ ... }}      (non-identifier content, e.g. {{"src": ...}}) -> collapsed
                      to single braces { ... }; nested {{var}} inside is
                      substituted first
    - {single}       -> left untouched (protects JSON examples)
    """
    if template is None:
        override = _load_override(name)
        template = override if override is not None else DEFAULT_TEMPLATES.get(name, "")
    if not template:
        return ""
    return _parse_template(template, variables)


# names exposed for the UI / playground ("which prompts are overridable")
OVERRIDABLE_PROMPTS = {
    "summary": "get_summary_prompt — 步骤4.1 总结+术语提取",
    "shared": "generate_shared_prompt — 翻译共享上下文块",
    "faith": "get_prompt_faithfulness — 直译提示词",
    "express": "get_prompt_expressiveness — 意译提示词",
    "verify": "get_verify_prompt — 翻译校验提示词（L4 质检）",
    "bili_meta": "get_bili_meta_prompt — B站投稿文案（标题/简介/标签）",
    "ad_detect": "get_ad_detect_prompt — 广告位识别（赞助中插行号区间）",
}

# Built-in default TEMPLATES ({{var}} placeholders). These are what the UI
# shows as the editable starting point when no override file exists, and
# what the functions fall back to. Keep in sync with the f-strings below —
# the functions render THESE templates, so there is exactly one source.
DEFAULT_TEMPLATES: dict = {
    "summary": """## Role
You are a video translation expert specializing in {{src_lang}} comprehension and {{tgt_lang}} expression.

## Task
1. Summarize the main topic in two sentences
2. Extract up to 15 professional terms/names with {{tgt_lang}} translations (excluding existing terms)
{{terms_note}}

## Input
<text>{{source_content}}</text>

## Output (JSON only)
```json
{{
  "theme": "two-sentence summary",
  "terms": [
    {{"src": "{{src_lang}} term", "tgt": "{{tgt_lang}} translation", "note": "brief explanation"}}
  ]
}}
```""",
    "shared": """### Context
<previous_content>{{previous_content}}</previous_content>
<subsequent_content>{{subsequent_content}}</subsequent_content>

### Summary
{{summary}}

### Terminology (use these translations consistently)
{{terms}}""",
    "faith": """## Role
You are a professional Netflix subtitle translator ({{src_lang}} → {{tgt_lang}}).

## Task
Translate each subtitle line faithfully, preserving meaning and terminology.

{{shared_prompt}}

<translation_principles>
1. Faithful: convey content accurately without adding or omitting
2. Accurate terminology: use correct professional terms consistently
3. Context-aware: reflect background and contextual relationships
</translation_principles>

## Input
<subtitles>{{lines}}</subtitles>

## Output (JSON only)
```json
{{json_format}}
```""",
    "express": """## Role
You are a professional Netflix subtitle translator and language consultant ({{src_lang}} → {{tgt_lang}}).

## Task
Improve direct translations to be natural and fluent. For each line:
1. Reflect on the direct translation (fluency, style, conciseness)
2. Provide a free translation natural to {{tgt_lang}} speakers
- No comments or explanations in translations
- No empty lines

{{shared_prompt}}

## Input
<subtitles>{{lines}}</subtitles>

## Output (JSON only)
```json
{{json_format}}
```""",
    "verify": """## Role
You are a strict translation QA reviewer ({{src_lang}} → {{tgt_lang}}). You did NOT make this translation — judge it independently.

## Context (what the translator had available)
{{shared_prompt}}

## Task
Review the translation below against the source, checking:
1. Completeness: any omitted or added meaning (per line)
2. Terminology: consistency with the terminology list above
3. Accuracy: meaning deviations, mistranslations
4. Naturalness: fluency in {{tgt_lang}}

## Input
<source>{{lines}}</source>
<translation>{{translation}}</translation>

## Output (JSON only)
```json
{{
  "score": 0,
  "issues": [
    {{"line": 0, "type": "omission|terminology|accuracy|naturalness", "detail": "what is wrong"}}
  ],
  "suggestion": "one-sentence improvement advice"
}}
```

Rules: score 0-10 (10 = publishable as-is). If the translation is fine, issues is an empty list. Line numbers refer to the line order in the input. Be strict but fair — flag REAL problems, not style preferences.""",

    # ---- B站投稿文案（GracePower 风格公式，源自 bili-video-meta skill 提炼） ----
    "bili_meta": """## Role
你是 B 站情感/成长类账号（GracePower 风格）的投稿文案编辑，为译介视频产出**可直接发布**的「标题 / 简介 / 标签」三件套。观众默认女性视角（女性成长 + 异性关系）。

## 输入（重要性从高到低）

### 1【一手素材 · 决定性】视频转录（{{transcript_meta}}）
下面是视频里**真实讲过的内容**。标题钩子、主题词、简介里的痛点/✔清单/答案金句、L3/L4 标签都必须从这里取材。
〈transcript〉
{{transcript}}
〈/transcript〉

### 2【次要参考 · 不得主导选题】原视频信息
- YouTube 原标题：{{video_title}}
- YouTube 原简介（常含广告/订阅/购书话术，只取与内容相关的信息）：
{{video_desc}}
只用于校准主题、人名、专有名词；**与转录冲突时一律以转录为准**，不得照搬宣传语。

### 3 生成参数
- EP 序号：{{ep}}
- 讲者名（标题第 4 段，一律英文名）：{{speaker}}
- 补充要求：{{extra_hint}}

## 取材铁律（违反即不合格）
1. 钩子 / 主题词 / ✔清单 / 答案金句 / 长尾标签，必须能在转录里找到出处（具体观点、场景、原话、案例）；不要发明视频没讲过的结论、数字或案例。
2. 优先挑转录里最反直觉、最能让观众对号入座的一句原话做钩子；简介⑤「答案金句」要复述转录里的核心洞见，不要套话。
3. 转录若是节选，没覆盖的部分不要编造细节；概括性表述可以，具体数字 / 引述不行。
4. 原视频信息只是背景；订阅、购书、广告一律忽略；不写「原标题 / 翻译 / 搬运 / 时长」。
5. 转录是中文译文时直接用中文表达，不生造英文概念；是英文原文时据此写自然中文，避免直译腔。

## 一、标题（生成 3 个候选，同一观点换三种钩子，套路不要重复）

唯一结构（四段，分隔符一律**全角 ｜**，不是半角 |）：
EP{{序号}}｜{{钩子}}｜{{主题词}}｜{{讲者}}

钩子 8 套路（按实测命中率排序，任选）：
1. 反常识疑问：为什么{{现象A}}，却{{现象B}}？　2. N个信号说明{{结论}}　3. 揭秘{{隐藏机制}}/{{现象}}的底层逻辑　4. 问句+数字组合　5. 否定式纠正：别再{{错误做法}}了，先{{正确做法}}　6. 越…越…悖论　7. 不是…而是…反转　8. 感叹式断言

钩子要求：9–44 字；推荐内含引号概念词（制造术语感）；可点名受众（男人/女生）；结尾 `？` 或 `！` 或无标点。

主题词（2–8 字，内容品类锚点，从下列选或按内容造）：恋爱心理学 / 男性心理 / 亲密关系 / 恋爱必修课 / 女性成长 / 回避型依恋 / 吸引力法则 / 两性关系 / 沟通技巧 / 真爱信号 / 情感清醒 / 失恋疗愈

全标题 40–70 字。序号零填充三位（如 EP042）。

## 二、简介（七段式骨架，缺一不可）

① 痛点反问（3–5 行，每行一短句，57% 以「为什么/是不是/你有没有」开头）
② 反转核心句（2–3 行）：真正{{决定结果}}的，不是{{表面原因}}，而是{{真实机制}}。
③ ✔ 清单（3–7 条）：`本集亮点：` 后每行 `   ✔ {{问句或反直觉断言}}？`；若标题含「N个信号」，条数必须与 N 一致
④ 困惑共鸣（4–6 行）：`也许你正困惑：` + 把观众的自我怀疑演出来，再松绑
⑤ 答案金句（3–4 行）：不伤自尊的归因 + 一句可截图转发的结论
⑥ 评论区互动（1–3 行）：开放式提问（如「这期提到的{{概念}}，哪个瞬间最触动你？」）或 A/B/C 选择题
⑦ 共情收尾（1–2 行）：你此刻写下的一句话，可能恰好就点亮了另一个{{处境}}里的{{人}}。

排版硬规则：**绝不空行**，每行一个短句靠换行做呼吸感；全文 350–600 字、20–28 有效行；中段禁止 # 话题（话题标签只允许在最后一行且可省略）；不写「原标题/翻译/搬运/时长」；不出现「首先/其次/总之/综上所述」等 AI 套话；不复述标题全文。

## 三、标签（恰好 10 个，四层混合）

L1 固定池必带 3 个：亲密关系、恋爱心理、两性关系
L2 轮换池挑 2–3 个：吸引力法则 / 恋爱技巧 / 女性成长 / 男性心理 / 情感心理学 / 上B站，聊情感 / 长期关系 / 情绪价值 / 女生必看
L3 本期主题词挑 2–3 个：与当期内容强相关（如 回避型依恋、调情信号、失恋疗愈、断联、忽冷忽热、边界感）
L4 长尾搜索词现造 1–2 个：观众会打进搜索框的大白话（如 他不回消息怎么办 / 怎么让男人主动 / 如何看清一个人），4–12 字，每期新造不复用

每个标签 ≤ 10 字，不用符号、不加 #，讲者名不进标签。

## 输出（严格 JSON，无任何多余文字）
```json
{{"titles": ["EP0xx｜…｜…｜…", "EP0xx｜…｜…｜…", "EP0xx｜…｜…｜…"], "desc": "第一行\\n第二行\\n…（不空行）", "tags": ["…", "…", "…", "…", "…", "…", "…", "…", "…", "…"]}}
```
titles 数组 3 个候选；tags 数组恰好 10 个；desc 内换行用 \\n。""",

    # ---- 广告位识别：只让 LLM 标行号，时间戳一律由代码从 SRT 映回 ----
    "ad_detect": """## Role
你是播客 / 视频的后期剪辑助理。任务：从转录稿里找出**所有可裁剪的赞助广告中插**，供发布前剪掉。

## 输入
素材：{{transcript_meta}}
下面是带行号的转录，每行格式 `[行号] mm:ss 文本`。**行号是唯一定位锚点**，必须原样引用，不要自己推算或改写时间戳。
〈transcript〉
{{transcript}}
〈/transcript〉

## 什么算广告段（宁可多报，不要漏）
1. 主持人念赞助口播：本期由 X 赞助 / 本集由 X 支持 / brought to you by / sponsored by / 感谢 X 赞助…
2. 品牌名 + 产品介绍 + 优惠码 / 折扣码 / 网址 / promo code / 打折话术（哪怕没出现「赞助」二字）
3. **只有一句的短口播也要报**，例如「本集由 X 支持」之后立刻回正文
4. 片头、片尾的独立赞助或推广段
5. 一段里换了品牌，按品牌拆成多段

## 什么不算
- 正文本身提到的书、播客、嘉宾、研究、案例（没有购买 / 优惠 / 网址话术）
- 主讲人自己的产品若就是本期主题，不算
- 与正文纠缠、无法干净裁掉的订阅号召：仍列出，但 confidence 填 low

## 行号规则（最关键）
- start_line = 广告段**第一行**行号，end_line = 广告段**最后一行**行号，都是 1-based 整数，范围 1..{{line_count}}
- 起止行都必须是广告内容本身；**不要把广告前最后一句正文、或广告后第一句正文圈进来**，边界定点程序会自己往前找上一句正文的结束时间
- 每段必须连续；中间夹了正文就拆成两段
- 行号区间不要重叠；按时间先后排序
- 一个广告都没找到，返回 {"ads": []}

## 输出（严格 JSON，无任何多余文字）
```json
{{"ads": [{{"brand": "品牌名", "start_line": 225, "end_line": 240, "note": "一句话说明（中文，30 字内）", "confidence": "high"}}]}}
```
confidence 只能是 high / medium / low。
{{extra_hint}}""",
}


def get_default_template(name: str) -> str:
    """The built-in default template of a prompt (for the UI editor)."""
    return DEFAULT_TEMPLATES.get(name, "")


## ================================================================
# @ step4_splitbymeaning.py
def get_split_prompt(sentence, num_parts=2, word_limit=20):
    language = load_key("whisper.detected_language")
    return f"""## Role
You are a professional Netflix subtitle splitter in **{language}**.

## Task
Split the subtitle into **{num_parts}** parts, each under **{word_limit}** words.
- Keep parts roughly equal in length (min 3 words each)
- Split at natural points (punctuation, conjunctions)
- For repeated words, split at the middle

## Steps
1. Analyze sentence structure and splitting challenges
2. Generate two alternatives with [br] tags at split positions
3. Compare and choose the best approach

## Input
<split_this_sentence>{sentence}</split_this_sentence>

## Output (JSON only)
```json
{{
    "analysis": "brief analysis",
    "split1": "first approach with [br] tags",
    "split2": "alternative approach with [br] tags",
    "assess": "comparison of both approaches",
    "choice": "1 or 2"
}}
```""".strip()


## ================================================================
# @ step4_1_summarize.py
def get_summary_prompt(source_content, custom_terms_json=None, *,
                       template=None, src_lang=None, tgt_lang=None):
    src_lang = src_lang or load_key("whisper.detected_language")
    tgt_lang = tgt_lang or load_key("target_language")

    terms_note = ""
    if custom_terms_json and custom_terms_json.get("terms"):
        terms_list = [f"- {t['src']}: {t['tgt']} ({t['note']})"
                      for t in custom_terms_json['terms']]
        terms_note = ("\n### Existing Terms (exclude from extraction)\n"
                      + "\n".join(terms_list))

    return render_prompt("summary", {
        "src_lang": src_lang, "tgt_lang": tgt_lang,
        "source_content": source_content, "terms_note": terms_note,
    }, template=template)


## ================================================================
# @ step5_translate.py & translate_lines.py
def generate_shared_prompt(previous_content_prompt, after_content_prompt, summary_prompt, things_to_note_prompt, *,
                           template=None):
    return render_prompt("shared", {
        "summary": summary_prompt,
        "terms": things_to_note_prompt or "",
        "previous_content": previous_content_prompt or "",
        "subsequent_content": after_content_prompt or "",
    }, template=template)


def get_prompt_faithfulness(lines, shared_prompt, *,
                            template=None, src_lang=None, tgt_lang=None):
    TARGET_LANGUAGE = tgt_lang or load_key("target_language")
    src_language = src_lang or load_key("whisper.detected_language")

    json_dict = {
        str(i): {"origin": line, "direct": f"direct {TARGET_LANGUAGE} translation {i}"}
        for i, line in enumerate(lines.split('\n'), 1)
    }
    json_format = json.dumps(json_dict, indent=2, ensure_ascii=False)

    return render_prompt("faith", {
        "src_lang": src_language, "tgt_lang": TARGET_LANGUAGE,
        "shared_prompt": shared_prompt, "lines": lines,
        "json_format": json_format,
    }, template=template)


def get_prompt_expressiveness(faithfulness_result, lines, shared_prompt, *,
                              template=None, src_lang=None, tgt_lang=None):
    TARGET_LANGUAGE = tgt_lang or load_key("target_language")
    src_language = src_lang or load_key("whisper.detected_language")

    json_format = json.dumps({
        key: {
            "origin": value["origin"],
            "direct": value["direct"],
            "reflect": "reflection on direct translation",
            "free": "free translation"
        }
        for key, value in faithfulness_result.items()
    }, indent=2, ensure_ascii=False)

    return render_prompt("express", {
        "src_lang": src_language, "tgt_lang": TARGET_LANGUAGE,
        "shared_prompt": shared_prompt, "lines": lines,
        "json_format": json_format,
    }, template=template)


def get_verify_prompt(lines, translation, shared_prompt, *,
                      template=None, src_lang=None, tgt_lang=None):
    """L4 QA: ask a (different) model to review one chunk's translation."""
    TARGET_LANGUAGE = tgt_lang or load_key("target_language")
    src_language = src_lang or load_key("whisper.detected_language")
    return render_prompt("verify", {
        "src_lang": src_language, "tgt_lang": TARGET_LANGUAGE,
        "shared_prompt": shared_prompt, "lines": lines,
        "translation": translation,
    }, template=template)


## ================================================================
# @ step6_splitforsub.py
def get_align_prompt(src_sub, tr_sub, src_part):
    targ_lang = load_key("target_language")
    src_lang = load_key("whisper.detected_language")
    src_splits = src_part.split('\n')
    num_parts = len(src_splits)
    src_part_display = src_part.replace('\n', ' [br] ')

    align_parts_json = ','.join(
        f'\n        {{"src_part_{i+1}": "{src_splits[i]}", "target_part_{i+1}": "aligned {targ_lang} part"}}'
        for i in range(num_parts)
    )

    return f"""## Role
You are a Netflix subtitle alignment expert ({src_lang} ↔ {targ_lang}).

## Task
Split the {targ_lang} subtitle to match the pre-split {src_lang} structure.
- Follow word order and structural correspondence
- No empty lines; rewrite if needed for natural alignment
- No comments in output

## Input
<subtitles>
{src_lang}: "{src_sub}"
{targ_lang}: "{tr_sub}"
Pre-split {src_lang} ([br] = split point): {src_part_display}
</subtitles>

## Output (JSON only)
```json
{{
    "analysis": "brief structural analysis",
    "align": [{align_parts_json}
    ]
}}
```""".strip()


## ================================================================
# @ step8_gen_audio_task.py @ step10_gen_audio.py
def get_subtitle_trim_prompt(text, duration):
    return f"""## Role
You are a professional subtitle editor shortening subtitles to fit voiceover duration.

## Input
<subtitles>Subtitle: "{text}" | Duration: {duration}s</subtitles>

## Rules
Reduce filler words and unnecessary modifiers without changing core meaning. Examples:
- "Please explain your thought process" → "Please explain thought process"
- "carefully analyze this complex problem" → "analyze this problem"

## Steps
1. Analyze structure, key info, and removable fillers
2. Trim to fit duration

## Output (JSON only)
```json
{{
    "analysis": "brief analysis",
    "result": "shortened subtitle in original language"
}}
```""".strip()


## ================================================================
# @ tts_main
def get_correct_text_prompt(text):
    return f"""## Role
You are a text cleaning expert for TTS systems.

## Task
Clean the text: keep only basic punctuation (.,?!), preserve meaning.

## Input
{text}

## Output (JSON only)
```json
{{"text": "cleaned text"}}
```""".strip()


## ================================================================
# @ services/bili_meta_service.py — B站投稿文案三件套
def get_bili_meta_prompt(video_title, video_desc, speaker, ep,
                         extra_hint="", transcript="", transcript_meta="",
                         *, template=None):
    """GracePower 风格标题/简介/标签（公式源自 bili-video-meta skill）。

    `transcript` 是决定性一手素材。用户在「术语 & 提示词」页保存过自定义
    模板时可能没写 {{transcript}} 占位符，那会把主素材整个丢掉 —— 检测到
    渲染结果里没有转录内容就强制追加一节，保证「转录为关键」不被模板绕过。
    """
    transcript = (transcript or "").strip()
    rendered = render_prompt("bili_meta", {
        "ep": ep,
        "speaker": speaker,
        "video_title": video_title,
        "video_desc": (video_desc or "").strip() or "（无）",
        "extra_hint": (extra_hint or "").strip() or "（无）",
        "transcript": transcript or "（无转录）",
        "transcript_meta": (transcript_meta or "").strip() or "（未标注）",
    }, template=template)
    if transcript and transcript[:64] not in rendered:
        rendered += ("\n\n## 一手素材（视频转录，以此为准；标题/简介必须据此取材）\n"
                     + transcript)
    return rendered


## ================================================================
# @ services/ad_service.py — 广告位识别
def get_ad_detect_prompt(transcript, transcript_meta="", line_count=0,
                         extra_hint="", *, template=None):
    """广告位识别：转录全文是唯一素材，行号是唯一定位锚点。

    与 bili_meta 同理：用户在「术语 & 提示词」页存了没写 {{transcript}}
    的自定义模板时，强制追加转录节，避免主素材被模板静默吞掉。
    """
    transcript = (transcript or "").strip()
    rendered = render_prompt("ad_detect", {
        "transcript": transcript or "（无转录）",
        "transcript_meta": (transcript_meta or "").strip() or "（未标注）",
        "line_count": line_count,
        "extra_hint": (extra_hint or "").strip() or "（无）",
    }, template=template)
    if transcript and transcript[:64] not in rendered:
        rendered += ("\n\n## 转录全文（以此为准；必须引用其中的行号）\n" + transcript)
    return rendered
