"""vtscreen.py - a small terminal emulator, just enough to snapshot Ink's TUI.

scripts/harness.py drives the native binary and the artifact through a pty
and compares what each leaves on the SCREEN, not the raw byte streams: the two
renderers are free to reach the same screen by different cursor movements (and
timing alone reorders frames), so bytes would differ where screens do not.

Standard library only, like every tool in this repo. It models what Claude
Code's Ink renderer was measured to emit - printable text, CR/LF/BS/TAB, CSI
cursor movement and erasure, scroll regions, insert/delete, the alternate
screen, save/restore cursor - and IGNORES styling (SGR), hyperlinks (OSC 8),
titles and every other OSC/DCS/APC string. It answers the queries a TUI blocks
on (cursor position, device attributes) so that neither side stalls on a
timeout the other does not hit. Widths come from unicodedata: East Asian
Wide/Fullwidth are two cells, combining marks and format characters zero.
Both sides go through this same emulator, so its approximations - emoji width,
above all - cancel out of every native-vs-artifact comparison.
"""

import codecs
import unicodedata


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

    # -- grid helpers -------------------------------------------------------
    def _blank_row(self):
        return [" "] * self.cols

    def _blank_grid(self):
        return [self._blank_row() for _ in range(self.rows)]

    def _scroll_up(self, n=1):
        for _ in range(n):
            del self.grid[self.top]
            self.grid.insert(self.bottom, self._blank_row())

    def _scroll_down(self, n=1):
        for _ in range(n):
            del self.grid[self.bottom]
            self.grid.insert(self.top, self._blank_row())

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

    def _print(self, ch):
        w = char_width(ch)
        if w == 0:
            # attach to the previous cell so a combining mark is not lost
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
        if w == 2 and self.c + 1 < self.cols:
            row[self.c + 1] = ""
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
            g = self.grid
            if mode == 0:
                g[self.r][self.c:] = [" "] * (self.cols - self.c)
                for i in range(self.r + 1, self.rows):
                    g[i] = self._blank_row()
            elif mode == 1:
                g[self.r][:self.c + 1] = [" "] * (self.c + 1)
                for i in range(0, self.r):
                    g[i] = self._blank_row()
            elif mode in (2, 3):
                for i in range(self.rows):
                    g[i] = self._blank_row()
        elif final == "K":
            mode = nums[0] if nums else 0
            row = self.grid[self.r]
            if mode == 0:
                row[self.c:] = [" "] * (self.cols - self.c)
            elif mode == 1:
                row[:self.c + 1] = [" "] * (self.c + 1)
            else:
                self.grid[self.r] = self._blank_row()
        elif final == "X":
            row = self.grid[self.r]
            for i in range(self.c, min(self.cols, self.c + n())):
                row[i] = " "
        elif final == "@":
            row = self.grid[self.r]
            k = min(n(), self.cols - self.c)
            row[self.c:] = ([" "] * k + row[self.c:])[:self.cols - self.c]
        elif final == "P":
            row = self.grid[self.r]
            k = min(n(), self.cols - self.c)
            row[self.c:] = row[self.c + k:] + [" "] * k
        elif final == "L":
            if self.top <= self.r <= self.bottom:
                for _ in range(n()):
                    del self.grid[self.bottom]
                    self.grid.insert(self.r, self._blank_row())
        elif final == "M":
            if self.top <= self.r <= self.bottom:
                for _ in range(n()):
                    del self.grid[self.r]
                    self.grid.insert(self.bottom, self._blank_row())
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
            self.alt = self._blank_grid()
            self.grid = self.alt
        else:
            self.grid = self.main
            self.r, self.c = getattr(self, "saved_main_cursor", (self.r, self.c))
        self.alt_active = on

    # -- output -------------------------------------------------------------
    def take_responses(self):
        out, self.responses = b"".join(self.responses), []
        return out

    def lines(self):
        return ["".join(row).rstrip() for row in self.grid]

    def text(self):
        return "\n".join(self.lines()).rstrip("\n")
