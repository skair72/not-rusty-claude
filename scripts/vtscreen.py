"""vtscreen.py - a small terminal emulator, just enough to snapshot Ink's TUI.

scripts/harness.py drives the native binary and the artifact through a pty
and compares what each leaves on the SCREEN, not the raw byte streams: the two
renderers are free to reach the same screen by different cursor movements (and
timing alone reorders frames), so bytes would differ where screens do not.

Standard library only, like every tool in this repo. It models what Claude
Code's Ink renderer was measured to emit - printable text, CR/LF/BS/TAB, CSI
cursor movement and erasure, scroll regions, insert/delete, the alternate
screen, save/restore cursor - plus, per cell, the SGR style and the OSC 8
hyperlink it was written with (styled_text()), so a comparison sees colour,
emphasis and links and not only characters. Titles and every other
OSC/DCS/APC string are ignored. It answers the queries a TUI blocks on
(cursor position, device attributes) so that neither side stalls on a
timeout the other does not hit.

Widths come from unicodedata: East Asian Wide/Fullwidth are two cells,
combining marks and format characters zero. A grapheme cluster occupies ONE
cell: whatever joins a cluster (ZWJ and what follows it, variation selectors,
skin tones, tag characters, the second regional indicator of a flag) is
attached to the cell before it. Measuring each code point on its own made a
ZWJ family eight cells wide, and Ink's next absolute cursor move then wrote
over part of it - hiding cells from the comparison, which is exactly what an
emulator used as an oracle must not do. Emoji widths remain approximations;
both sides go through the same ones.
"""

import codecs
import unicodedata


def _joins_cluster(ch):
    o = ord(ch)
    return (o == 0x200D or 0xFE00 <= o <= 0xFE0F or 0x1F3FB <= o <= 0x1F3FF
            or 0xE0020 <= o <= 0xE007F or 0xE0100 <= o <= 0xE01EF)


