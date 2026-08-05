import json
from core.utils import *

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
def get_summary_prompt(source_content, custom_terms_json=None):
    src_lang = load_key("whisper.detected_language")
    tgt_lang = load_key("target_language")

    terms_note = ""
    if custom_terms_json:
        terms_list = [f"- {t['src']}: {t['tgt']} ({t['note']})" for t in custom_terms_json['terms']]
        terms_note = "\n### Existing Terms (exclude from extraction)\n" + "\n".join(terms_list)

    return f"""## Role
You are a video translation expert specializing in {src_lang} comprehension and {tgt_lang} expression.

## Task
1. Summarize the main topic in two sentences
2. Extract up to 15 professional terms/names with {tgt_lang} translations (excluding existing terms)
{terms_note}

## Input
<text>{source_content}</text>

## Output (JSON only)
```json
{{
  "theme": "two-sentence summary",
  "terms": [
    {{"src": "{src_lang} term", "tgt": "{tgt_lang} translation", "note": "brief explanation"}}
  ]
}}
```""".strip()


## ================================================================
# @ step5_translate.py & translate_lines.py
def generate_shared_prompt(previous_content_prompt, after_content_prompt, summary_prompt, things_to_note_prompt):
    return f"""### Context
<previous_content>{previous_content_prompt}</previous_content>
<subsequent_content>{after_content_prompt}</subsequent_content>

### Summary
{summary_prompt}

### Notes
{things_to_note_prompt}"""


def get_prompt_faithfulness(lines, shared_prompt):
    TARGET_LANGUAGE = load_key("target_language")
    src_language = load_key("whisper.detected_language")

    json_dict = {
        str(i): {"origin": line, "direct": f"direct {TARGET_LANGUAGE} translation {i}"}
        for i, line in enumerate(lines.split('\n'), 1)
    }
    json_format = json.dumps(json_dict, indent=2, ensure_ascii=False)

    return f"""## Role
You are a professional Netflix subtitle translator ({src_language} → {TARGET_LANGUAGE}).

## Task
Translate each subtitle line faithfully, preserving meaning and terminology.

{shared_prompt}

<translation_principles>
1. Faithful: convey content accurately without adding or omitting
2. Accurate terminology: use correct professional terms consistently
3. Context-aware: reflect background and contextual relationships
</translation_principles>

## Input
<subtitles>{lines}</subtitles>

## Output (JSON only)
```json
{json_format}
```""".strip()


def get_prompt_expressiveness(faithfulness_result, lines, shared_prompt):
    TARGET_LANGUAGE = load_key("target_language")
    src_language = load_key("whisper.detected_language")

    json_format = json.dumps({
        key: {
            "origin": value["origin"],
            "direct": value["direct"],
            "reflect": "reflection on direct translation",
            "free": "free translation"
        }
        for key, value in faithfulness_result.items()
    }, indent=2, ensure_ascii=False)

    return f"""## Role
You are a professional Netflix subtitle translator and language consultant ({src_language} → {TARGET_LANGUAGE}).

## Task
Improve direct translations to be natural and fluent. For each line:
1. Reflect on the direct translation (fluency, style, conciseness)
2. Provide a free translation natural to {TARGET_LANGUAGE} speakers
- No comments or explanations in translations
- No empty lines

{shared_prompt}

## Input
<subtitles>{lines}</subtitles>

## Output (JSON only)
```json
{json_format}
```""".strip()


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
