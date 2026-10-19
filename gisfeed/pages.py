"""Rendering of GIS feed records.

Record type 0x1010 carries Reuters Monitor data as received by GIS:
  "PR" b <page text>    full page; the text starts with the 8-character page
                        header (4-letter page code + HHMM time stamp, GMT)
  "UP" 00 <code> {10 row col text}...   update of parts of a page
  "HL" ...              news headline
Page text is 64 columns x 14 rows, ASCII; 1A n inserts (n - 0x1F) spaces.
The text of a full page starts with the 8-character header (page code and
time). It is followed by the page itself, whose first row only has 60
columns: the last four columns of row 0 show the time from the header.
In updates, row and column are given as (value + 0x20).

Record type 0x1020 is a GIS screen: 4-character screen id, one byte, then a
78 x 22 screen in code page 437.
"""
from . import packed

SKIP = 0x1A
COLS, ROWS = 64, 14
GIS_COLS, GIS_ROWS = 78, 22


def expand(data):
    out = []
    i = 0
    while i < len(data):
        c = data[i]
        if c == SKIP and i + 1 < len(data):
            out.append(" " * max(0, data[i + 1] - 0x1F))
            i += 2
            continue
        out.append(chr(c) if 32 <= c < 127 else "·")
        i += 1
    return "".join(out)


class ReutersPage:
    def __init__(self, code):
        self.code = code
        self.rows = [[" "] * COLS for _ in range(ROWS)]
        self.complete = False
        self.updated = None

    def put(self, row, col, text):
        for ch in text:
            if col >= (COLS - 4 if row == 0 else COLS):
                row, col = row + 1, 0
            if row >= ROWS:
                return
            self.rows[row][col] = ch
            col += 1

    def set_time(self, hhmm):
        if len(hhmm) == 4:
            self.rows[0][COLS - 4:] = list(hhmm)

    def text(self):
        return "\n".join("".join(r) for r in self.rows)


def split_updates(body):
    """Splits the body of an UP record into (code, [(row, col, text)])."""
    parts = body.split(b"\x10")
    code = parts[0].decode("latin-1")
    edits = []
    for p in parts[1:]:
        if len(p) >= 2:
            edits.append((p[0] - 0x20, p[1] - 0x20, expand(p[2:])))
    return code, edits


class PageStore:
    def __init__(self):
        self.pages = {}
        self.gis = {}

    def page(self, code4):
        if code4 not in self.pages:
            self.pages[code4] = ReutersPage(code4)
        return self.pages[code4]

    def apply(self, rec):
        """Applies a record; returns a one-line description and optional text."""
        p = rec.payload
        if rec.rtype == 0x1010:
            tag = p[:2]
            if tag == b"PR" and len(p) > 11:
                text = expand(p[3:])
                code = text[:8].strip()
                page = self.page(code[:4])
                page.rows = [[" "] * COLS for _ in range(ROWS)]
                page.put(0, 0, text[8:])
                page.set_time(code[4:8])
                page.complete = True
                page.updated = rec.frame
                return f"Reuters page {code} (full)", page.text()
            if tag == b"UP" and len(p) > 3:
                code, edits = split_updates(p[3:])
                page = self.page(code[:4])
                page.set_time(code[4:8])
                for row, col, txt in edits:
                    if 0 <= row < ROWS and 0 <= col < COLS:
                        page.put(row, col, txt)
                page.updated = rec.frame
                desc = "; ".join(f"r{r}c{c}={t!r}" for r, c, t in edits)
                return f"Reuters update {code}: {desc}", None
            return f"Reuters {tag.decode('latin-1')!r} record", expand(p)
        if rec.rtype == 0x1020 and len(p) > 5:
            sid = p[:4].decode("latin-1")
            body = p[5:].decode("cp437")
            rows = [body[i:i + GIS_COLS] for i in range(0, len(body), GIS_COLS)]
            text = "\n".join(rows[:GIS_ROWS])
            self.gis[sid] = text
            return f"GIS screen {sid}", text
        if rec.rtype == 0x5030:
            frags = packed.text_fragments(p)
            return f"packed record, {len(p)} bytes", " | ".join(frags)
        return f"record type {rec.rtype:04x}, {len(p)} bytes", None
