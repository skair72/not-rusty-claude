// The Bun.wrapAnsi differential corpus: every case the shim must answer
// exactly as Bun 1.3.14 does. Shared by both runtimes so neither side can
// drift, and deliberately escape-free in source - a literal control byte in a
// repo file is unreviewable.
//
// 43 inputs x 10 widths x 10 option combinations = 4,300 cases, covering SGR
// colour, OSC 8 hyperlinks, CJK, emoji, combining marks, tabs and embedded
// newlines. Bun is the oracle; nothing here hardcodes an expected answer.

const ESC = "\u001b";
const RED = ESC + "[31m", OFF = ESC + "[39m";
const BOLD = ESC + "[1m", NB = ESC + "[22m";
const LINK = ESC + "]8;;https://x.example" + ESC + "\\link" + ESC + "]8;;" + ESC + "\\";

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
