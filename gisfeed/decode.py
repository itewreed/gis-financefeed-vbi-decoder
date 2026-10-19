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
    shifts: tuple = wst.DEFAULT_SHIFTS


def crc_ok(pkt):
    p = idl.parse(pkt)
    return p is not None and p.crc_ok


def decode_rows(frame_data, rows, sampler, ch):
    """Decodes the given rows of one frame. Yields (row, pkt, err, y, bits)."""
    for r in rows:
        line = frame_data[r]
        if line.std() < ACTIVE_STD:
            continue
        res = wst.decode_line(line, sampler, ch.taps, ch.offset, ch.shifts, crc_ok)
        if res is not None:
            yield (r,) + tuple(res[:4])


def survey(frames, fmt, lines_per_row=60, scan_frames=8000):
    """Finds rows that carry the feed and estimates the channel model.

    1. collects up to lines_per_row active lines of every row, spread over
       the recording;
    2. fits a channel to the known packet header of each row; rows whose
       lines fit well are candidates;
    3. decodes the candidates with that channel and keeps rows where packets
       pass the CRC;
    4. refits the channel from the packets that passed (twice).

    Returns (rows, channel, {row: (lines decoded, CRC ok)})."""
    sampler = wst.LineSampler(fmt.sample_rate)
    n = len(frames)
    picks = np.unique(np.linspace(0, n - 1, min(n, scan_frames)).astype(int))
    pool = {r: [] for r in range(fmt.lines_per_frame)}
    for f in picks:
        data = np.asarray(frames[f])
        act = data.astype(np.float32).std(1) > ACTIVE_STD
        for r in np.nonzero(act)[0]:
            pool[r].append(data[r])
    lines = {}
    for r, v in pool.items():
        if len(v) >= 5:
            idx = np.unique(np.linspace(0, len(v) - 1, min(len(v), lines_per_row)).astype(int))
            lines[r] = [v[i] for i in idx]

    prepared = {}
    fits = {}
    for r, v in lines.items():
        ys = [sampler.sample(l, 0.0) for l in v]
        ys = [y for y in ys if y is not None]
        if len(ys) < 5:
            continue
        prepared[r] = ys
        taps, off, res = wst.fit_header(ys)
        fits[r] = (taps, off, res)
    # rows whose lines fit the header well give the first channel estimate;
    # a looser test selects the rows that are tried with it
    cand = [r for r, (t, o, res) in fits.items() if t.max() > 25 and res < 0.4 * t.max()]
    loose = [r for r, (t, o, res) in fits.items() if t.max() > 15 and res < 0.7 * t.max()]
    stats = {r: (len(lines.get(r, [])), 0) for r in lines}
    if not cand:
        return [], Channel(wst.DEFAULT_TAPS, wst.DEFAULT_OFFSET), stats
    taps, off, _ = wst.fit_header([y for r in cand for y in prepared[r]])
    ch = Channel(taps, off)
    cand = sorted(set(cand) | set(loose))

    def run(rows, ch):
        st, samples, bits = {}, [], []
        for r in rows:
            a = ok = 0
            for line in lines[r]:
                res = wst.decode_line(line, sampler, ch.taps, ch.offset, ch.shifts, crc_ok)
                if res is None:
                    continue
                pkt, err, y, b, sh = res
                a += 1
                p = idl.parse(pkt) if err == 0 else None
                if p is not None and p.crc_ok:
                    ok += 1
                    samples.append(y)
                    bits.append(b)
            st[r] = (a, ok)
        return st, samples, bits

    st, samples, bits = run(cand, ch)
    stats.update(st)
    rows = [r for r in cand if st[r][1] >= 3 and st[r][1] >= 0.05 * st[r][0]]
    for _ in range(2):
        if len(samples) < 20:
            break
        ch = Channel(*wst.fit_channel(samples, bits))
        st, samples, bits = run(rows, ch)
        stats.update(st)
    return sorted(rows), ch, stats


_G = {}


def _init(path, fmt, rows, taps, offset, shifts):
    _G["frames"] = open_vbi(path, fmt)
    _G["rows"] = rows
    _G["ch"] = Channel(np.asarray(taps), offset, shifts)
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
    with mp.Pool(workers or mp.cpu_count(), _init, (path, fmt, list(rows), ch.taps, ch.offset, ch.shifts)) as pool:
        for i, res in enumerate(pool.imap(_work, ranges)):
            out += res
            if progress:
                progress(ranges[i][1] - start, stop - start)
    return out
