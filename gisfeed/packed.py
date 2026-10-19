"""Packed records (record type 0x5030).

These records are not plain text. They are nibble-oriented: when the payload
is read as a stream of 4-bit units (low nibble of each byte first, i.e. in
transmission order), ASCII text fields appear on both even and odd nibble
boundaries. Fields of the form "E" + 7 characters are common, and numeric
values appear to be nibble coded. The content looks like a securities
database (issuers, currencies such as DMK, month names, ratings), but the
exact field layout is not yet decoded.

text_fragments() recovers the readable text by scanning both nibble phases.
"""


def nibbles(data):
    out = []
    for b in data:
        out += (b & 15, b >> 4)
    return out


def text_fragments(data, min_len=3):
    """Readable ASCII runs, in order, taken at whichever nibble phase they occur."""
    nib = nibbles(data)
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
