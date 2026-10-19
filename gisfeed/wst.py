"""Recovery of WST teletext packets from bandwidth-limited (VHS) recordings.

Teletext runs at 6.9375 Mbit/s, far above the ~3 MHz luminance bandwidth of a
VHS LP recording. A plain slicer fails, so each line is treated as a
partial-response channel: the bit clock is recovered from the zero crossings,
the line is sampled once per bit, and the bits are found with a Viterbi
(maximum-likelihood sequence) detector against an estimated channel response.
"""
import numpy as np

BIT_RATE = 6.9375e6
PACKET_BITS = 360  # 45 bytes including clock run-in and framing code
def byte_bits(data):
    """Bits of data in transmission order (LSB first)."""
    return [(b >> i) & 1 for b in data for i in range(8)]


# clock run-in 0x55 0x55, framing code 0x27 and the address of the GIS feed
# (data channel 11, designation code 15 -> Hamming bytes 9B EA). The Viterbi
# detector is constrained to start with these bits.
KNOWN_PREFIX = byte_bits(b"\x55\x55\x27\x9b\xea")
# The packets of the feed also share format type, IAL and service packet
# address; used only to estimate the channel of a new recording.
FEED_HEADER = byte_bits(b"\x55\x55\x27\x9b\xea\xa1\x02\x2f")

# Channel taps for bits k-4 .. k+4, in units of "normalised" signal (black = 0,
# peak white of the data line = 100). Refined per recording by fit_channel().
DEFAULT_TAPS = np.array([0.0, 4.2, 0.5, 23.4, 52.2, 23.4, -3.5, -1.2, 0.0])
DEFAULT_OFFSET = -5.0


def _ham84_table():
    table = []
    for b in range(256):
        p = [(b >> i) & 1 for i in range(8)]
        a = p[0] ^ p[1] ^ p[5] ^ p[7]
        bb = p[1] ^ p[2] ^ p[3] ^ p[7]
        c = p[1] ^ p[3] ^ p[4] ^ p[5]
        d = sum(p) & 1
        if a and bb and c and d:
            table.append((p[1] | p[3] << 1 | p[5] << 2 | p[7] << 3, 0))
            continue
        # single bit errors are correctable, everything else is not
        fixed = None
        if d == 0:
            for i in range(8):
                q = b ^ (1 << i)
                pq = [(q >> j) & 1 for j in range(8)]
                if (pq[0] ^ pq[1] ^ pq[5] ^ pq[7]) and (pq[1] ^ pq[2] ^ pq[3] ^ pq[7]) and \
                        (pq[1] ^ pq[3] ^ pq[4] ^ pq[5]) and (sum(pq) & 1):
                    fixed = pq[1] | pq[3] << 1 | pq[5] << 2 | pq[7] << 3
                    break
        table.append((fixed, 1) if fixed is not None else (-1, 2))
    return table


HAM84 = _ham84_table()


