"""From IDL packets to GIS feed messages and records.

Every IDL packet of the feed starts with one block-flag byte, followed by the
next bytes of a continuous byte stream. The byte stream carries messages:

  10 02 07 SQ 05 00 T1 T2 01 06 LL LH KD FR 00 <payload, LL|LH<<8 bytes> 00 10 03

  SQ     message sequence number (mod 256)
  T1 T2  record type: 10 10 Reuters page data, 10 20 GIS screen, 50 30 packed data
  KD     1 or 2 (unknown meaning, possibly a priority)
  FR     fragment: high nibble = fragment number, low nibble = 0 single,
         2 first, 3 middle, 1 last fragment

Long records are split into fragments of at most 494 payload bytes, sent as
messages with consecutive sequence numbers.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List

START = b"\x10\x02\x07"
END = b"\x00\x10\x03"


@dataclass
class Message:
    frame: int
    seq: int
    rtype: int
    kind: int
    frag: int
    payload: bytes

    @property
    def key(self):
        return (self.seq, self.rtype, self.frag, self.payload)


@dataclass
class Record:
    frame: int
    rtype: int
    kind: int
    seq: int
    payload: bytes
    fragments: int = 1
    streams: set = field(default_factory=set)


def byte_runs(packets):
    """packets: iterable of (frame, ci, data) of one stream in time order.

    Yields runs of (byte string, frame of each byte) without CI gaps."""
    buf = bytearray()
    frames = []
    last = None
    for frame, ci, data in packets:
        if last is not None and ci != (last + 1) & 0xFF:
            if buf:
                yield bytes(buf), frames
            buf, frames = bytearray(), []
        body = data[1:]
        buf += body
        frames += [frame] * len(body)
        last = ci
    if buf:
        yield bytes(buf), frames


def parse_messages(run, frames):
    out = []
    i = 0
    while True:
        j = run.find(START, i)
        if j < 0 or j + 15 > len(run):
            break
        length = run[j + 10] | run[j + 11] << 8
        end = j + 15 + length
        if end + 3 <= len(run) and run[end:end + 3] == END:
            out.append(Message(frames[j], run[j + 3], run[j + 6] << 8 | run[j + 7],
                               run[j + 12], run[j + 13], run[j + 15:end]))
            i = end + 3
        else:
            i = j + 1
    return out


def stream_messages(packets):
    msgs = []
    for run, frames in byte_runs(packets):
        msgs += parse_messages(run, frames)
    return msgs


def chain(messages):
    """Joins fragments of one stream into records."""
    out = []
    pending = {}
    for m in messages:
        pos, part = m.frag & 0xF, m.frag >> 4
        if pos in (0, 2):
            pending[m.rtype] = [m]
        else:
            prev = pending.get(m.rtype)
            if not prev or prev[-1].seq != (m.seq - 1) & 0xFF or (prev[-1].frag >> 4) + 1 != part:
                pending.pop(m.rtype, None)
                continue
            prev.append(m)
        if pos in (0, 1):
            parts = pending.pop(m.rtype)
            out.append(Record(parts[0].frame, m.rtype, parts[0].kind, parts[0].seq,
                              b"".join(p.payload for p in parts), len(parts)))
    return out


def records_from_streams(streams):
    """streams: {stream id: [(frame, ci, data), ...]}.

    The feed is sent twice (once in each field) with independent continuity
    counters, so records found in both copies are merged."""
    merged = defaultdict(list)
    stats = {}
    for sid, pk in streams.items():
        recs = chain(stream_messages(pk))
        stats[sid] = len(recs)
        for r in recs:
            same = merged[(r.rtype, r.seq, r.payload)]
            for o in same:
                if abs(o.frame - r.frame) < 250:  # the copies are seconds apart
                    o.frame = min(o.frame, r.frame)
                    o.streams.add(sid)
                    break
            else:
                r.streams = {sid}
                same.append(r)
    recs = sorted((r for v in merged.values() for r in v), key=lambda r: r.frame)
    return recs, stats


def group_streams(packets, min_packets=20):
    """packets: iterable of (frame, row, IdlPacket) with CRC ok, in frame order.

    One stream per (data channel, SPA, field). Streams with fewer than
    min_packets packets are dropped: they are misdetections that happen to
    pass the 16-bit CRC."""
    streams = defaultdict(list)
    for frame, row, p in packets:
        streams[(p.channel, p.spa, row >= 16)].append((frame, p.ci, p.data))
    return {k: v for k, v in streams.items() if len(v) >= min_packets}
