"""Raw VBI capture files (bttv / Bt848 / Bt878 style).

A bttv PAL capture stores, per frame, 16 lines of the first field followed by
16 lines of the second field. Each line is 2048 unsigned 8-bit samples taken at
8 x fsc (35.468950 MHz). Row 0 of each field is TV line 7 (320 in field 2).
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class VbiFormat:
    samples_per_line: int = 2048
    lines_per_field: int = 16
    sample_rate: float = 35.468950e6
    first_line: int = 7

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


def open_vbi(path, fmt=BTTV_PAL):
    """Memory-map a capture as an array [frame, row, sample]."""
    raw = np.memmap(path, np.uint8, "r")
    n = raw.size // fmt.frame_bytes
    return raw[: n * fmt.frame_bytes].reshape(n, fmt.lines_per_frame, fmt.samples_per_line)
