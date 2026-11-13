import json
import os
from core.prompts import get_summary_prompt
from core.utils import *
from core.utils.models import _3_2_SPLIT_BY_MEANING, _4_1_TERMINOLOGY
from core.services.translate_service import (
    active_term_names, load_active_terms, merge_terms,
    migrate_custom_terms_xlsx, _PROJECT_ROOT,
)

# Full-summary text (per job) — written alongside terminology.json so step
# 4.2 can carry the WHOLE summary in every chunk prompt (not just a theme
# line). 2026-09-09 redesign: covers the full transcript instead of the
# first `summary_length` chars.
_4_1_SUMMARY_TXT = os.path.join('output', 'log', 'summary.txt')


def combine_chunks():
    """Combine the text chunks identified by whisper into a single long text.

    Full text (not truncated): the summary call handles long input on
    modern 1M-context models. summary_length remains as a safety cap for
    extreme cases (e.g. 3-hour streams) via config."""
    with open(_3_2_SPLIT_BY_MEANING, 'r', encoding='utf-8') as file:
        # strip \r (CRLF file): otherwise it pollutes the joined summary text
        sentences = [l.rstrip('\r') for l in file.readlines()]
    cleaned_sentences = [line.strip() for line in sentences]
    combined_text = ' '.join(cleaned_sentences)
    cap = load_key('summary_length')
    if cap:
        combined_text = combined_text[:int(cap)]
    return combined_text


def get_summary():
    # one-time migration: legacy custom_terms.xlsx -> terms/default.json
    n = migrate_custom_terms_xlsx()
    if n:
        rprint(f"📖 Migrated {n} custom terms from custom_terms.xlsx → terms/default.json")

    src_content = combine_chunks()

    # checked base sets (terms/*.json, merged) — the BOTTOM PLATE. It goes
    # into the summary prompt as "already known, don't re-extract" AND into
    # the per-job glossary 4.2 will translate with.
    custom_terms_json = load_active_terms()
    plate_count = len(custom_terms_json['terms'])
    rprint(f"📖 底板术语 {plate_count} 条"
           f"（勾选基础库: {', '.join(active_term_names()) or '无'}）")

    summary_prompt = get_summary_prompt(src_content, custom_terms_json)
    rprint("📝 Summarizing and extracting terminology ...")

    def valid_summary(response_data):
        required_keys = {'src', 'tgt', 'note'}
        if 'terms' not in response_data:
            return {"status": "error", "message": "Invalid response format"}
        for term in response_data['terms']:
            if not all(key in term for key in required_keys):
                return {"status": "error", "message": "Invalid response format"}
        return {"status": "success", "message": "Summary completed"}

    summary = ask_gpt(summary_prompt, resp_type='json', valid_def=valid_summary, log_title='summary')
    # Job glossary = freshly extracted terms + the checked bottom plate.
    # The plate is appended LAST so a manually maintained base term always
    # wins over an LLM guess for the same src (merge_terms: later-wins).
    # This file — not terms/*.json — is the ONLY source step 4.2 reads.
    new_count = len(summary.get('terms') or [])
    summary['terms'] = merge_terms(summary.get('terms') or [],
                                   custom_terms_json['terms'])
    rprint(f"📖 任务术语表：LLM 新抽 {new_count} 条 + 底板 {plate_count} 条"
           f" → 去重后 {len(summary['terms'])} 条")

    os.makedirs(os.path.dirname(_4_1_TERMINOLOGY), exist_ok=True)
    with open(_4_1_TERMINOLOGY, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=4)
    rprint(f'💾 Summary log saved to → `{_4_1_TERMINOLOGY}`')

    # persist the full theme text for step 4.2 (every chunk carries it)
    theme = summary.get('theme') or ''
    if theme:
        os.makedirs(os.path.dirname(_4_1_SUMMARY_TXT), exist_ok=True)
        with open(_4_1_SUMMARY_TXT, 'w', encoding='utf-8') as f:
            f.write(theme)
        rprint(f'💾 Full summary saved to → `{_4_1_SUMMARY_TXT}`')


def load_full_summary() -> str:
    """Full summary text for translation prompts; falls back to the theme
    recorded in terminology.json, then to ''."""
    try:
        with open(_4_1_SUMMARY_TXT, 'r', encoding='utf-8') as f:
            text = f.read().strip()
        if text:
            return text
    except OSError:
        pass
    try:
        with open(_4_1_TERMINOLOGY, 'r', encoding='utf-8') as f:
            return json.load(f).get('theme', '') or ''
    except (OSError, ValueError):
        return ''


if __name__ == '__main__':
    get_summary()
