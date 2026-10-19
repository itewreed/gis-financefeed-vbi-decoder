"""Teletext Independent Data Line (IDL) packets, Format A.

Layout of a decoded 45-byte line (byte offsets):
  0-1  clock run-in, 2 framing code
  3    data channel (Hamming 8/4)        4  designation code (Hamming 8/4, 15)
  5    format type FT (Hamming 8/4)      6  interpretation/address length IAL
  7..  service packet address (Hamming 8/4 nibbles), then optional RI, CI, DL
  ...  user data, last two bytes CRC

The CRC polynomial is x^16 + x^9 + x^7 + x^4 + 1, bits fed in transmission
order (LSB of each byte first) starting at the CI byte. In this feed, packets
shorter than the full user data group carry the CRC over CI, DL and the DL
user-data bytes only, not over the filler.
"""
from dataclasses import dataclass
from typing import Optional

from .wst import HAM84



def crc_register(data):
    """Runs the Format A CRC register over data; 0 means a valid packet."""
    r = 0
    for byte in data:
        for i in range(8):
            fb = ((r >> 15) & 1) ^ ((byte >> i) & 1)
            r = (r << 1) & 0xFFFF
            if fb:
                r ^= 0x0291
    return r


# Bit error patterns the Viterbi detector typically leaves behind (single
# bits and short bursts); used for CRC based error correction.
ERROR_PATTERNS = ((1,), (1, 1), (1, 1, 1), (1, 1, 1, 1), (1, 0, 1), (1, 1, 1, 1, 1))
_SYNDROMES = {}


def _syndrome_table(nbytes):
    """Maps CRC syndromes to error positions for a CRC span of nbytes."""
    if nbytes not in _SYNDROMES:
        nbits = nbytes * 8
        table = {}
        for pat in ERROR_PATTERNS:
            for start in range(nbits - len(pat) + 1):
                e = bytearray(nbytes)
                for i, b in enumerate(pat):
                    if b:
                        k = start + i
                        e[k // 8] |= 1 << (k % 8)
                syn = crc_register(e)
                # ambiguous syndromes are not used
                table[syn] = None if syn in table else bytes(e)
        _SYNDROMES[nbytes] = table
    return _SYNDROMES[nbytes]


@dataclass
class IdlPacket:
    channel: int
    spa: int
    ci: Optional[int]
    repeat: Optional[int]
    data: bytes
    crc_ok: bool
    raw: bytes
    corrected: int = 0  # number of bits fixed by CRC error correction


def parse(pkt: bytes) -> Optional[IdlPacket]:
    ch, e1 = HAM84[pkt[3]]
    dc, e2 = HAM84[pkt[4]]
    ft, e3 = HAM84[pkt[5]]
    ial, e4 = HAM84[pkt[6]]
    if min(ch, dc, ft, ial) < 0 or dc != 15 or ft & 1:
        return None
    spa_len = ial & 7
    if spa_len == 7:
        return None
    pos = 7
    spa = 0
    for i in range(spa_len):
        nib, e = HAM84[pkt[pos]]
        if nib < 0:
            return None
        spa |= nib << (4 * i)
        pos += 1
    crc_start = pos
    repeat = ci = None
    if ft & 2:
        repeat = pkt[pos]
        pos += 1
        crc_start = pos
    if ft & 4:
        ci = pkt[pos]
        pos += 1
    dl = None
    if ft & 8:
        dl = pkt[pos] & 0x3F
        pos += 1
    room = 43 - pos
    n = room if dl is None else min(dl, room)
    data = pkt[pos: pos + n]
    full = n == room
    span = pkt[crc_start:45] if full else pkt[crc_start: pos + n] + pkt[43:45]
    syn = crc_register(span)
    if syn == 0 or full:
        return IdlPacket(ch, spa, ci, repeat, bytes(data), syn == 0, bytes(pkt))
    # filler not covered by the CRC (see module docstring); fall back to the
    # standard span if that one matches
    ok = crc_register(pkt[crc_start:45]) == 0
    return IdlPacket(ch, spa, ci, repeat, bytes(data), ok, bytes(pkt))


def correct(pkt: bytes) -> Optional[IdlPacket]:
    """Tries to repair a packet whose CRC fails, assuming one short error burst.

    Returns the repaired packet (with .corrected set) or None."""
    p = parse(pkt)
    if p is None or p.crc_ok or p.ci is None:
        return None
    # locate the CRC span exactly as parse() does
    ft = HAM84[pkt[5]][0]
    pos = 7 + (HAM84[pkt[6]][0] & 7)
    crc_start = pos + (1 if ft & 2 else 0)
    dl_pos = crc_start + 1
    dl = pkt[dl_pos] & 0x3F if ft & 8 else 33
    end = dl_pos + 1 + dl
    if end >= 43:
        idx = list(range(crc_start, 45))
    else:
        idx = list(range(crc_start, end)) + [43, 44]
    span = bytes(pkt[i] for i in idx)
    err = _syndrome_table(len(span)).get(crc_register(span))
    if err is None:
        return None
    fixed = bytearray(pkt)
    for i, e in zip(idx, err):
        fixed[i] ^= e
    q = parse(bytes(fixed))
    if q is None or not q.crc_ok:
        return None
    q.corrected = sum(bin(e).count("1") for e in err)
    return q