def bits_to_bytes(bits):
    bits = np.asarray(bits[: len(bits) // 8 * 8]).reshape(-1, 8)
    return bytes((bits << np.arange(8)).sum(1).astype(np.uint8))


# Run-in, framing code and the packet address of the GIS feed (data channel
# 11, designation code 15), used to locate the packet start in a line.
SYNC_BYTES = bytes([0x55, 0x55, 0x27, 0x9B, 0xEA])


class LineSampler:
    """Normalises a line and samples it at the bit centres."""

    def __init__(self, sample_rate, cutoff=3.5e6):
        self.spb = sample_rate / BIT_RATE
        # The recording holds no signal above ~3.5 MHz, only noise: low-pass
        # filter the line before sampling it.
        n = np.arange(-24, 25)
        lp = np.sinc(2 * cutoff / sample_rate * n) * np.hamming(len(n))
        self.lowpass = lp / lp.sum()
        # template of the start of a packet as seen through a ~3 MHz channel
        bits = np.array([(b >> i) & 1 for b in SYNC_BYTES for i in range(8)], float) - 0.5
        n = int(len(bits) * self.spb) + 1
        t = np.arange(n) / self.spb - 0.5
        wave = bits[np.clip(np.round(t).astype(int), 0, len(bits) - 1)]
        k = np.arange(-8, 9)
        kern = np.exp(-0.5 * (k / (0.8 * self.spb)) ** 2)
        wave = np.convolve(wave, kern / kern.sum(), "same")
        self.template = wave - wave.mean()

    def find_start(self, x):
        """Sample position of the centre of the first run-in bit."""
        # the run-in shows up as a rise to mid level; search around that onset
        sm = np.convolve(x, np.ones(9) / 9, "same")
        above = np.nonzero(sm[:600] > 30)[0]
        if not len(above):
            return None
        onset = above[0]
        a, b = max(0, onset - 40), onset + 40
        seg = x[a: b + len(self.template)]
        if len(seg) < len(self.template):
            return None
        corr = np.correlate(seg - seg.mean(), self.template, "valid")
        return a + int(np.argmax(corr)) + 0.5 * self.spb

    def normalise(self, line):
        line = np.asarray(line, dtype=float)
        black = np.median(line[:100])
        top = np.percentile(line, 99)
        if top - black < 20:
            return None
        x = (line - black) * (100.0 / (top - black))
        return np.convolve(x, self.lowpass, "same")

    def bit_phase(self, x, start):
        """Position of the bit boundaries modulo one bit period."""
        t = []
        for thr in (25.0, 75.0):
            above = x > thr
            idx = np.nonzero(above[1:] != above[:-1])[0]
            idx = idx[(idx > start + 80) & (idx < start + 1830)]
            if len(idx):
                t.append(idx + (thr - x[idx]) / (x[idx + 1] - x[idx]))
        if not t:
            return None
        t = np.concatenate(t)
        z = np.exp(2j * np.pi * t / self.spb).sum()
        return (np.angle(z) / (2 * np.pi)) % 1 * self.spb

    def prepare(self, line):
        """Normalised line and the estimated centre of bit 0, or None."""
        x = self.normalise(line)
        if x is None:
            return None
        start = self.find_start(x)
        if start is None:
            return None
        b0 = self.bit_phase(x, int(start))
        if b0 is not None:
            # snap to the sampling grid recovered from the zero crossings; for
            # this channel the best sampling instants coincide with them
            start = b0 + np.round((start - b0) / self.spb) * self.spb
        return x, start

    def sample(self, line, shift=0.0, prepared=None):
        """Returns per-bit samples of a line, or None if it carries no data."""
        prep = prepared or self.prepare(line)
        if prep is None:
            return None
        x, first = prep
        t = first + (np.arange(PACKET_BITS) + shift) * self.spb
        return np.interp(t, np.arange(len(x)), x)


def viterbi(y, taps, offset, prefix=KNOWN_PREFIX, return_cost=False):
    """Maximum-likelihood bit sequence for samples y under the given channel."""
    taps = np.asarray(taps, dtype=float)
    L = len(taps)
    M = L // 2
    S = 1 << (L - 1)
    n = len(y)
    st = np.arange(S)
    hist = (st[:, None] >> np.arange(L - 1)) & 1
    p0 = np.concatenate([hist, np.zeros((S, 1), int)], 1) @ taps + offset
    p1 = np.concatenate([hist, np.ones((S, 1), int)], 1) @ taps + offset
    ns0 = st >> 1
    ns1 = ns0 | (1 << (L - 2))
    inf = 1e18
    cost = np.full(S, inf)
    cost[0] = 0.0
    back = np.zeros((n + M, S), np.int64)
    for t in range(n + M):
        k = t - M
        if k >= 0:
            c0 = cost + (y[k] - p0) ** 2
            c1 = cost + (y[k] - p1) ** 2
        else:
            c0 = cost.copy()
            c1 = cost.copy()
        if t < len(prefix):
            if prefix[t]:
                c0 += inf
            else:
                c1 += inf
        new = np.full(S, inf * 10)
        np.minimum.at(new, ns0, c0)
        np.minimum.at(new, ns1, c1)
        ptr = np.zeros(S, np.int64)
        m = c0 == new[ns0]
        ptr[ns0[m]] = st[m]
        m = c1 == new[ns1]
        ptr[ns1[m]] = st[m]
        cost = new
        back[t] = ptr
    s = int(np.argmin(cost))
    best = float(cost[s])
    out = []
    for t in range(n + M - 1, -1, -1):
        out.append((s >> (L - 2)) & 1)
        s = back[t][s]
    bits = np.array(out[::-1][:n])
    return (bits, best) if return_cost else bits


def design_matrix(bits, M):
    padded = np.concatenate([np.zeros(M, int), bits, np.zeros(M, int)])
    X = np.lib.stride_tricks.sliding_window_view(padded, 2 * M + 1)[: len(bits)]
    return np.concatenate([X, np.ones((len(bits), 1))], 1)


def fit_channel(samples, bits, M=4):
    """Least-squares channel taps and offset from lines with known bits."""
    X = np.concatenate([design_matrix(b, M) for b in bits])
    Y = np.concatenate(samples)
    sol = np.linalg.lstsq(X, Y, rcond=None)[0]
    return sol[:-1], sol[-1]


def mrag_errors(packet):
    """Hamming errors in the packet address (0 for a clean decode)."""
    return HAM84[packet[3]][1] + HAM84[packet[4]][1]


def fit_header(samples, M=4):
    """Channel estimate from lines assumed to start with FEED_HEADER.

    Returns (taps, offset, rms residual)."""
    n = len(FEED_HEADER)
    bits = np.array(FEED_HEADER + [0] * (PACKET_BITS - n))
    X1 = design_matrix(bits, M)[:n - M]
    X = np.concatenate([X1] * len(samples))
    Y = np.concatenate([y[:n - M] for y in samples])
    sol = np.linalg.lstsq(X, Y, rcond=None)[0]
    res = float(np.sqrt(np.mean((X @ sol - Y) ** 2)))
    return sol[:-1], sol[-1], res


DEFAULT_SHIFTS = (0.0, -1.0, 1.0)


def decode_line(line, sampler, taps, offset, shifts=DEFAULT_SHIFTS, accept=None):
    """Best packet (45 bytes) from one line, trying several bit alignments.

    The packet start is found by correlation with the expected run-in,
    framing code and address; as this can be off by a bit, the alignments in
    shifts (in bits) are tried in order. The first alignment whose packet is
    accepted by accept(packet) wins, otherwise the one with the best fit.

    Returns (packet bytes, Hamming errors in the address, per-bit samples,
    bits, shift) or None if the line carries no signal."""
    prep = sampler.prepare(line)
    if prep is None:
        return None
    best = None
    for shift in shifts:
        y = sampler.sample(line, shift, prep)
        bits, cost = viterbi(y, taps, offset, return_cost=True)
        pkt = bits_to_bytes(bits)
        res = (pkt, mrag_errors(pkt), y, bits, shift)
        if accept is not None and accept(pkt):
            return res
        if best is None or cost < best[0]:
            best = (cost, res)
    return best[1]
