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


@dataclass
class IdlPacket:
    channel: int
    spa: int
    ci: Optional[int]
    repeat: Optional[int]
    data: bytes
    crc_ok: bool
    raw: bytes


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
    ok = crc_register(pkt[crc_start:45]) == 0
    if not ok and n < room:
        ok = crc_register(pkt[crc_start: pos + n] + pkt[43:45]) == 0
    return IdlPacket(ch, spa, ci, repeat, bytes(data), ok, bytes(pkt))
