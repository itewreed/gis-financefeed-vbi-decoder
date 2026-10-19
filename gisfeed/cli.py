"""Command line interface: python -m gisfeed decode capture.vbi -o outdir"""
import argparse
import json
import os
import sys
import time

import numpy as np

from . import decode, feed, idl, pages, wst
from .vbi import BTTV_PAL, open_vbi

RECORD_NAMES = {0x1010: "reuters", 0x1020: "gis", 0x5030: "packed"}


def frame_time(frame):
    s = frame / 25.0
    return f"{int(s // 3600):02d}:{int(s // 60 % 60):02d}:{s % 60:05.2f}"


def cmd_decode(args):
    fmt = BTTV_PAL
    frames = open_vbi(args.file, fmt)
    os.makedirs(args.out, exist_ok=True)
    log = open(os.path.join(args.out, "summary.txt"), "w")

    def say(*a):
        msg = " ".join(str(x) for x in a)
        print(msg)
        log.write(msg + "\n")
        log.flush()

    say(f"file: {args.file}")
    say(f"frames: {len(frames)} ({len(frames) / 25:.1f} s), format bttv PAL 2048 x u8, 16 lines per field")
    ch = decode.Channel(wst.DEFAULT_TAPS, wst.DEFAULT_OFFSET)
    if args.rows:
        rows = [int(r) for r in args.rows.split(",")]
        rows, ch, stats = rows, *decode.survey(frames, fmt, ch)[1:]
    else:
        rows, ch, stats = decode.survey(frames, fmt, ch)
    for r in sorted(stats):
        a, ok = stats[r]
        if a:
            say(f"  row {r:2d} (TV line {fmt.tv_line(r)}): {ok}/{a} sample lines are valid IDL packets")
    say(f"rows used: {rows}")
    say(f"channel taps: {np.round(ch.taps, 1).tolist()} offset {ch.offset:.1f}")
    if not rows:
        say("no IDL data found")
        return

    t0 = time.time()

    def progress(done, total):
        if sys.stderr.isatty() or done == total or done % 20000 < 200:
            el = time.time() - t0
            sys.stderr.write(f"\r  {done}/{total} frames, {el:.0f} s")
            sys.stderr.flush()

    raw = decode.decode_file(args.file, rows, ch, fmt, args.start, args.stop, args.workers,
                             progress=progress)
    sys.stderr.write("\n")
    parsed = []
    n_addr = n_crc = 0
    with open(os.path.join(args.out, "packets.t42"), "wb") as t42:
        for f, r, pkt, err in raw:
            t42.write(pkt[3:45])
            p = idl.parse(pkt) if err < 2 else None
            if p is None:
                continue
            n_addr += 1
            if p.crc_ok:
                n_crc += 1
                parsed.append((f, r, p))
    say(f"lines with a signal: {len(raw)}, IDL packets: {n_addr}, CRC ok: {n_crc}")
    streams = feed.group_streams(parsed)
    for sid, pk in streams.items():
        say(f"  stream channel {sid[0]} SPA {sid[1]} field {2 if sid[2] else 1}: {len(pk)} packets")
    recs, st = feed.records_from_streams(streams)
    say(f"records: {len(recs)}")
    from collections import Counter
    say("record types: " + ", ".join(f"{RECORD_NAMES.get(t, hex(t))}={n}"
                                      for t, n in Counter(r.rtype for r in recs).items()))

    store = pages.PageStore()
    os.makedirs(os.path.join(args.out, "packed"), exist_ok=True)
    with open(os.path.join(args.out, "records.txt"), "w", encoding="utf-8") as rt, \
            open(os.path.join(args.out, "records.jsonl"), "w") as rj:
        for i, rec in enumerate(recs):
            desc, text = store.apply(rec)
            ts = frame_time(rec.frame)
            rt.write(f"[{ts} frame {rec.frame}] {desc}\n")
            if text:
                rt.write(text + "\n")
            rt.write("\n")
            rj.write(json.dumps({"frame": rec.frame, "type": RECORD_NAMES.get(rec.rtype, rec.rtype),
                                 "seq": rec.seq, "kind": rec.kind, "fragments": rec.fragments,
                                 "description": desc, "text": text,
                                 "payload_hex": rec.payload.hex()}) + "\n")
            if rec.rtype == 0x5030:
                with open(os.path.join(args.out, "packed", f"{rec.frame:07d}_{rec.seq:02x}.bin"), "wb") as fb:
                    fb.write(rec.payload)
    pdir = os.path.join(args.out, "pages")
    os.makedirs(pdir, exist_ok=True)
    for code, pg in sorted(store.pages.items()):
        with open(os.path.join(pdir, f"{code}.txt"), "w") as fp:
            note = "" if pg.complete else "  (only updates received, page incomplete)"
            fp.write(f"Reuters page {code}, last change at {frame_time(pg.updated)}{note}\n")
            fp.write("+" + "-" * pages.COLS + "+\n")
            for line in pg.text().split("\n"):
                fp.write("|" + line + "|\n")
            fp.write("+" + "-" * pages.COLS + "+\n")
    for sid, text in sorted(store.gis.items()):
        with open(os.path.join(pdir, f"GIS_{sid}.txt"), "w", encoding="utf-8") as fp:
            fp.write(text + "\n")
    say(f"Reuters pages: {len(store.pages)} ({sum(p.complete for p in store.pages.values())} complete), "
        f"GIS screens: {len(store.gis)}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gisfeed", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode", help="decode a raw VBI capture")
    d.add_argument("file")
    d.add_argument("-o", "--out", required=True, help="output directory")
    d.add_argument("--rows", help="comma separated row numbers (default: detect)")
    d.add_argument("--start", type=int, default=0, help="first frame")
    d.add_argument("--stop", type=int, help="frame to stop at")
    d.add_argument("--workers", type=int, help="worker processes (default: all CPUs)")
    args = ap.parse_args(argv)
    if args.cmd == "decode":
        cmd_decode(args)
