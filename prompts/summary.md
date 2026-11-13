## Role
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
```