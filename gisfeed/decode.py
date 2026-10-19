"""Decoding of IDL packets from a whole capture."""
import multiprocessing as mp
from dataclasses import dataclass

import numpy as np

from . import idl, wst
from .vbi import BTTV_PAL, open_vbi

ACTIVE_STD = 15.0  # raw sample std above which a line is considered to carry data


@dataclass
class Channel:
    taps: np.ndarray
    offset: float


def decode_rows(frame_data, rows, sampler, ch):
    """Decodes the given rows of one frame. Yields (row, pkt, err, y, bits)."""
    for r in rows:
        line = frame_data[r]
        if line.std() < ACTIVE_STD:
            continue
        res = wst.decode_line(line, sampler, ch.taps, ch.offset)
        if res is not None:
            yield (r,) + tuple(res)


def survey(frames, fmt, ch, lines_per_row=30, scan_frames=4000):
    """Finds rows that carry IDL packets and refines the channel model.

    Looks at up to lines_per_row active lines of every row, spread over the
    recording. Returns (rows, channel, {row: (decoded, crc ok)})."""
    sampler = wst.LineSampler(fmt.sample_rate)
    n = len(frames)
    picks = np.unique(np.linspace(0, n - 1, min(n, scan_frames)).astype(int))
    lines = {r: [] for r in range(fmt.lines_per_frame)}
    for f in picks:
        data = np.asarray(frames[f])
        act = data.astype(float).std(1) > ACTIVE_STD
        for r in np.nonzero(act)[0]:
            if len(lines[r]) < lines_per_row * 4:
                lines[r].append(data[r])
        if all(len(v) >= lines_per_row * 4 for v in lines.values()):
            break
    # spread the chosen lines over what was collected
    for r in lines:
        v = lines[r]
        if len(v) > lines_per_row:
            lines[r] = [v[i] for i in np.linspace(0, len(v) - 1, lines_per_row).astype(int)]

    def run(rows, ch):
        stats, samples, bits = {}, [], []
        for r in rows:
            a = ok = 0
            for line in lines[r]:
                res = wst.decode_line(line, sampler, ch.taps, ch.offset)
                if res is None:
                    continue
                pkt, err, y, b = res
                a += 1
                p = idl.parse(pkt) if err == 0 else None
                if p is not None and p.crc_ok:
                    ok += 1
                    samples.append(y)
                    bits.append(b)
            stats[r] = (a, ok)
        return stats, samples, bits

    stats, samples, bits = run([r for r in lines if lines[r]], ch)
    rows = [r for r, (a, ok) in stats.items() if ok >= 3 and ok >= 0.2 * a]
    for _ in range(2):
        if len(samples) < 20:
            break
        ch = Channel(*wst.fit_channel(samples, bits))
        s2, samples, bits = run(rows, ch)
        stats.update(s2)
    return rows, ch, stats


_G = {}


def _init(path, fmt, rows, taps, offset):
    _G["frames"] = open_vbi(path, fmt)
    _G["rows"] = rows
    _G["ch"] = Channel(np.asarray(taps), offset)
    _G["sampler"] = wst.LineSampler(fmt.sample_rate)


def _work(rng):
    a, b = rng
    out = []
    frames = _G["frames"]
    for f in range(a, b):
        data = np.asarray(frames[f])
        for r, pkt, err, y, bits in decode_rows(data, _G["rows"], _G["sampler"], _G["ch"]):
            out.append((f, r, pkt, err))
    return out


def decode_file(path, rows, ch, fmt=BTTV_PAL, start=0, stop=None, workers=None,
                chunk=200, progress=None):
    """Decodes all lines in rows. Returns list of (frame, row, 45-byte packet, addr errors)."""
    n = len(open_vbi(path, fmt))
    stop = n if stop is None else min(stop, n)
    ranges = [(a, min(a + chunk, stop)) for a in range(start, stop, chunk)]
    out = []
    with mp.Pool(workers or mp.cpu_count(), _init, (path, fmt, list(rows), ch.taps, ch.offset)) as pool:
        for i, res in enumerate(pool.imap(_work, ranges)):
            out += res
            if progress:
                progress(ranges[i][1] - start, stop - start)
    return out