def _is_ri(ch):
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def char_width(ch):
    o = ord(ch)
    if o < 0x20 or 0x7F <= o < 0xA0:
        return 0
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    if 0x1F300 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF and unicodedata.east_asian_width(ch) == "W":
        return 2
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class Screen:
    def __init__(self, rows=30, cols=100):
        self.rows, self.cols = rows, cols
        self.main = self._blank_grid()
        self.alt = self._blank_grid()
        self.grid = self.main
        self.alt_active = False
        self.cursor_visible = True
        self.r = self.c = 0
        self.saved = (0, 0)
        self.top, self.bottom = 0, rows - 1
        self.wrap_pending = False
        self.autowrap = True
        self.responses = []          # bytes the terminal owes the application
        self._dec = codecs.getincrementaldecoder("utf-8")("replace")
        self._state = "ground"
        self._buf = ""
        self.bells = 0
        self.modes = set()           # private modes currently set, e.g. "?2004"
        self.sgr = {}                # active SGR attributes, see _sgr()
        self.link = ""               # active OSC 8 URI
        self.pen = ""                # canonical (sgr, link) key of the next cell
        self.style = self._blank_styles()
        self.main_style, self.alt_style = self.style, self._blank_styles()
        self._join_next = False      # the previous code point was a ZWJ
        self._last = None            # (row, col) of the last cell written

    # -- grid helpers -------------------------------------------------------
    def _blank_row(self):
        return [" "] * self.cols

    def _blank_grid(self):
        return [self._blank_row() for _ in range(self.rows)]

    def _blank_styles(self):
        return [[""] * self.cols for _ in range(self.rows)]

    def _blank_style_row(self):
        return [""] * self.cols

    def _scroll_up(self, n=1):
        for _ in range(n):
            del self.grid[self.top]
            self.grid.insert(self.bottom, self._blank_row())
            del self.style[self.top]
            self.style.insert(self.bottom, self._blank_style_row())

    def _scroll_down(self, n=1):
        for _ in range(n):
            del self.grid[self.bottom]
            self.grid.insert(self.top, self._blank_row())
            del self.style[self.bottom]
            self.style.insert(self.top, self._blank_style_row())

    def _linefeed(self):
        if self.r == self.bottom:
            self._scroll_up()
        elif self.r < self.rows - 1:
            self.r += 1

    def _clamp(self):
        self.r = max(0, min(self.rows - 1, self.r))
        self.c = max(0, min(self.cols - 1, self.c))

    # -- input --------------------------------------------------------------
    def feed(self, data):
        text = self._dec.decode(data)
        for ch in text:
            self._step(ch)

    def _step(self, ch):
        st = self._state
        if st == "ground":
            if ch == "\x1b":
                self._state, self._buf = "esc", ""
            elif ch == "\x9b":
                self._state, self._buf = "csi", ""
            elif ch == "\x9d":
                self._state, self._buf = "osc", ""
            elif ord(ch) < 0x20 or ch == "\x7f":
                self._control(ch)
            else:
                self._print(ch)
        elif st == "esc":
            if ch == "[":
                self._state, self._buf = "csi", ""
            elif ch == "]":
                self._state, self._buf = "osc", ""
            elif ch in "P_^X":
                self._state, self._buf = "string", ""
            elif ch in "()*+":
                self._state = "charset"
            else:
                self._state = "ground"
                self._esc(ch)
        elif st == "charset":
            self._state = "ground"
        elif st == "csi":
            if "\x40" <= ch <= "\x7e":
                self._state = "ground"
                self._csi(self._buf, ch)
            elif ch == "\x1b":
                self._state, self._buf = "esc", ""
            else:
                self._buf += ch
        elif st in ("osc", "string"):
            if ch == "\x07" and st == "osc":
                self._state = "ground"
                self._osc(self._buf)
            elif ch == "\x1b":
                self._state = st + "_esc"
            else:
                self._buf += ch
        elif st in ("osc_esc", "string_esc"):
            base = st[:-4]
            if ch == "\\":
                self._state = "ground"
                if base == "osc":
                    self._osc(self._buf)
            else:
                self._buf += "\x1b" + ch
                self._state = base

    def _control(self, ch):
        if ch == "\r":
            self.c, self.wrap_pending = 0, False
        elif ch in "\n\x0b\x0c":
            self._linefeed()
            self.wrap_pending = False
        elif ch == "\b":
            self.c, self.wrap_pending = max(0, self.c - 1), False
        elif ch == "\t":
            self.c = min(self.cols - 1, (self.c // 8 + 1) * 8)
        elif ch == "\x07":
            self.bells += 1

    def _attach(self, ch):
        if self._last is not None:
            r, c = self._last
            self.grid[r][c] += ch
            return True
        return False

    def _print(self, ch):
        # grapheme continuation: joins the cell written last, takes no cell
        if self._join_next or _joins_cluster(ch):
            self._join_next = ord(ch) == 0x200D
            if self._attach(ch):
                return
        if _is_ri(ch) and self._last is not None:
            r, c = self._last
            cell = self.grid[r][c]
            if len(cell) == 1 and _is_ri(cell):
                self.grid[r][c] += ch      # the second half of a flag
                return
        w = char_width(ch)
        if w == 0:
            # attach to the previous cell so a combining mark is not lost
            if not self._attach(ch):
                pc = self.c - 1 if self.c > 0 and not self.wrap_pending else self.c
                if 0 <= pc < self.cols:
                    self.grid[self.r][pc] += ch
            return
        if self.wrap_pending and self.autowrap:
            self.c = 0
            self._linefeed()
            self.wrap_pending = False
        if w == 2 and self.c == self.cols - 1:
            if self.autowrap:
                self.grid[self.r][self.c] = " "
                self.c = 0
                self._linefeed()
        row = self.grid[self.r]
        row[self.c] = ch
        self.style[self.r][self.c] = self.pen
        self._last = (self.r, self.c)
        if w == 2 and self.c + 1 < self.cols:
            row[self.c + 1] = ""
            self.style[self.r][self.c + 1] = self.pen
        if self.c + w >= self.cols:
            self.c = self.cols - 1
            self.wrap_pending = True
        else:
            self.c += w

    def _esc(self, ch):
        if ch == "7":
            self.saved = (self.r, self.c)
        elif ch == "8":
            self.r, self.c = self.saved
        elif ch == "M":
            if self.r == self.top:
                self._scroll_down()
            else:
                self.r = max(0, self.r - 1)
        elif ch == "D":
            self._linefeed()
        elif ch == "E":
            self.c = 0
            self._linefeed()
        elif ch == "c":
            self.__init__(self.rows, self.cols)

    def _osc(self, s):
        if s.startswith("8;"):
            # OSC 8 ; params ; URI - an empty URI closes the link
            parts = s.split(";", 2)
            self.link = parts[2] if len(parts) > 2 else ""
            self._repen()
            return
        # colour queries: answer with a dark background so both sides agree
        if s.startswith("11;?"):
            self.responses.append(b"\x1b]11;rgb:0000/0000/0000\x1b\\")
        elif s.startswith("10;?"):
            self.responses.append(b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\")

    def _csi(self, buf, final):
        priv = ""
        if buf and buf[0] in "?<=>":
            priv, buf = buf[0], buf[1:]
        inter = ""
        while buf and buf[-1] in " !\"#$%&'()*+,-./":
            inter, buf = buf[-1] + inter, buf[:-1]
        parts = [p.split(":")[0] for p in buf.split(";")] if buf else []
        nums = [int(p) if p.isdigit() else 0 for p in parts]

        def n(i=0, d=1):
            return nums[i] if len(nums) > i and nums[i] else d

        if final not in "mnhlqpucst":
            self.wrap_pending = False
        # `priv in "<="` would be True for "" - every plain CSI dropped.
        if inter or priv in ("<", "="):
            return
        if final == "m" and not priv:
            self._sgr(buf)
            return
        if final == "A":
            self.r = max(self.top if self.r >= self.top else 0, self.r - n())
        elif final == "B":
            self.r = min(self.bottom if self.r <= self.bottom else self.rows - 1, self.r + n())
        elif final == "C":
            self.c = min(self.cols - 1, self.c + n())
        elif final == "D":
            self.c = max(0, self.c - n())
        elif final == "E":
            self.r, self.c = min(self.rows - 1, self.r + n()), 0
        elif final == "F":
            self.r, self.c = max(0, self.r - n()), 0
        elif final == "G" or final == "`":
            self.c = n() - 1
        elif final == "d":
            self.r = n() - 1
        elif final in "Hf":
            self.r, self.c = n(0) - 1, n(1) - 1
        elif final == "J":
            mode = nums[0] if nums else 0
            g, st = self.grid, self.style
            if mode == 0:
                g[self.r][self.c:] = [" "] * (self.cols - self.c)
                st[self.r][self.c:] = [""] * (self.cols - self.c)
                for i in range(self.r + 1, self.rows):
                    g[i], st[i] = self._blank_row(), self._blank_style_row()
            elif mode == 1:
                g[self.r][:self.c + 1] = [" "] * (self.c + 1)
                st[self.r][:self.c + 1] = [""] * (self.c + 1)
                for i in range(0, self.r):
                    g[i], st[i] = self._blank_row(), self._blank_style_row()
            elif mode in (2, 3):
                for i in range(self.rows):
                    g[i], st[i] = self._blank_row(), self._blank_style_row()
        elif final == "K":
            mode = nums[0] if nums else 0
            row, st = self.grid[self.r], self.style[self.r]
            if mode == 0:
                row[self.c:] = [" "] * (self.cols - self.c)
                st[self.c:] = [""] * (self.cols - self.c)
            elif mode == 1:
                row[:self.c + 1] = [" "] * (self.c + 1)
                st[:self.c + 1] = [""] * (self.c + 1)
            else:
                self.grid[self.r] = self._blank_row()
                self.style[self.r] = self._blank_style_row()
        elif final == "X":
            row, st = self.grid[self.r], self.style[self.r]
            for i in range(self.c, min(self.cols, self.c + n())):
                row[i], st[i] = " ", ""
        elif final == "@":
            k = min(n(), self.cols - self.c)
            for grid, blank in ((self.grid[self.r], " "), (self.style[self.r], "")):
                grid[self.c:] = ([blank] * k + grid[self.c:])[:self.cols - self.c]
        elif final == "P":
            k = min(n(), self.cols - self.c)
            for grid, blank in ((self.grid[self.r], " "), (self.style[self.r], "")):
                grid[self.c:] = grid[self.c + k:] + [blank] * k
        elif final == "L":
            if self.top <= self.r <= self.bottom:
                for _ in range(n()):
                    del self.grid[self.bottom]
                    self.grid.insert(self.r, self._blank_row())
                    del self.style[self.bottom]
                    self.style.insert(self.r, self._blank_style_row())
        elif final == "M":
            if self.top <= self.r <= self.bottom:
                for _ in range(n()):
                    del self.grid[self.r]
                    self.grid.insert(self.bottom, self._blank_row())
                    del self.style[self.r]
                    self.style.insert(self.bottom, self._blank_style_row())
        elif final == "S":
            self._scroll_up(n())
        elif final == "T":
            self._scroll_down(n())
        elif final == "r":
            top, bot = n(0) - 1, n(1, self.rows) - 1
            if 0 <= top < bot < self.rows:
                self.top, self.bottom = top, bot
            else:
                self.top, self.bottom = 0, self.rows - 1
            self.r = self.c = 0
        elif final == "s" and not priv:
            self.saved = (self.r, self.c)
        elif final == "u" and not priv:
            self.r, self.c = self.saved
        elif final in "hl":
            on = final == "h"
            for p in parts:
                key = priv + p
                if on:
                    self.modes.add(key)
                else:
                    self.modes.discard(key)
                if priv == "?" and p in ("1049", "1047", "47"):
                    self._set_alt(on)
                elif priv == "?" and p == "25":
                    self.cursor_visible = on
                elif priv == "?" and p == "7":
                    self.autowrap = on
        elif final == "n" and not priv:
            if nums and nums[0] == 6:
                self.responses.append(b"\x1b[%d;%dR" % (self.r + 1, self.c + 1))
            elif nums and nums[0] == 5:
                self.responses.append(b"\x1b[0n")
        elif final == "c" and not priv and (not nums or nums[0] == 0):
            self.responses.append(b"\x1b[?62;22c")
        self._clamp()

    def _set_alt(self, on):
        if on == self.alt_active:
            return
        if on:
            self.saved_main_cursor = (self.r, self.c)
            self.alt, self.alt_style = self._blank_grid(), self._blank_styles()
            self.grid, self.style = self.alt, self.alt_style
        else:
            self.grid, self.style = self.main, self.main_style
            self.r, self.c = getattr(self, "saved_main_cursor", (self.r, self.c))
        self.alt_active = on
        self._last = None

    # -- style --------------------------------------------------------------
    _SGR_FLAGS = {1: "bold", 2: "dim", 3: "italic", 4: "underline", 5: "blink",
                  7: "inverse", 8: "hidden", 9: "strike", 53: "overline"}
    _SGR_OFF = {22: ("bold", "dim"), 23: ("italic",), 24: ("underline",),
                25: ("blink",), 27: ("inverse",), 28: ("hidden",), 29: ("strike",),
                55: ("overline",)}

    def _sgr(self, buf):
        """Apply one SGR sequence to the pen. Colours are kept exactly as
        spelled (5;n, 2;r;g;b, colon forms), since both sides spelling them the
        same way is part of what is compared."""
        params = buf.split(";") if buf else ["0"]
        i = 0
        while i < len(params):
            p = params[i]
            head = p.split(":")[0]
            code = int(head) if head.isdigit() else 0
            if ":" in p and code in (38, 48, 58, 4):
                key = {38: "fg", 48: "bg", 58: "ul", 4: "underline"}[code]
                self.sgr[key] = p
            elif code in (38, 48, 58):
                key = {38: "fg", 48: "bg", 58: "ul"}[code]
                if i + 1 < len(params) and params[i + 1] == "5":
                    self.sgr[key] = ";".join(params[i:i + 3])
                    i += 2
                elif i + 1 < len(params) and params[i + 1] == "2":
                    self.sgr[key] = ";".join(params[i:i + 5])
                    i += 4
            elif code == 0:
                self.sgr = {}
            elif code in self._SGR_FLAGS:
                self.sgr[self._SGR_FLAGS[code]] = "1"
            elif code in self._SGR_OFF:
                for k in self._SGR_OFF[code]:
                    self.sgr.pop(k, None)
            elif 30 <= code <= 37 or 90 <= code <= 97:
                self.sgr["fg"] = str(code)
            elif 40 <= code <= 47 or 100 <= code <= 107:
                self.sgr["bg"] = str(code)
            elif code == 39:
                self.sgr.pop("fg", None)
            elif code == 49:
                self.sgr.pop("bg", None)
            elif code == 59:
                self.sgr.pop("ul", None)
            i += 1
        self._repen()

    def _repen(self):
        style = ",".join("%s=%s" % kv for kv in sorted(self.sgr.items()))
        self.pen = style + ("|link=" + self.link if self.link else "")

    # -- output -------------------------------------------------------------
    def take_responses(self):
        out, self.responses = b"".join(self.responses), []
        return out

    def lines(self):
        return ["".join(row).rstrip() for row in self.grid]

    def text(self):
        return "\n".join(self.lines()).rstrip("\n")

    def styled_text(self):
        """The screen with every style change marked inline as {style}, e.g.
        `{bold=1,fg=31}Error{} plain {|link=https://x}here{}` - comparable
        text that differs whenever colour, emphasis or a link differs."""
        out = []
        for row, st in zip(self.grid, self.style):
            parts, cur = [], ""
            last = max((i for i, ch in enumerate(row) if ch != " " or st[i]), default=-1)
            for i in range(last + 1):
                if st[i] != cur:
                    parts.append("{%s}" % st[i])
                    cur = st[i]
                parts.append(row[i])
            if cur:
                parts.append("{}")
            out.append("".join(parts))
        return "\n".join(out).rstrip("\n")
