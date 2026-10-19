"""From IDL packets to GIS feed messages and records.

Every IDL packet of the feed starts with one block-flag byte, followed by the
next bytes of a continuous byte stream. The byte stream carries messages:

  10 02 07 SQ 05 00 T1 T2 01 06 LL LH KD FR 00 <payload, LL|LH<<8 bytes> 00 10 03

or, when there is nothing to send, short control messages such as
  10 02 07 SQ 01 00 00 00 00 00 10 03

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

import numpy as np

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
    """Finds the messages in a run of the byte stream.

    Data messages (class 05) carry a length; other classes (01, 03, ...) are
    short control messages sent while there is nothing to transmit, which
    end at the next 10 03 and are returned with rtype None."""
    out = []
    i = 0
    while True:
        j = run.find(START, i)
        if j < 0 or j + 6 > len(run):
            break
        cls = run[j + 4]
        if cls == 0x05 and j + 15 <= len(run):
            length = run[j + 10] | run[j + 11] << 8
            end = j + 15 + length
            if end + 3 <= len(run) and run[end:end + 3] == END:
                out.append(Message(frames[j], run[j + 3], run[j + 6] << 8 | run[j + 7],
                                   run[j + 12], run[j + 13], run[j + 15:end]))
                i = end + 3
                continue
        elif cls != 0x05:
            k = run.find(b"\x10\x03", j + 5)
            if 0 <= k < j + 64:
                out.append(Message(frames[j], run[j + 3], None, cls, 0, run[j + 4:k]))
                i = k + 2
                continue
        i = j + 1
    return out


def stream_messages(packets):
    msgs = []
    for run, frames in byte_runs(packets):
        msgs += parse_messages(run, frames)
    return msgs


def control_messages(streams):
    """Counts the control messages per class over all streams."""
    counts = defaultdict(int)
    for pk in streams.values():
        for m in stream_messages(pk):
            if m.rtype is None:
                counts[m.kind] += 1
    return dict(counts)


def chain(messages):
    """Joins fragments of one stream into records."""
    out = []
    pending = {}
    for m in messages:
        if m.rtype is None:
            continue
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


def _delta_track(primary, other, max_lag=3):
    """Continuity indicator offset between two copies of the feed over time.

    Returns a sorted list of (frame, delta) from packets whose user data
    (after the block flag) is identical in both copies."""
    index = defaultdict(list)
    for frame, ci, data in other:
        index[data[1:]].append((frame, ci))
    track = []
    for frame, ci, data in primary:
        for f2, ci2 in index.get(data[1:], ()):
            if abs(f2 - frame) <= max_lag:
                track.append((frame, (ci2 - ci) & 0xFF))
                break
    return track


def fill_gaps(primary, other, max_lag=3):
    """Fills missing packets of one copy of the feed from the other copy.

    The copy in the other field carries the same user data (except the block
    flag) with its continuity indicator offset by a constant that is found
    from matching packets. Returns the filled list of (frame, ci, data)."""
    track = _delta_track(primary, other, max_lag)
    if not track or not primary:
        return primary
    frames = np.array([t[0] for t in track])
    deltas = [t[1] for t in track]
    by_ci = defaultdict(list)
    for frame, ci, data in other:
        by_ci[ci].append((frame, data))

    def delta_at(frame):
        i = int(np.searchsorted(frames, frame))
        lo, hi = max(0, i - 25), min(len(deltas), i + 25)
        vals = deltas[lo:hi]
        return max(set(vals), key=vals.count)

    out = [primary[0]]
    for frame, ci, data in primary[1:]:
        pf, pci, _ = out[-1]
        gap = (ci - pci - 1) & 0xFF
        if 0 < gap < 64 and frame - pf <= 50:
            d = delta_at(frame)
            for k in range(1, gap + 1):
                want = (pci + k) & 0xFF
                for f2, d2 in by_ci.get((want + d) & 0xFF, ()):
                    if pf - max_lag <= f2 <= frame + max_lag:
                        out.append((f2, want, d2))
                        break
                else:
                    break  # keep the remaining gap
        out.append((frame, ci, data))
    return out


def group_streams(packets, min_packets=20):
    """packets: iterable of (frame, row, IdlPacket) with CRC ok, in frame order.

    One stream per (data channel, SPA, field). Streams with fewer than
    min_packets packets are dropped: they are misdetections that happen to
    pass the 16-bit CRC."""
    streams = defaultdict(list)
    for frame, row, p in packets:
        if p.ci is None:  # only the explicit-CI format of the feed is handled
            continue
        streams[(p.channel, p.spa, row >= 16)].append((frame, p.ci, p.data))
    # streams far smaller than the main one come from packets whose address
    # or CRC was damaged in a way the CRC did not catch
    largest = max((len(v) for v in streams.values()), default=0)
    streams = {k: v for k, v in streams.items() if len(v) >= max(min_packets, 0.01 * largest)}
    # each field's copy is completed from the other field's copy
    filled = {}
    for (ch, spa, f2), pk in streams.items():
        other = streams.get((ch, spa, not f2))
        filled[(ch, spa, f2)] = fill_gaps(pk, other) if other else pk
    return filled
