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
# clock run-in 0x55 0x55 and framing code 0x27, in transmission order (LSB first)
KNOWN_PREFIX = [1, 0] * 8 + [1, 1, 1, 0, 0, 1, 0, 0]

# Channel taps for bits k-3 .. k+3, in units of "normalised" signal (black = 0,
# peak white of the data line = 100). Refined per recording by fit_channel().
DEFAULT_TAPS = np.array([0.0, 0.5, 23.8, 52.9, 23.4, -3.8, -1.0])
DEFAULT_OFFSET = -4.0


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


class LineSampler:
    """Normalises a line and samples it at the bit centres."""

    def __init__(self, sample_rate):
        self.spb = sample_rate / BIT_RATE

    def normalise(self, line):
        line = np.asarray(line, dtype=float)
        black = np.median(line[:100])
        top = np.percentile(line, 99)
        if top - black < 20:
            return None
        return (line - black) * (100.0 / (top - black))

    def bit_phase(self, x, start):
        """Position of the bit boundaries modulo one bit period."""
        t = []
        for thr in (25.0, 75.0):
            above = x > thr
            idx = np.nonzero(above[1:] != above[:-1])[0]
            idx = idx[(idx > start + 60) & (idx < start + 1830)]
            if len(idx):
                t.append(idx + (thr - x[idx]) / (x[idx + 1] - x[idx]))
        if not t:
            return None
        t = np.concatenate(t)
        z = np.exp(2j * np.pi * t / self.spb).sum()
        return (np.angle(z) / (2 * np.pi)) % 1 * self.spb

    def sample(self, line, shift=1.5):
        """Returns per-bit samples of a line, or None if it carries no data."""
        x = self.normalise(line)
        if x is None:
            return None
        above = np.nonzero(x > 25)[0]
        if not len(above):
            return None
        start = above[0]
        b0 = self.bit_phase(x, start)
        if b0 is None:
            return None
        k0 = np.ceil((start - 25 - b0) / self.spb)
        first = b0 + k0 * self.spb + self.spb / 2
        t = first + (np.arange(PACKET_BITS) + shift) * self.spb
        return np.interp(t, np.arange(len(x)), x)


def viterbi(y, taps, offset, prefix=KNOWN_PREFIX):
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
    out = []
    for t in range(n + M - 1, -1, -1):
        out.append((s >> (L - 2)) & 1)
        s = back[t][s]
    return np.array(out[::-1][:n])


def design_matrix(bits, M):
    padded = np.concatenate([np.zeros(M, int), bits, np.zeros(M, int)])
    X = np.lib.stride_tricks.sliding_window_view(padded, 2 * M + 1)[: len(bits)]
    return np.concatenate([X, np.ones((len(bits), 1))], 1)


def fit_channel(samples, bits, M=3):
    """Least-squares channel taps and offset from lines with known bits."""
    X = np.concatenate([design_matrix(b, M) for b in bits])
    Y = np.concatenate(samples)
    sol = np.linalg.lstsq(X, Y, rcond=None)[0]
    return sol[:-1], sol[-1]


def mrag_errors(packet):
    return HAM84[packet[3]][1] + HAM84[packet[4]][1]


def decode_line(line, sampler, taps, offset):
    """Best packet (45 bytes) from one line, trying neighbouring bit alignments.

    Returns (packet bytes, Hamming errors in the address, per-bit samples, bits)
    or None if the line carries no signal."""
    best = None
    for shift in (1.5, 0.5, 2.5, 3.5):
        y = sampler.sample(line, shift)
        if y is None:
            return None
        bits = viterbi(y, taps, offset)
        pkt = bits_to_bytes(bits)
        err = mrag_errors(pkt)
        if best is None or err < best[1]:
            best = (pkt, err, y, bits)
        if err == 0:
            break
    return best
