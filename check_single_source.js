#!/usr/bin/env node
/* Single-source-of-truth guard (project convention, 2026-09-09).
 *
 * Decision literals must appear in exactly ONE module. If someone
 * re-implements a decision in a new entry point (the root cause of the
 * 2026-09-08/09 "test button didn't rotate keys" and "single-task rerun
 * didn't clean markers" bugs), this check fails the build.
 *
 * Usage: node check_single_source.js   (exit 0 = clean, 1 = violation)
 */
const fs = require('fs');
const path = require('path');

const RULES = [
  {
    id: 'dead-key-status-codes',
    // the 401/402/403 "key is dead" decision: a list/tuple of these codes
    // in code (not comments, not HTTPException permission codes, not prose)
    home: 'core/services/llm_service.py',
    pattern: /\((?:\s*40[123]\s*,)+\s*40[123]\s*\)|in\s+\(\s*40[123]\s*(?:,\s*40[123]\s*)*\)/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/services/llm_service.py',   // the home
    ],
    msg: 'dead-key decision (401/402/403 status codes) must live only in core/services/llm_service.py — import classify_error/on_failure instead',
  },
  {
    id: 'rate-limit-status-code',
    // the 429 "we were too fast" decision: only llm_service may test for it;
    // consumers import classify_error / rate_limit_wait instead
    home: 'core/services/llm_service.py',
    pattern: /==\s*429\b|=\s*429\b/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/services/llm_service.py',   // the home
    ],
    msg: 'rate-limit decision (429) must live only in core/services/llm_service.py — import classify_error/rate_limit_wait instead',
  },
  {
    id: 'mark-key-dead-calls',
    // who may write dead-key marks
    home: 'core/services/llm_service.py',
    pattern: /mark_key_dead\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/utils/llm_profiles.py',     // the storage primitive itself
      'core/services/llm_service.py',   // the only caller
    ],
    msg: 'mark_key_dead must be called only from core/services/llm_service.py (via on_failure)',
  },
  {
    id: 'delete-step-artifacts-calls',
    // who may wipe step markers (force-rerun cleanup)
    home: 'core/services/run_service.py',
    pattern: /delete_step_artifacts\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/utils/job_context.py',      // the primitive + internal delegation
      'core/services/run_service.py',   // the only entry caller
    ],
    msg: 'delete_step_artifacts must be called only from core/services/run_service.force_clean',
  },
  {
    id: 'job-terms-loader',
    // the per-job glossary (checked base sets + 4.1's fresh terms) has ONE
    // reader: 4.2 / playground must not read terms/*.json a second time
    home: 'core/services/translate_service.py',
    pattern: /def\s+load_job_terms\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/services/translate_service.py',   // the home
    ],
    msg: 'load_job_terms (per-job glossary) must live only in core/services/translate_service.py — import it instead of re-reading terms/*.json in step 4.2',
  },
  {
    id: 'step42-terms-source',
    // step 4.2 (and everything that renders a TRANSLATION prompt) must use
    // the per-job glossary. Only 4.1's summary view may see the raw plate.
    home: 'core/services/translate_service.py',
    pattern: /load_active_terms\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/services/translate_service.py',   // definition + internal fallback
      'core/_4_1_summarize.py',               // 4.1 builds the plate
      'server/translate_api.py',              // 4.1 summary preview + write-back plate markers
    ],
    msg: 'step 4.2 / translation prompts must use load_job_terms() (base + 4.1 fresh terms); load_active_terms() is for step 4.1 / review only',
  },
  {
    id: 'term-set-write-decisions',
    // every decision about WRITING a base term set (name rule, whole-set
    // save, checked write-back) belongs to translate_service; the API only
    // adapts HTTP -> service and must never re-implement json.dump/re.match
    home: 'core/services/translate_service.py',
    pattern: /def\s+(?:validate_term_set_name|create_or_replace_term_set|write_back_terms)\s*\(|save_term_set\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/services/translate_service.py',   // definitions + the only callers
    ],
    msg: 'base term-set writes (name validation / create / write_back / save) must live only in core/services/translate_service.py',
  },
  {
    id: 'term-catalog-view',
    // #/terms "all terms" view: the deduped union of every base set + the
    // per-job glossary, which value 4.2 actually injects, AND each source
    // file's complete list are ONE decision (term_catalog). No API/UI may
    // hand-roll a second merge, a second "effective" rule, or re-read the
    // term files to rebuild a per-source list.
    home: 'core/services/translate_service.py',
    pattern: /def\s+term_catalog\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: ['core/services/translate_service.py'],
    msg: 'the all-terms catalog (union + effective value + per-source lists) must live only in core/services/translate_service.py — call term_catalog() instead',
  },
  {
    id: 'term-source-file-labels',
    // the source-file labels shown on #/terms (terms/<name>.json and
    // output/log/terminology.json) are derived once by term_catalog();
    // no other module may build its own list of "where terms come from".
    home: 'core/services/translate_service.py',
    pattern: /terms\/\{name\}\.json|["']path["']\s*:\s*["']output\/log\/terminology\.json["']/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: ['core/services/translate_service.py'],
    msg: 'per-source term file labels must come from term_catalog() in translate_service only',
  },
  {
    id: 'history-term-import-decisions',
    // archived glossaries -> base term sets: path whitelist, dedup preview
    // and the shared read-modify-write are ONE decision. No API/UI may
    // re-read history/*.json or hand-roll a second merge/write rule.
    home: 'core/services/translate_service.py',
    pattern: /def\s+(?:history_term_sources|history_import_preview|import_history_terms|_merge_into_base_set|_resolve_hist_file)\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: ['core/services/translate_service.py'],
    msg: 'history glossary enumeration / dedup preview / base-set import must live only in core/services/translate_service.py',
  },
  {
    id: 'history-glossary-path',
    // the ONLY path to an archived glossary; a client-supplied name must
    // never be joined into a filesystem path anywhere else.
    home: 'core/services/translate_service.py',
    pattern: /os\.path\.join\(\s*HISTORY_DIR|["']log["']\s*,\s*["']terminology\.json["']/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: ['core/services/translate_service.py'],
    msg: 'history/<name>/log/terminology.json must be resolved only by _resolve_hist_file() in translate_service',
  },
  {
    id: 'ad-default-source',
    home: 'core/services/ad_cut_service.py',
    pattern: /def\s+default_source\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: ['core/services/ad_cut_service.py'],
    msg: 'default preview/cut source must be decided only in core/services/ad_cut_service.py',
  },
  {
    id: 'cq-encode-decisions',
    // constant-quality knobs (CQ level, AQ, lookahead, multipass) are the
    // one encoding decision; no step/API may hand-roll a second set
    home: 'core/services/encode_service.py',
    pattern: /-spatial_aq|-temporal_aq|-rc-lookahead|-multipass|["']-cq["']/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: ['core/services/encode_service.py'],
    msg: 'constant-quality encode args must come only from core/services/encode_service.py — call build_encode_args() instead',
  },
  {
    id: 'preview-stream-release',
    // Windows: a single leaked Range read handle blocks archive cleanup.
    // Every entry point must call this registry instead of opening its own.
    home: 'server/preview_stream.py',
    pattern: /def\s+(?:open_stream|close_preview_streams|active_stream_count)\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: ['server/preview_stream.py'],
    msg: 'preview read handles must be registered/released only by server/preview_stream.py — call close_preview_streams() instead',
  },
  {
    id: 'dub-task-table-cell-codec',
    // tts_tasks.xlsx stores str(list); numpy>=2 leaks np.float64(...) into
    // new_sub_times and a bare eval() in step 11 explodes. ONE codec for both
    // the write and the read side (v1.27).
    home: 'core/services/dub_timeline_service.py',
    pattern: /def\s+(?:parse_excel_list|plain_time_pairs)\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/services/dub_timeline_service.py',   // the home
    ],
    msg: 'tts_tasks.xlsx list-cell codec must live only in core/services/dub_timeline_service.py — import parse_excel_list / plain_time_pairs instead',
  },
  {
    id: 'qwen3-asr-runtime-dispatch',
    // FR-35: ASR runtime is dispatched in exactly one place. A second
    // `runtime == "qwen3_asr"` branch would silently split decode policy,
    // hints and checkpoint fingerprints.
    home: 'core/_2_asr.py',
    pattern: /runtime\s*==\s*["']qwen3_asr["']/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/_2_asr.py',   // the home
    ],
    msg: 'Qwen3-ASR runtime dispatch must live only in core/_2_asr.py — do not add a second ASR runtime decision',
  },
  {
    id: 'qwen-word-normalize',
    // FR-39/41: zero-width / hallucination / URL-split / uniform-stretch
    // cleanup of ForcedAligner word stamps belongs to the isolated runner.
    // audio_preprocess.py's "missing start/end -> zero-width placeholder" is a
    // DIFFERENT concern (backends without word stamps) and must not grow a
    // second interpolation or a second stretch detector.
    home: 'core/asr_backend/qwen3_asr_runner.py',
    pattern: /def\s+(?:normalize_words|_split_url_word|detect_uniform_stretch|_shrink_uniform_runs)\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/asr_backend/qwen3_asr_runner.py',   // the home
    ],
    msg: 'Qwen3-ASR word-stamp normalization must live only in qwen3_asr_runner.py — do not add a second interpolation / hallucination policy',
  },
  {
    id: 'bili-meta-md-format',
    // FR-27 v1.37: bili_meta.md is the single source of truth for the last
    // generated copy. _render_md (write) and load_saved_bili_meta (read) must
    // remain the ONLY pair that knows its layout; no second parser in an API
    // entry point (that is exactly how the refresh-empty-state bug happened).
    home: 'core/services/bili_meta_service.py',
    pattern: /["']## (?:标题候选|标签)["']|# B站投稿文案（/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/services/bili_meta_service.py',   // the home
    ],
    msg: 'bili_meta.md layout must live only in core/services/bili_meta_service.py (_render_md + load_saved_bili_meta) — never parse it in server/ or frontend/',
  },
  {
    id: 'subtitle-source-decision',
    // FR-36 v1.38: step 7 and step 12 used to hard-code their own subtitle
    // lists. ONE service decides which srt each version burns; the English
    // dubbing line is derived there, never in a numbered step.
    home: 'core/services/subtitle_source_service.py',
    pattern: /def\s+(?:subtitle_plan|build_dub_src_srt|parse_srt_blocks|format_srt_time)\s*\(|["']dub_src\.srt["']/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [
      'core/services/subtitle_source_service.py',   // the home
    ],
    msg: 'subtitle source decision + dub_src.srt generation must live only in core/services/subtitle_source_service.py (FR-36)',
  },
  {
    id: 'build-id-git-probe',
    // FR-37 v1.39: only version.py may probe git for the build marker.
    home: 'version.py',
    pattern: /git\s+rev-parse|def\s+build_info\s*\(/g,
    scan: ['core/**/*.py', 'server/**/*.py'],
    allow: [],
    msg: 'git build-id probe must live only in version.py — call build_info() instead of running git in a route (FR-37)',
  },
];

function walk(dir, out) {
  if (!fs.existsSync(dir)) return out;
  for (const name of fs.readdirSync(dir)) {
    if (name === '__pycache__' || name === 'node_modules' || name === '.git') continue;
    const p = path.join(dir, name);
    const st = fs.statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (p.endsWith('.py')) out.push(p);
  }
  return out;
}

const root = __dirname;
const files = ['core', 'server'].flatMap(d => walk(path.join(root, d), []))
  .map(f => path.relative(root, f).replace(/\\/g, '/'));

let violations = 0;
for (const rule of RULES) {
  for (const f of files) {
    if (rule.allow.includes(f)) continue;
    const src = fs.readFileSync(path.join(root, f), 'utf-8');
    const matches = src.match(rule.pattern);
    if (matches) {
      // count only "decision-looking" occurrences: skip pure comments/strings
      const real = src.split('\n').some(line =>
        rule.pattern.test(line) && !/^\s*#/.test(line) &&
        !/^(\s*["']|#)/.test(line));
      if (real) {
        console.error(`✗ [${rule.id}] ${f}\n    ${rule.msg}`);
        violations++;
      }
    }
  }
}

if (violations) { console.error(`\n${violations} violation(s)`); process.exit(1); }
console.log('✓ single-source guard clean');
