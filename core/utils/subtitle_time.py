"""Subtitle timestamp alignment — shared tool, not a pipeline step.

Extracted from core/_6_gen_sub.py (2026-09-08). Step 6 uses it to generate
the final SRT files, and step 4-2 uses it for its own timing math — neither
should have to import the other's step module.

Pure pandas/re logic: no config, no LLM, no side effects beyond the
optional SRT files written when `output_dir` is given.
"""
import bisect
import os
import re


def convert_to_srt_format(start_time, end_time):
    """Convert time (in seconds) to the format: hours:minutes:seconds,milliseconds"""
    def seconds_to_hmsm(seconds):
        total_ms = max(0, int(round(float(seconds) * 1000)))
        hours, rest = divmod(total_ms, 3600000)
        minutes, rest = divmod(rest, 60000)
        seconds, milliseconds = divmod(rest, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    start_srt = seconds_to_hmsm(start_time)
    end_srt = seconds_to_hmsm(end_time)
    return f"{start_srt} --> {end_srt}"


def remove_punctuation(text):
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s]', '', text)
    return text.strip()


def _norm_tokens(text):
    """Normalized word tokens: lower-cased, punctuation stripped, empty dropped.

    The ONE tokenizer used on both sides (source sentence and ASR word row), so
    the two are always measured with the same ruler.
    """
    return [t for t in remove_punctuation(str(text).lower()).split() if t]


def _token_count(text):
    """Count word tokens, ignoring punctuation-only ones.

    A merged ASR correction ("in such a way") counts as 4 tokens — that is what
    keeps the projection in sync with the source sentences instead of starving
    the tail.
    """
    return len(_norm_tokens(text))


# --- anchor alignment knobs -------------------------------------------------
# Before consuming tokens, each sentence is located by CONTENT: its opening
# words are looked up near the cursor, so a divergence in one sentence can no
# longer push every following sentence later (the old pure-counting walk did
# exactly that and drifted by tens of seconds over a long video).
ANCHOR_LEN = 3           # how many opening words form the search key
ANCHOR_WINDOW = 600      # how many word tokens AHEAD of the cursor we may look
ANCHOR_BACK_WINDOW = 100  # how many word tokens BEHIND it (fixes over-eating)
ANCHOR_MAX_OFFSET = 3    # retries skipping the 1st/2nd word (LLM may rewrite it)


def _build_groups(texts):
    """Group rows as [a row carrying tokens] + [every empty row right after it].

    Blanked merge tails and punctuation-only rows belong to their neighbour; a
    group is consumed as a unit, which preserves full merged audio bounds.
    Returns list of (start_row, end_row, n_tokens).
    """
    groups = []
    cur = None
    for i, n in enumerate(_token_count(t) for t in texts):
        if cur is None:
            cur = [i, i, n]
        elif n > 0:
            groups.append(tuple(cur))
            cur = [i, i, n]
        else:
            cur[1] = i
    if cur is not None:
        groups.append(tuple(cur))
    return groups


def _find_anchor(sent_tokens, flat_tokens, positions, lo, cur, hi):
    """Index in [lo, hi) where this sentence's opening words actually occur.

    Preference order matters:
      1. the NEAREST match at or behind `cur` — a previous sentence over-ate and
         pushed the cursor past this sentence (the observed failure: subtitles
         drifting later and later);
      2. the FIRST match ahead of `cur` — a previous sentence under-ate.

    Returns None when the opening words cannot be found (LLM rewrote them, or
    the words genuinely are not in the ASR output) — the caller then falls back
    to plain token counting for that one sentence.
    """
    for offset in range(min(ANCHOR_MAX_OFFSET, len(sent_tokens))):
        key = sent_tokens[offset:offset + ANCHOR_LEN]
        if not key:
            break
        cands = positions.get(key[0])
        if not cands:
            continue
        k = len(key)

        # 1) nearest match at or behind the cursor (smallest rewind wins)
        ci = bisect.bisect_right(cands, cur) - 1
        while ci >= 0:
            j = cands[ci]
            if j < lo:
                break
            start = max(0, j - offset)  # skipped words have no audio of their own
            if start >= lo and flat_tokens[j:j + k] == key:
                return start
            ci -= 1

        # 2) first match ahead of the cursor
        ci = bisect.bisect_right(cands, cur)
        while ci < len(cands):
            j = cands[ci]
            if j + k > hi:
                break
            start = max(0, j - offset)
            if start >= lo and flat_tokens[j:j + k] == key:
                return start
            ci += 1
    return None


