from core.prompts import generate_shared_prompt, get_prompt_faithfulness, get_prompt_expressiveness
from rich.panel import Panel
from rich.console import Console
from rich.table import Table
from rich import box
from core.utils import *
from core.services.translate_service import build_shared_context
console = Console()

MAX_ATTEMPTS = 3  # per prompt, per step — replaces the old space-suffix hack


# 模型不照键名输出是常态（实测 gemini-2.5-flash-lite 把 direct 写成
# translation）。差一个键名就让整块翻译崩掉太脆 —— 这里做保守的别名归一：
# 只认语义等价的常见写法，绝不猜内容。
_SUB_KEY_ALIASES = {
    "direct": ("translation", "translated", "translated_text", "target",
               "target_text", "zh", "chinese", "cn", "译文", "直译",
               "direct_translation", "literal", "output", "text", "result"),
    "free": ("free_translation", "polished", "fluent", "natural", "refined",
             "smooth", "意译", "润色", "final", "output", "text", "result"),
}


def _norm_key(k) -> str:
    return str(k).strip().lower().replace(" ", "").replace("-", "_")


def normalize_sub_keys(result, sub_key: str) -> bool:
    """把写成别名的键映射回 `sub_key`（原地改，ask_gpt 随后返回的就是修好的 dict）。

    返回是否做了改动 —— 调用方据此打一条提示，方便事后看出模型又不听话了。
    只在【缺 sub_key】时动手，已有的一律不动。
    """
    if not isinstance(result, dict):
        return False
    aliases = _SUB_KEY_ALIASES.get(sub_key, ())
    changed = False
    for item in result.values():
        if not isinstance(item, dict) or sub_key in item:
            continue
        norm = {_norm_key(k): v for k, v in item.items()}
        hit = next((a for a in aliases if _norm_key(a) in norm), None)
        if hit is not None:
            item[sub_key] = norm[_norm_key(hit)]
            changed = True
            continue
        # 兜底：除 origin 外只剩一个键，那必然就是译文
        others = {k: v for k, v in item.items() if _norm_key(k) != "origin"}
        if len(others) == 1:
            item[sub_key] = next(iter(others.values()))
            changed = True
    return changed


def valid_translate_result(result: dict, required_keys: list, required_sub_keys: list):
    # Check for the required key
    if not all(key in result for key in required_keys):
        return {"status": "error", "message": f"Missing required key(s): {', '.join(set(required_keys) - set(result.keys()))}"}

    # Check for required sub-keys in all items
    for key in result:
        if not all(sub_key in result[key] for sub_key in required_sub_keys):
            return {"status": "error", "message": f"Missing required sub-key(s) in item {key}: {', '.join(set(required_sub_keys) - set(result[key].keys()))}"}

    return {"status": "success", "message": "Translation completed"}


