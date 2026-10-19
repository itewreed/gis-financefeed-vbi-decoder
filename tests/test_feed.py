"""Unit tests on packets taken from a real recording (January 1994)."""
import unittest

import numpy as np

from gisfeed import feed, idl, pages, wst

FULL = bytes.fromhex(
    "5555279beaa1022f80211512030974af58e7825a9b0302a4461c10400000564444146be04b1006be0461e0c3ea")
SHORT = bytes.fromhex(
    "5555279beaa1022fa4038c1003808080808080808080808080808080808080808080808080808080808080bb97")


class TestIdl(unittest.TestCase):
    def test_full_packet(self):
        p = idl.parse(FULL)
        self.assertEqual((p.channel, p.spa, p.ci), (11, 7, 0x80))
        self.assertEqual(len(p.data), 33)
        self.assertTrue(p.crc_ok)

    def test_short_packet_crc_excludes_filler(self):
        p = idl.parse(SHORT)
        self.assertEqual(p.data, bytes.fromhex("8c1003"))
        self.assertTrue(p.crc_ok)

    def test_correction(self):
        for bits in ((100,), (200, 201), (150, 151, 152)):
            bad = bytearray(FULL)
            for b in bits:
                bad[b // 8] ^= 1 << (b % 8)
            self.assertFalse(idl.parse(bytes(bad)).crc_ok)
            q = idl.correct(bytes(bad))
            self.assertIsNotNone(q)
            self.assertEqual(q.raw, FULL)
            self.assertEqual(q.corrected, len(bits))


class TestWst(unittest.TestCase):
    def test_hamming(self):
        self.assertEqual(wst.HAM84[0x9B], (11, 0))
        self.assertEqual(wst.HAM84[0xEA], (15, 0))
        self.assertEqual(wst.HAM84[0x9B ^ 0x04][0], 11)

    def test_viterbi_with_noise(self):
        rng = np.random.default_rng(1)
        bits = np.concatenate([wst.KNOWN_PREFIX, rng.integers(0, 2, wst.PACKET_BITS - 40)])
        y = wst.design_matrix(bits, 4) @ np.append(wst.DEFAULT_TAPS, wst.DEFAULT_OFFSET)
        y = y + rng.normal(0, 3, len(y))
        out = wst.viterbi(y, wst.DEFAULT_TAPS, wst.DEFAULT_OFFSET)
        self.assertTrue(np.array_equal(out, bits))


class TestFeed(unittest.TestCase):
    def test_message_framing(self):
        payload = b"UP\x00IDXA1937\x10%04.73"
        msg = (b"\x32\x32\x10\x02\x07\x91\x05\x00\x10\x10\x01\x06"
               + bytes([len(payload), 0, 2, 0x00, 0]) + payload + b"\x00\x10\x03")
        # split into packets of 32 bytes after a block flag byte
        packets = [(0, ci, b"\x15" + msg[i:i + 32]) for ci, i in enumerate(range(0, len(msg), 32))]
        recs = feed.chain(feed.stream_messages(packets))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].payload, payload)

    def test_page_update(self):
        store = pages.PageStore()
        page = store.page("IDXA")
        page.put(5, 0, "DJ INDS      3805.01      +6.19")
        rec = feed.Record(0, 0x1010, 2, 0, b"UP\x00IDXA1937\x10%04.73\x10%;5.90")
        store.apply(rec)
        self.assertEqual("".join(page.rows[5][:31]), "DJ INDS      3804.73      +5.90")
        self.assertEqual("".join(page.rows[0][60:]), "1937")


if __name__ == "__main__":
    unittest.main()
