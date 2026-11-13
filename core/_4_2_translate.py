import pandas as pd
import concurrent.futures
from core.translate_lines import translate_lines
from core._4_1_summarize import load_full_summary
from core.utils.audio_trim import check_len_then_trim
from core.utils.subtitle_time import align_timestamp
from core.utils import *
from core.services.translate_service import (
    TranslationCkpt, build_shared_context, chunks_fingerprint,
    load_job_terms, split_chunks, terms_signature,
)
from rich.console import Console
from core.utils.progress import ChunkProgress
from core.utils.models import *
console = Console()


def neighbor_lines(chunks: list, i: int):
    """(previous last-3 lines, next first-2 lines) around chunk i."""
    prev = None if i == 0 else chunks[i - 1].split('\n')[-3:]
    after = None if i == len(chunks) - 1 else chunks[i + 1].split('\n')[:2]
    return prev, after


def translate_chunk(chunk, chunks, summary_text, terms, i):
    prev, after = neighbor_lines(chunks, i)
    shared_context = build_shared_context(summary_text, terms, prev, after)
    translation, english_result = translate_lines(chunk, shared_context, i)
    return i, english_result, translation


# 🚀 Main function to translate all chunks
@check_file_exists(_4_2_TRANSLATION)
def translate_all():
    console.print("[bold green]Start Translating All...[/bold green]")
    with open(_3_2_SPLIT_BY_MEANING, "r", encoding="utf-8") as file:
        # strip \r: the file is CRLF on Windows; split_chunks also guards,
        # but the length-sensitive code below works on these sentences too
        sentences = [l.rstrip("\r") for l in file.read().strip().split("\n")]
    chunks = split_chunks(sentences)

    # full context carried by EVERY chunk (2026-09-09 redesign).
    # Terms come from the PER-JOB glossary 4.1 wrote (checked base sets +
    # freshly extracted), never from terms/*.json again — that would drop
    # the newly extracted terms and double-inject the base ones.
    summary_text = load_full_summary()
    terms = load_job_terms()
    console.print(f"📚 Context: summary {len(summary_text)} chars, "
                  f"{len(terms['terms'])} job terms (base + 4.1 extracted)")

    # per-chunk checkpoint: resume from the first missing chunk
    ckpt = TranslationCkpt()
    # stale guard: re-split (or changed chunk_size/max_sentences) shifts
    # every index; a changed glossary makes old chunks silently keep
    # translations made with the OLD terms. Both invalidate the ckpt.
    had_before = len(ckpt.load()["chunks"])
    fp = chunks_fingerprint(sentences, len(chunks), terms_signature(terms))
    if not ckpt.ensure_fingerprint(fp):
        if had_before:
            console.print(f"[yellow]⚠️ 分块输入或术语表已变化（改了分块参数 / "
                          f"重跑切句 / 改了勾选或 4.1 重抽），"
                          f"丢弃 {had_before} 块旧断点，从头翻译[/yellow]")
    have = {int(k): v for k, v in ckpt.load()["chunks"].items()
            if k.isdigit() and int(k) < len(chunks)}
    todo = [i for i in range(len(chunks)) if i not in have]
    # headcount BEFORE the loop: the UI log needs the total up front, and
    # rich won't redraw its bar when stdout is captured (non-terminal)
    console.print(f"🧩 共 {len(chunks)} 块 | 已完成 {len(have)} 块（断点续跑） | "
                  f"本次待翻 {len(todo)} 块")
    ckpt_results = {i: have[i]["translation"] for i in have}

    # 🔄 Use concurrent execution for translation
    total = len(todo)
    with ChunkProgress("Translating chunks", total) as p:
        with concurrent.futures.ThreadPoolExecutor(max_workers=load_key("max_workers")) as executor:
            futures = []
            for i in todo:
                future = executor.submit(translate_chunk, chunks[i], chunks,
                                         summary_text, terms, i)
                futures.append(future)
            results = []
            try:
                for future in concurrent.futures.as_completed(futures):
                    check_cancel()
                    i, english_result, translation = future.result()
                    results.append((i, english_result, translation))
                    # Checkpoint EACH chunk the moment it lands. Writing them
                    # only after the whole loop finished meant an interrupt
                    # (stop button / crash / Ctrl+C) lost every finished
                    # chunk — the next run started over from chunk 0.
                    ckpt.put(i, {"lines": chunks[i].count('\n') + 1,
                                 "translation": translation})
                    p.advance()
            except BaseException:
                for f in futures:
                    f.cancel()
                raise
    # merge: checkpoint hits + fresh results (original order)
    all_translations = {}
    for i, _, translation in results:
        all_translations[i] = translation
    for i in ckpt_results:
        all_translations[i] = ckpt_results[i]

    # 💾 Save results to lists and Excel file
    src_text = []
    trans_text = []
    for i, chunk in enumerate(chunks):
        src_text.extend(chunk.split('\n'))
        trans_text.extend(all_translations[i].split('\n'))

    # Trim long translation text
    from core.utils.words_store import load_words
    df_text = load_words(xlsx_path=_2_CLEANED_CHUNKS)  # JSON primary, xlsx fallback; already unquoted
    df_translate = pd.DataFrame({'Source': src_text, 'Translation': trans_text})
    subtitle_output_configs = [('trans_subs_for_audio.srt', ['Translation'])]
    df_time = align_timestamp(df_text, df_translate, subtitle_output_configs, output_dir=None, for_display=False)
    # Trim over-long lines to fit their time budget — CONCURRENTLY.
    # df.apply(axis=1) walked every line serially and each over-long line is a
    # blocking LLM call, which made this the slowest tail of step 4-2 (the
    # translation loop itself was already done at this point).
    import importlib
    audio_trim = importlib.import_module("core.utils.audio_trim")
    # Warm the estimator BEFORE fanning out: check_len_then_trim lazily
    # initialises a module global, which would otherwise race across threads.
    if audio_trim.ESTIMATOR is None:
        from core.tts_backend.estimate_duration import init_estimator
        audio_trim.ESTIMATOR = init_estimator()

    min_trim = load_key("min_trim_duration")
    translations = df_time['Translation'].tolist()
    durations = df_time['duration'].tolist()
    todo_trim = []
    for i, d in enumerate(durations):
        try:
            if float(d) > min_trim:
                todo_trim.append(i)
        except (TypeError, ValueError):
            continue

    trimmed = 0
    if todo_trim:
        with ChunkProgress("Trimming long lines", len(todo_trim), unit="行") as p:
            with concurrent.futures.ThreadPoolExecutor(max_workers=load_key("max_workers")) as executor:
                futures = {executor.submit(check_len_then_trim, translations[i], durations[i]): i
                           for i in todo_trim}
                for future in concurrent.futures.as_completed(futures):
                    i = futures[future]
                    try:
                        new_text = future.result()
                        if new_text != translations[i]:
                            trimmed += 1
                        translations[i] = new_text
                    except Exception as e:
                        console.print(f"[yellow]⚠️ Trim failed for line {i}, "
                                      f"keeping original: {e}[/yellow]")
                    p.advance()
        console.print(f"✂️ 检查 {len(todo_trim)} 行，实际缩短 {trimmed} 行")
    df_time['Translation'] = translations

    df_time.to_excel(_4_2_TRANSLATION, index=False)
    ckpt.clear()  # done — the xlsx is the final artifact now
    console.print(f"[bold green]✅ Translation completed: {len(chunks)} chunks "
                  f"(new {len(results)}) → {_4_2_TRANSLATION}[/bold green]")

if __name__ == '__main__':
    translate_all()
