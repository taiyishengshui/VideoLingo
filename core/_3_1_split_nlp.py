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
    split_by_comma_main(nlp)
    split_sentences_main(nlp)
    split_long_by_root_main(nlp)
    return

if __name__ == '__main__':
    split_by_spacy()