"""Cross-entry decision: may two adjacent short subtitle lines be merged?

Step 8.1 merges very short subtitles so the TTS has enough audio to work
with. Two people trading quick lines look "short" to that rule, and get
glued into one dubbing task with a blended speaker embedding. This is the
single home for that merge decision (PRD FR-34).

Deliberately dependency-free and text-only: it must be callable from step
8.1 without dragging the TTS duration estimator (and its optional NLP deps)
into the import chain.
"""

# Only strong signals. "so" / "but" are common in a single speaker's
# continued turn and would over-split.
_REPLY_START_EN = ('yes', 'no', 'yeah', 'yep', 'right', 'well', 'oh',
                   'of course', 'absolutely', 'exactly', 'sure', 'hmm',
                   'um', 'actually')
_REPLY_START_ZH = ('是的', '对', '嗯', '哦', '不', '没错', '当然',
                   '好', '是', '可是', '但是', '所以')


def looks_like_cross_turn(prev_text, next_text, prev_origin, next_origin) -> bool:
    """True when the two lines look like an obvious speaker change."""
    for text in (str(prev_origin or ''), str(prev_text or '')):
        if text.rstrip().endswith(('?', '？')):
            return True
    for text in (str(next_origin or ''), str(next_text or '')):
        low = text.lstrip().lower()
        if low.startswith(_REPLY_START_EN) or text.lstrip().startswith(_REPLY_START_ZH):
            return True
    return False
