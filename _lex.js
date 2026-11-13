// Character-level Python lexer: checks quote/bracket balance, comments,
// string prefixes and line continuations. Enough to catch syntax breakage
// without a Python interpreter (sandbox has none).
const fs = require('fs');

function lexPython(src, filename) {
  const errors = [];
  let i = 0, line = 1;
  const stack = [];
  const bracketPairs = { '(': ')', '[': ']', '{': '}' };
  let inTriple = null; // full delimiter (''' or """) of an open triple string

  const err = (msg) => errors.push(`${filename}:${line}: ${msg}`);

  while (i < src.length) {
    const c = src[i];

    if (c === '\n') {
      line++;
      i++;
      continue;
    }
    if (c === '#' && !inTriple) {
      while (i < src.length && src[i] !== '\n') i++;
      continue;
    }
    if (inTriple) {
      if (c === '\\') { i += 2; continue; }
      if (src.startsWith(inTriple, i)) { i += 3; inTriple = null; continue; }
      if (c === '\n') line++;
      i++;
      continue;
    }

    // string start?
    const prefixMatch = /^([rRbBuUfF]{0,2})('''|"""|'|")/.exec(src.slice(i, i + 5));
    if (prefixMatch && (i === 0 || !/[\w]/.test(src[i - 1]))) {
      const q = prefixMatch[2];
      i += prefixMatch[0].length;
      if (q.length === 3) {
        inTriple = q;
        continue;
      }
      // single-line string
      while (i < src.length) {
        if (src[i] === '\\') { i += 2; continue; }
        if (src[i] === '\n') { err(`unterminated string (opened line ${line})`); break; }
        if (src.startsWith(q, i)) { i += 1; break; }
        i++;
      }
      continue;
    }

    if (c === '\\' && src[i + 1] === '\n') { i += 2; line++; continue; }

    if (bracketPairs[c]) { stack.push({ c, line }); i++; continue; }
    if (c === ')' || c === ']' || c === '}') {
      const open = stack.pop();
      if (!open) { err(`unmatched closing '${c}'`); }
      else if (bracketPairs[open.c] !== c) { err(`bracket mismatch: '${open.c}' (line ${open.line}) closed by '${c}'`); }
      i++;
      continue;
    }
    i++;
  }

  if (inTriple) err(`unterminated triple-quoted string`);
  for (const open of stack) err(`unclosed '${open.c}' opened at line ${open.line}`);
  return errors;
}

const files = process.argv.slice(2);
let bad = 0;
for (const f of files) {
  const src = fs.readFileSync(f, 'utf-8');
  const errors = lexPython(src, f);
  if (errors.length) {
    bad++;
    errors.forEach((e) => console.log(e));
  } else {
    console.log(`OK  ${f}`);
  }
}
process.exit(bad ? 1 : 0);