def get_sentence_timestamps(df_words, df_sentences):
    """Map each source sentence to a (start, end) timestamp span.

    First match the COMPLETE normalized source text in forward order, with a
    character-to-group map (including blank merge tails). This makes repeated
    sentences consume distinct occurrences and neutralizes split contractions.
    Only unmatched text uses the following bounded fallback:

    1. ANCHOR (content): the sentence's opening words are looked up in the word
       store around the cursor (a little behind it, further ahead of it).
       Whatever an earlier sentence over- or under-consumed is discarded here,
       so error never accumulates across sentences. A rewind means the previous
       sentence had swallowed words that belong to this one, so its end is then
       trimmed to this sentence's start — cues never overlap.
    2. PROJECTION (count): from that anchor the sentence consumes word groups
       until it has collected as many word tokens as it contains.

    The fallback preserves compatibility with:

      * punctuation / case / spelling divergence between the sentence text and
        the ASR word tokens (the old substring scan desynced its global cursor
        on the first mismatch and hard-failed the whole run);
      * punctuation-only rows ("—") — they carry 0 tokens, so they ride along
        with a neighbouring row instead of consuming one of the sentence's
        tokens;
      * asr_correct MERGE fixes — those blank out the consumed rows
        (`words[j]['text'] = ""`) but keep row count and every timecode
        identical, so a merged multi-word token ("in such a way") is 1 text row
        + 3 blank rows that still carry the merged word's audio, and the span
        reaches the last of them.

    Divergence degrades gracefully (warns, clamps to the tail) instead of
    raising.
    """
    time_stamp_list = []
    texts = list(df_words['text'])
    n_rows = len(texts)

    if n_rows == 0:
        return [(0.0, 0.0)] * len(df_sentences['Source'])

    groups = _build_groups(texts)

    # Flat token view: one entry per word token, tagged with the group it came
    # from. Anchoring searches this list; consuming walks `groups`.
    flat_tokens = []
    flat_group = []
    group_tok0 = [0] * len(groups)
    for g, (g_start, _g_end, _n) in enumerate(groups):
        group_tok0[g] = len(flat_tokens)
        for t in _norm_tokens(texts[g_start]):
            flat_tokens.append(t)
            flat_group.append(g)

    positions = {}
    for i, t in enumerate(flat_tokens):
        positions.setdefault(t, []).append(i)

    # Reference algorithm's whole-text match, normalized identically on BOTH
    # sides. Ignoring internal spaces handles spaCy contractions (did n't) and
    # ASR merge rows without guessing token counts. Each character maps back to
    # a complete group, including blank merge tails.
    normalized_groups = [re.sub(r"\s+", "", remove_punctuation(str(texts[a]).lower()))
                         for a, _, _ in groups]
    full_chars = "".join(normalized_groups)
    char_group = []
    group_char0 = []
    for g, value in enumerate(normalized_groups):
        group_char0.append(len(char_group))
        char_group.extend([g] * len(value))
    char_cursor = 0
    confirmed_char = 0
    previous_start_group = -1

    starts = df_words['start']
    ends = df_words['end']
    cursor = 0
    clamped = 0
    reanchored = 0
    unanchored = 0

    for idx, sentence in df_sentences['Source'].items():
        toks = _norm_tokens(sentence)
        sentence_chars = re.sub(r"\s+", "", remove_punctuation(str(sentence).lower()))
        if not sentence_chars:
            at = time_stamp_list[-1][1] if time_stamp_list else 0.0
            time_stamp_list.append((at, at))
            continue
        # Whole-text match at the current cursor first; only search FORWARD to
        # the next occurrence. Never reuse an earlier identical phrase.
        hit_char = full_chars.find(sentence_chars, char_cursor)
        if hit_char >= 0:
            first_g = char_group[hit_char]
            last_g = char_group[hit_char + len(sentence_chars) - 1]
            if first_g != cursor:
                reanchored += 1
            time_stamp_list.append((float(starts.iloc[groups[first_g][0]]),
                                    float(ends.iloc[groups[last_g][1]])))
            previous_start_group = first_g
            char_cursor = hit_char + len(sentence_chars)
            confirmed_char = char_cursor
            cursor = last_g + 1
            continue
        n = len(toks) or 1

        # ---- fallback: bounded content anchor + token count ----
        if toks and cursor < len(groups) and flat_tokens:
            cur_tok = group_tok0[cursor]
            floor_g = (char_group[confirmed_char] if confirmed_char < len(char_group)
                       else len(groups))
            floor_g = max(floor_g, previous_start_group + 1)
            floor_tok = group_tok0[floor_g] if floor_g < len(groups) else len(flat_tokens)
            lo = max(0, cur_tok - ANCHOR_BACK_WINDOW, floor_tok)
            hi = min(len(flat_tokens), cur_tok + ANCHOR_WINDOW)
            hit = _find_anchor(toks, flat_tokens, positions, lo, cur_tok, hi)
            if hit is None:
                unanchored += 1
            else:
                g = flat_group[hit]
                if g != cursor:
                    reanchored += 1
                    cursor = g

        # ---- stage 2: consume by token count ----
        previous_start_group = cursor
        start_row = groups[cursor][0] if cursor < len(groups) else n_rows - 1
        end_row = start_row
        remaining = n

        while remaining > 0 and cursor < len(groups):
            _, g_end, g_n = groups[cursor]
            end_row = g_end
            remaining -= g_n
            cursor += 1

        if remaining > 0:
            # Severe divergence: not enough word tokens left. Clamp to the tail
            # instead of crashing — the affected subtitle may be slightly off,
            # but the rest of the run survives.
            clamped += 1
            start_row = min(start_row, n_rows - 1)
            end_row = n_rows - 1
            if clamped <= 3:
                print(f"\n⚠️ Warning: ran out of word rows aligning sentence "
                      f"{idx}; clamped to tail. Sentence: {sentence}")

        time_stamp_list.append((
            float(starts.iloc[start_row]),
            float(ends.iloc[end_row]),
        ))
        char_cursor = group_char0[cursor] if cursor < len(groups) else len(full_chars)

    if clamped > 3:
        print(f"\n⚠️ Warning: {clamped} sentences ran out of word rows and were "
              f"clamped to the tail (only the first 3 were logged).")

    # A backwards re-anchor means the previous sentence had eaten words that
    # belong to this one — trim its end so the two cues never overlap.
    trimmed = 0
    for i in range(1, len(time_stamp_list)):
        prev_start, prev_end = time_stamp_list[i - 1]
        cur_start, _cur_end = time_stamp_list[i]
        if prev_start < cur_start < prev_end:
            time_stamp_list[i - 1] = (prev_start, cur_start)
            trimmed += 1

    if reanchored or unanchored:
        extra = f"，{unanchored} 句未命中（回退计数投影）" if unanchored else ""
        extra += f"，收紧 {trimmed} 处重叠" if trimmed else ""
        print(f"🔗 字幕对齐：{reanchored} 句按内容重新锚定{extra}")

    return time_stamp_list


