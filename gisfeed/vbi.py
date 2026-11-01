"""Capture files.

Two kinds of input are supported; both are presented as frames of 32 rows,
16 per field, for TV lines 7-22 of the first field and 320-335 of the
second field.

bttv raw VBI (.vbi): per frame 16 lines of the first field followed by 16
lines of the second field, each 2048 unsigned 8-bit samples taken at
8 x fsc (35.468950 MHz). Row 0 of each field is TV line 7.

vhs-decode / ld-decode time base corrected luma (.tbcy, also .tbc): one
field after the other, fieldHeight lines of fieldWidth unsigned 16-bit
samples at 4 x fsc (17.734475 MHz for PAL), described by the .tbc.json file
next to it. Line k (counting from 0) of a field is TV line k + 1. The
samples are scaled to 8 bits, and the first 150 samples (sync and colour
burst) are dropped, so that a line starts in the back porch.
"""
import json
import os
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class VbiFormat:
    samples_per_line: int = 2048
    lines_per_field: int = 16
    sample_rate: float = 35.468950e6
    first_line: int = 7
    black_window: tuple = (0, 100)  # samples that are at black level in data lines
    name: str = "bttv PAL 2048 x u8"

    @property
    def lines_per_frame(self):
        return 2 * self.lines_per_field

    @property
    def frame_bytes(self):
        return self.samples_per_line * self.lines_per_frame

    def tv_line(self, row):
        """TV line number (1-625) of a row index within a frame."""
        field, r = divmod(row, self.lines_per_field)
        return self.first_line + r + (313 if field else 0)


BTTV_PAL = VbiFormat()
TBC_SKIP = 150


def open_vbi(path, fmt=BTTV_PAL):
    """Memory-map a bttv capture as an array [frame, row, sample]."""
    raw = np.memmap(path, np.uint8, "r")
    n = raw.size // fmt.frame_bytes
    return raw[: n * fmt.frame_bytes].reshape(n, fmt.lines_per_frame, fmt.samples_per_line)


def tbc_json_path(path):
    base, ext = os.path.splitext(path)
    for cand in (base + ".tbc.json", path + ".json", base + ".json"):
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError(f"no .tbc.json found next to {path}")


class TbcFrames:
    """Frames of a .tbcy/.tbc file, indexable like the bttv array."""

    def __init__(self, path, fmt=None):
        meta = json.load(open(tbc_json_path(path)))
        vp = meta["videoParameters"]
        self.width, self.height = vp["fieldWidth"], vp["fieldHeight"]
        self.fields = np.memmap(path, np.uint16, "r")
        nfields = self.fields.size // (self.width * self.height)
        self.fields = self.fields[: nfields * self.width * self.height].reshape(
            nfields, self.height, self.width)
        # pair fields into frames, starting at the first "first field"
        flags = [f.get("isFirstField", i % 2 == 0) for i, f in enumerate(meta.get("fields", []))]
        self.start = flags.index(True) if True in flags else 0
        self.n = (nfields - self.start) // 2
        self.fmt = fmt or VbiFormat(samples_per_line=self.width - TBC_SKIP, sample_rate=vp["sampleRate"],
                                    black_window=(0, 25), name=f"TBC {vp.get('system', '')} "
                                    f"{self.width} x u16, {vp['sampleRate'] / 1e6:.3f} MHz")
        lpf = self.fmt.lines_per_field
        self.lines = np.arange(self.fmt.first_line - 1, self.fmt.first_line - 1 + lpf)

    def __len__(self):
        return self.n

    def __getitem__(self, f):
        if f < 0:
            f += self.n
        if not 0 <= f < self.n:
            raise IndexError(f)
        a = self.start + 2 * f
        both = self.fields[a:a + 2][:, self.lines, TBC_SKIP:]
        return (both.reshape(-1, both.shape[-1]).astype(np.float32) / 256.0)


def open_capture(path):
    """Opens a capture; returns (frames, format)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".tbcy", ".tbc"):
        t = TbcFrames(path)
        return t, t.fmt
    return open_vbi(path), BTTV_PAL
