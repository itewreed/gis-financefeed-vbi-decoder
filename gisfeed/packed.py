"""Securities records (record type 0x5030).

A record carries one or more entries, each describing one security quoted
at one exchange. Entries come in two forms.

Packed entries (most of them):

    LL  FA 85 E7 82 ...  header ...  <fields>  CC

  LL       entry length in bytes (including LL itself)
  fields   a sequence of variable-length fields; the payload is read as a
           stream of 4-bit nibbles, low nibble of each byte first, and every
           field is one nibble L followed by L nibbles of data. Text is read
           in pairs of nibbles (low, high) per character; numbers are the
           nibbles in reverse order read as a hexadecimal number. Leading
           zero bytes and trailing spaces are removed, which is what makes
           the entries short.

  The fields, starting with the exchange code:

    exchange   3 letters: region, country, exchange (EDF = Europe, Germany,
               Frankfurt; EDD Duesseldorf, EDM Munich, EDB Berlin, EDH Hamburg,
               EDS Stuttgart, EIM Milan, NAY New York, FJT Tokyo, AAS Sydney, ...)
    -          always 11
    high, low, last   prices x 10000 (high and low are most likely the
               high and low of the year)
    price date packed date of the last price (see unpack_date)
    currency   DMK, USD, ITL, JPY, XEU, ...
    type, status   two short codes, meaning unknown
    -, symbol, -, number   symbol (shares) and a number of unknown meaning
    country    3-digit code (004 for German issuers)
    coupon day, coupon month   e.g. 16 JAN, or "JJ" for January/July
    maturity   packed date
    name       several fields of up to 7 characters, joined
    coupon     interest rate x 100000 (650000 = 6.5 %)
    frequency  coupon payments per year

Full entries are 200 bytes with fixed positions, see parse_full(). They
carry the same information unpacked and confirm the meaning of the packed
fields.
"""
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

MONTHS = ("JAN", "FEB", "MRZ", "APR", "MAI", "JUN", "JUL", "AUG", "SEP", "OKT", "NOV", "DEZ")


@dataclass
class Security:
    exchange: str
    name: str = ""
    symbol: str = ""
    currency: str = ""
    high: Optional[float] = None
    low: Optional[float] = None
    last: Optional[float] = None
    price_date: str = ""
    coupon: Optional[float] = None
    coupon_date: str = ""
    maturity: str = ""
    frequency: str = ""
    country: str = ""
    type_code: str = ""
    status: str = ""
    entry_id: str = ""
    form: str = "packed"

    def as_dict(self):
        return asdict(self)


def nibbles(data):
    return "".join("%x%x" % (b & 15, b >> 4) for b in data)


def nib_num(d):
    return int(d[::-1], 16) if d else 0


def nib_text(d):
    return "".join(chr(int(d[k + 1] + d[k], 16)) for k in range(0, len(d) - 1, 2))


def unpack_date(v):
    """16-bit date: bits 0-6 year (two digits), 7-10 month, 11-15 day."""
    if not v:
        return ""
    y, m, d = v & 0x7F, (v >> 7) & 15, v >> 11
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return ""
    year = 1900 + y if y >= 50 else 2000 + y
    return f"{year:04d}-{m:02d}-{d:02d}"


def split_fields(s):
    out = []
    i = 0
    while i < len(s):
        n = int(s[i], 16)
        d = s[i + 1:i + 1 + n]
        if len(d) < n:
            break
        out.append(d)
        i += 1 + n
    return out


_CODE = re.compile("6((?:[0-9a-f]{2}){3})1b")