def translate_lines(lines, shared_context, index=0, reflect=None,
                    bypass_cache=False):
    """Translate one chunk.

    shared_context: the four RAW content pieces (summary/terms/prev/after)
    from translate_service.build_shared_context — assembled here through
    the shared TEMPLATE (generate_shared_prompt), so the user's shared
    prompt edits apply for real. Accepts a pre-rendered string too (legacy
    callers / probe path) for compatibility.
    reflect: override reflect_translate for this call (the playground L3
    probe passes False to halve the cost); None = follow config.
    bypass_cache: True for playground probes — every re-test must be a REAL
    call (no cached replay), so results reflect the live chain.
    """
    check_cancel()

    # assemble the shared block through the template (single source of
    # formatting — no doubled headers, UI edits apply)
    if isinstance(shared_context, dict):
        shared_context = generate_shared_prompt(
            shared_context.get("previous_content", ""),
            shared_context.get("subsequent_content", ""),
            shared_context.get("summary", ""),
            shared_context.get("terms", ""),
        )

    def retry_translation(prompt, length, step_name):
        def valid_faith(response_data):
            normalize_sub_keys(response_data, 'direct')
            return valid_translate_result(response_data, [str(i) for i in range(1, length+1)], ['direct'])
        def valid_express(response_data):
            normalize_sub_keys(response_data, 'free')
            return valid_translate_result(response_data, [str(i) for i in range(1, length+1)], ['free'])
        last_err = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            # explicit attempt marker (NOT trailing spaces): cache-busting
            # is deliberate and visible, and the log records which attempt
            marked = prompt + f"\n\n<!-- attempt {attempt}/{MAX_ATTEMPTS} -->"
            try:
                if step_name == 'faithfulness':
                    result = ask_gpt(marked, resp_type='json', valid_def=valid_faith, log_title='translate_faith', bypass_cache=bypass_cache)
                elif step_name == 'expressiveness':
                    result = ask_gpt(marked, resp_type='json', valid_def=valid_express, log_title='translate_express', bypass_cache=bypass_cache)
            except ValueError as e:
                # 校验失败（键名/结构不对）—— ask_gpt 内部用【同一个】prompt
                # 重试多少次都没用，模型会犯同样的错。这里换成新的 attempt
                # marker（=新 prompt，缓存也不会命中）再给它一次机会。
                if 'API response error' not in str(e):
                    raise
                last_err = e
                console.print(f'[yellow]⚠️ {step_name.capitalize()} translation of block {index} rejected ({e}), retry {attempt}/{MAX_ATTEMPTS}...[/yellow]')
                continue
            if len(lines.split('\n')) == len(result):
                return result
            last_err = ValueError(f'line count {len(result)} ≠ {length}')
            if attempt != MAX_ATTEMPTS:
                console.print(f'[yellow]⚠️ {step_name.capitalize()} translation of block {index} failed (line count {len(result)} ≠ {length}), retry {attempt}/{MAX_ATTEMPTS}...[/yellow]')
        raise ValueError(f'[red]❌ {step_name.capitalize()} translation of block {index} failed after {MAX_ATTEMPTS} attempts ({last_err}). Please check `output/gpt_log/error.json` for more details.[/red]')

    ## Step 1: Faithful to the Original Text
    prompt1 = get_prompt_faithfulness(lines, shared_context)
    faith_result = retry_translation(prompt1, len(lines.split('\n')), 'faithfulness')

    for i in faith_result:
        faith_result[i]["direct"] = faith_result[i]["direct"].replace('\n', ' ')

    do_reflect = load_key('reflect_translate') if reflect is None else reflect
    if not do_reflect:
        # If reflect_translate is False or not set, use faithful translation directly
        translate_result = "\n".join([faith_result[i]["direct"].strip() for i in faith_result])

        table = Table(title="Translation Results", show_header=False, box=box.ROUNDED)
        table.add_column("Translations", style="bold")
        for i, key in enumerate(faith_result):
            table.add_row(f"[cyan]Origin:  {faith_result[key]['origin']}[/cyan]")
            table.add_row(f"[magenta]Direct:  {faith_result[key]['direct']}[/magenta]")
            if i < len(faith_result) - 1:
                table.add_row("[yellow]" + "-" * 50 + "[/yellow]")

        console.print(table)
        return translate_result, lines

    ## Step 2: Express Smoothly
    prompt2 = get_prompt_expressiveness(faith_result, lines, shared_context)
    express_result = retry_translation(prompt2, len(lines.split('\n')), 'expressiveness')

    table = Table(title="Translation Results", show_header=False, box=box.ROUNDED)
    table.add_column("Translations", style="bold")
    for i, key in enumerate(express_result):
        table.add_row(f"[cyan]Origin:  {faith_result[key]['origin']}[/cyan]")
        table.add_row(f"[magenta]Direct:  {faith_result[key]['direct']}[/magenta]")
        table.add_row(f"[green]Free:    {express_result[key]['free']}[/green]")
        if i < len(faith_result) - 1:
            table.add_row("[yellow]" + "-" * 50 + "[/yellow]")

    console.print(table)

    translate_result = "\n".join([express_result[i]["free"].replace('\n', ' ').strip() for i in express_result])

    if len(lines.split('\n')) != len(translate_result.split('\n')):
        console.print(Panel(f'[red]❌ Translation of block {index} failed, Length Mismatch, Please check `output/gpt_log/translate_expressiveness.json`[/red]'))
        raise ValueError(f'Origin ···{lines}···,\nbut got ···{translate_result}···')

    return translate_result, lines


if __name__ == '__main__':
    # test e.g.
    lines = '''All of you know Andrew Ng as a famous computer science professor at Stanford.
He was really early on in the development of neural networks with GPUs.
Of course, a creator of Coursera and popular courses like deeplearning.ai.
Also the founder and creator and early lead of Google Brain.'''
    ctx = build_shared_context("Test summary", {"terms": []}, None, None)
    translate_lines(lines, ctx)
