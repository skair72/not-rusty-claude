// The Bun.wrapAnsi differential corpus: every case the shim must answer
// exactly as Bun 1.3.14 does. Shared by both runtimes so neither side can
// drift, and deliberately escape-free in source - a literal control byte in a
// repo file is unreviewable.
//
// 51 inputs x 10 widths x 10 option combinations = 5,100 cases, covering SGR
// colour, OSC 8 hyperlinks, CJK, emoji, combining marks, tabs and embedded
// newlines. Bun is the oracle; nothing here hardcodes an expected answer.

const ESC = "\u001b";
const RED = ESC + "[31m", OFF = ESC + "[39m";
const BOLD = ESC + "[1m", NB = ESC + "[22m";
const LINK = ESC + "]8;;https://x.example" + ESC + "\\link" + ESC + "]8;;" + ESC + "\\";
// String.fromCharCode, never typed literally: NBSP (U+00A0) is visually
// identical to a plain space in a terminal, and a heredoc silently
// flattened one to the other during this same investigation - a mistake
// that would be unreviewable as a literal byte in this file too.
const NBSP = String.fromCharCode(0xa0);
const MARK = String.fromCharCode(0x301);

const strings = [
  "", " ", "  ", "a", "abc",
  "the quick brown fox jumps over the lazy dog",
  "aaaa bbbb cccc", "abcdefghij", "aa   bb", "a  b  c",
  "trailing   ", "   leading", "one\ntwo three", "a\n\nb",
  "hyphen-ated words here", "tab\there now",
  RED + "redred redred" + OFF,
  BOLD + "bold text that is long" + NB + " plain tail",
  RED + "colour spans " + OFF + "the break point",
  LINK + " after a link",
  "日本語テキストです",
  "日本 語c キスト",
  "ab日本cd ef",
  "emoji 👍 here", "👍👍👍👍",
  "café combining",
  RED + BOLD + "nested codes here" + NB + OFF,
  "word " + RED + "mid" + OFF + "dle break",

  // Added 2026-08-26 after review. The corpus had no multi-parameter SGR at
  // all - no 256-colour, no truecolor, no combined codes, no background, no
  // reset - and behind that gap sat a real defect: the carry model kept the
  // LAST parameter and re-synthesised it, turning 38;5;208 into a carry of
  // \u001b[208m, an SGR code that does not exist. A themed TUI emits these
  // constantly, so "byte-equal over 2,800 cases" was true and still missed the
  // shapes that mattered most.
  ESC + "[38;5;208m256 colour words wrapping here" + ESC + "[39m",
  ESC + "[38;2;215;119;87mtruecolor words here" + ESC + "[39m",
  ESC + "[48;5;20mbackground colour words" + ESC + "[49m",
  ESC + "[1;31mcombined bold red words" + ESC + "[0m",
  ESC + "[31mred words" + ESC + "[0m then plain text",
  ESC + "[41maaa bbb ccc ddd eee",
  ESC + "[31munclosed colour to the end",
  "trailing escape then nothing " + ESC + "[31m",
  ESC + "[4munderlined words here" + ESC + "[24m",
  BOLD + "bold " + RED + "and red" + OFF + " tail" + NB,
  // A BEL-terminated hyperlink: the other OSC 8 form, and the common one.
  ESC + "]8;;https://x.example\u0007bel link" + ESC + "]8;;\u0007 after",
  "a lone \r carriage return here",

  // Added 2026-08-26, minimised from real 100-seed fuzz failures by deleting
  // everything that was not load-bearing. Each one is an ST-terminated OSC 8
  // with an EMPTY uri - a CLOSE, not an opener. The glue rule keyed on "ST
  // terminated" alone and held these on one row; the oracle breaks them
  // normally, because a close opens no link and so glues nothing.
  //
  // The generated corpus never produced this shape on its own, so nothing in
  // the fuzz ratchet covers it: over 100 seeds the fix moved zero cases. It
  // belongs here or it is unprotected.
  ESC + "]8;;" + ESC + "\\" + ESC + "]8;;\u0007wo",
  ESC + "]8;;" + ESC + "\\" + ESC + "]8;;\u0007" + "\u672c",
  "." + ESC + "]8;;" + ESC + "\\" + ESC + "]8;;https://x.example/23\u0007f",

  // Also 2026-08-26. An ST-terminated OSC 8 opener IMMEDIATELY followed by
  // another OSC 8: the second supersedes the first before it reaches any
  // text, so the first covers nothing and glues nothing. The oracle breaks
  // these per character; the glue rule held them on one row.
  //
  // Like the three above, the generated grammar never emits this adjacency -
  // it appeared only after minimisation DELETED the text between the two
  // sequences, which is a shape no real input had. Measured: zero of 2,735
  // pooled failures change either way. So the ratchet cannot see it, and this
  // corpus is the only thing that holds it.
  ESC + "]8;;https://x.example/63" + ESC + "\\" +
    ESC + "]8;;https://x.example/79\u0007" + "\u4e2d\u6587\u5b57",
  "S" + ESC + "]8;;https://x.example/23" + ESC + "\\" + ESC + "]8;;\u0007" + "\ud83d\udc4d",

  // Trailing-trim rewrite, 2026-08-26. A single unified backward walk
  // replaced three narrower rules that could not reach a shape like this:
  // two escape groups with a SPACE between them, both trailing.
  "ab" + " " + BOLD + " " + RED + "	",

  // NBSP is NOT removable in trailing position, unlike a plain space -
  // measured directly with fromCharCode after a probe script's literal
  // NBSP was silently flattened to a plain space and inverted the
  // conclusion. A tab after it survives too: NBSP gives the row nonzero
  // visible width, so the tab is not "trailing on an otherwise blank row".
  "ab" + BOLD + NBSP + "	",
  NBSP + " " + RED + "	",

  // A row holding nothing but a combining mark and a CSI, the shape a hard
  // break produces when it splits a mark off its base onto its own row.
  // The mark has to vanish THROUGH the escape, not just when the row is
  // otherwise escape-free - the gap an earlier version of this rule had.
  "e" + MARK + ESC + "[H" + "\u4e2d",

  // Leading-edge tab shelter, 2026-08-27. A leading escape does not protect
  // the whitespace behind it from the per-row leading trim - except that a
  // non-SGR CSI shelters a TAB, and only a tab. That exception turns out to
  // cover an ST-terminated OSC 8 as well, opener or closer, which the
  // original escape-by-escape sweep recorded the other way round ("with
  // either OSC 8 form, ' \t ab' trims to 'ab'"). The two below pin the ST
  // form, empty uri and non-empty, which shelters unconditionally - making
  // the shelter test literally wrap_rowGateQualifies's, the same two escape
  // kinds that let a row gate its whitespace collapse.
  //
  // The BEL form is deliberately NOT here. It shelters too, but only when its
  // uri contains the letter "m" - see docs/findings.md section 13. That is an
  // upstream parsing quirk rather than a rule, and pinning it would pin a bug.
  //
  // Like the OSC 8 adjacency cases above, the generated grammar never emits
  // this shape - it puts no whitespace run directly behind a leading OSC 8 -
  // so all 100 fuzz seeds move by exactly zero either way, and this corpus is
  // the only thing holding it.
  ESC + "]8;;https://x.example" + ESC + "\\" + " \t ab",
  ESC + "]8;;" + ESC + "\\" + " \t ab",
];

const widths = [0, 1, 2, 3, 4, 5, 6, 10, 20, 80];

const optionSets = [
  undefined, {}, { hard: true }, { hard: false },
  { trim: false }, { trim: true }, { wordWrap: false }, { wordWrap: true },
  { hard: true, trim: false }, { hard: true, wordWrap: false },
];

const cases = [];
for (let s = 0; s < strings.length; s++)
  for (let w = 0; w < widths.length; w++)
    for (let o = 0; o < optionSets.length; o++)
      cases.push({ s, w, o });

module.exports = { strings, widths, optionSets, cases };