def parse_packed(entry):
    """Parses one packed entry given as a nibble string; None if not understood."""
    for m in _CODE.finditer(entry, 18, 64):
        code = nib_text(m.group(1))
        if code.isalpha() and code.isupper():
            break
    else:
        return None
    f = split_fields(entry[m.start():])
    if len(f) < 21:
        return None

    def text(i):
        return nib_text(f[i]).strip() if i < len(f) else ""

    s = Security(code, entry_id=entry[10:16])
    prices = [nib_num(x) / 10000 for x in f[2:5]]
    if any(prices):
        s.high, s.low, s.last = prices
    s.price_date = unpack_date(nib_num(f[5]))
    s.currency = text(6)
    s.type_code, s.status = text(7), text(8)
    s.symbol = text(10)
    s.country = text(13)
    day, month = nib_num(f[14]), text(15)
    if month:
        s.coupon_date = f"{day} {month}" if day else month
    s.maturity = unpack_date(nib_num(f[16]))
    # name pieces run until three empty fields; then coupon and frequency
    name = []
    i = 17
    while i < len(f) and f[i] != "":
        name.append(nib_text(f[i]))
        i += 1
    s.name = clean_name("".join(name))
    # after the name: three empty fields, then coupon and payment frequency
    rest = f[i:]
    while rest and rest[0] == "":
        rest = rest[1:]
    if len(rest) >= 2 and len(rest[1]) == 2 and nib_text(rest[1]).strip().isdigit():
        c = nib_num(rest[0])
        if 0 < c < 3000000:
            s.coupon = c / 100000
        s.frequency = nib_text(rest[1]).strip()
    return s


def clean_name(name):
    """Removes the zero padding that follows the name of shares and warrants."""
    name = "".join(c for c in name if c >= " ")
    return " ".join(re.sub(r"0{4,}\s*$", "", name).split())


def parse_full(b):
    """Parses a 200-byte full entry."""
    def i32(o):
        return int.from_bytes(b[o:o + 4], "little")

    def txt(a, n):
        return b[a:a + n].split(b"\0")[0].decode("latin-1").strip()

    def stamp(o):
        h, mi, d, mo, y = b[o:o + 5]
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return ""
        return f"{1900 + y if y >= 50 else 2000 + y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"

    s = Security(txt(10, 3), form="full", entry_id=b[0:3].hex())
    s.symbol = txt(4, 6)
    s.last = i32(16) / 10000 or None
    s.high = i32(51) / 10000 or None
    s.low = i32(55) / 10000 or None
    s.currency = txt(59, 3)
    s.price_date = stamp(66)
    s.type_code, s.status = txt(71, 4), txt(75, 4)
    s.coupon_date = (f"{b[94]} " if b[94] else "") + txt(95, 4) if txt(95, 4) else ""
    s.country = txt(99, 3)
    s.name = clean_name(txt(108, 38))
    d, m, y = b[150:153]
    if 1 <= m <= 12 and 1 <= d <= 31:
        s.maturity = f"{1900 + y if y >= 50 else 2000 + y:04d}-{m:02d}-{d:02d}"
        c = i32(153)
        s.coupon = c / 100000 if 0 < c < 3000000 else None
        s.frequency = txt(157, 1)
    return s


def parse_record(payload):
    """All securities in a 0x5030 record payload."""
    if len(payload) == 200 and payload[10:13].isalpha():
        return [parse_full(payload)]
    n = nibbles(payload)
    out = []
    i = 0
    while i + 20 <= len(n):
        length = int(n[i + 1] + n[i], 16) * 2
        if length < 20 or n[i + 2:i + 6] != "fa85":
            break
        s = parse_packed(n[i:i + length])
        if s is not None:
            out.append(s)
        i += length
    return out


def text_fragments(data, min_len=3):
    """Readable ASCII runs, in order, taken at whichever nibble phase they occur."""
    nib = [int(c, 16) for c in nibbles(data)]
    out = []
    i = 0
    n = len(nib)
    while i < n - 1:
        j = i
        chars = []
        while j < n - 1:
            v = nib[j] | nib[j + 1] << 4
            if not 32 <= v < 127:
                break
            chars.append(chr(v))
            j += 2
        text = "".join(chars).strip()
        if len(chars) >= min_len and len(text) >= min_len and any(c.isalpha() or c.isdigit() for c in text):
            out.append(text)
            i = j
        else:
            i += 1
    return out
