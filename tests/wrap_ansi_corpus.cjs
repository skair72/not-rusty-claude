// The Bun.wrapAnsi differential corpus: every case the shim must answer
// exactly as Bun 1.3.14 does. Shared by both runtimes so neither side can
// drift, and deliberately escape-free in source - a literal control byte in a
// repo file is unreviewable.
//
// 28 inputs x 10 widths x 10 option combinations = 2,800 cases, covering SGR
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
