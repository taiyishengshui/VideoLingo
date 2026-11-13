from core.spacy_utils import *
from core.utils.models import _3_1_SPLIT_BY_NLP
from core.utils import check_file_exists

@check_file_exists(_3_1_SPLIT_BY_NLP)
def split_by_spacy():
    # Repair systematic ASR word errors (idempotent, no-op when already done)
    # BEFORE sentence splitting, so _3_1/_3_2 and everything downstream reads
    # corrected text. Works on the JSON word store; xlsx re-exported in sync.
    from core.asr_correct import correct_transcription
    correct_transcription()

    nlp = init_nlp()
    split_by_mark(nlp)
    # 2026-09-08: comma/connector splitters removed from the chain.
    # They were designed to make subtitles short, but running them BEFORE
    # translation shredded sentences and hurt translation quality. Shortening
    # now happens AFTER translation in _5_split_sub. split_long_by_root reads
    # SPLIT_BY_MARK_FILE directly and remains as the >80-token safety valve.
    split_long_by_root_main(nlp)
    return

if __name__ == '__main__':
    split_by_spacy()