def align_timestamp(df_text, df_translate, subtitle_output_configs: list, output_dir: str, for_display: bool = True):
    """Align timestamps and add a new timestamp column to df_translate"""
    df_trans_time = df_translate.copy()

    # Assign an ID to each word in df_text['text'] and create a new DataFrame
    words = df_text['text'].str.split(expand=True).stack().reset_index(level=1, drop=True).reset_index()
    words.columns = ['id', 'word']
    words['id'] = words['id'].astype(int)

    # Process timestamps ⏰
    time_stamp_list = get_sentence_timestamps(df_text, df_translate)
    df_trans_time['timestamp'] = time_stamp_list
    df_trans_time['duration'] = df_trans_time['timestamp'].apply(lambda x: x[1] - x[0])

    # Remove gaps 🕳️
    for i in range(len(df_trans_time)-1):
        delta_time = df_trans_time.loc[i+1, 'timestamp'][0] - df_trans_time.loc[i, 'timestamp'][1]
        if 0 < delta_time < 1:
            df_trans_time.at[i, 'timestamp'] = (df_trans_time.loc[i, 'timestamp'][0], df_trans_time.loc[i+1, 'timestamp'][0])

    # Convert start and end timestamps to SRT format
    df_trans_time['timestamp'] = df_trans_time['timestamp'].apply(lambda x: convert_to_srt_format(x[0], x[1]))

    # Polish subtitles: replace punctuation in Translation if for_display
    if for_display:
        df_trans_time['Translation'] = df_trans_time['Translation'].apply(lambda x: re.sub(r'[，。]', ' ', x).strip())

    # Output subtitles 📜
    def generate_subtitle_string(df, columns):
        return ''.join([f"{i+1}\n{row['timestamp']}\n{row[columns[0]].strip()}\n{row[columns[1]].strip() if len(columns) > 1 else ''}\n\n" for i, row in df.iterrows()]).strip()

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        for filename, columns in subtitle_output_configs:
            subtitle_str = generate_subtitle_string(df_trans_time, columns)
            with open(os.path.join(output_dir, filename), 'w', encoding='utf-8') as f:
                f.write(subtitle_str)

    return df_trans_time
