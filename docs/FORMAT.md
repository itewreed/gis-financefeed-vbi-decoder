# The GIS financial data feed on ProSieben

This document describes the data service found in the VBI of ProSieben
recordings from 1992 to 1994, layer by layer. Everything here was derived
from recordings; nothing is based on documentation of the service itself.
Facts that are inferred rather than verified are marked as such.

## 1. Capture format

The recordings were captured with a bttv (Bt848/Bt878) card:

| property | value |
| --- | --- |
| samples per line | 2048, unsigned 8 bit |
| sample rate | 35.468950 MHz (8 × PAL colour subcarrier) |
| lines per field | 16, TV lines 7–22 and 320–335 |
| lines per frame | 32, first field first |

Row numbers in this document and in the decoder count from 0 within a frame
(rows 0–15 first field, 16–31 second field). Row 0 is TV line 7; this is
consistent with VPS appearing in row 9 (TV line 16) of the first field only.

Lines in use on ProSieben in these recordings:

| rows | TV lines | content |
| --- | --- | --- |
| 4, 5, 6 / 20, 21, 22 | 11–13 / 324–326 | Videodat (about 2 Mbit/s NRZ) |
| 7, 8, 13, 14 / 23, 24, 29, 30 | 14, 15, 20, 21 / 327, 328, 333, 334 | **GIS feed** (this document) |
| 9 | 16 | VPS |

The feed lines are only active in bursts: all four lines of a field send
together for about 10 frames, then stay black for a similar time.

## 2. Signal

The lines carry standard WST teletext: 6.9375 Mbit/s NRZ, clock run-in
`55 55`, framing code `27`, then 42 bytes, LSB first. The bit rate was
measured from the recording: zero crossings fall on a grid of
5.111 samples = 6.94 Mbit/s.

After VHS LP recording the luminance bandwidth is about 3 MHz, so the clock
run-in (a 3.47 MHz square wave) is almost invisible and single bits are
smeared over about three bit periods. The measured channel, in signal units
per bit at offsets −3 … +3 bits, is close to

    [0, 0, 24, 53, 23, −4, −1]

i.e. roughly a 1 + 2D + D² partial-response channel.

How the decoder recovers the bits:

1. **Normalise and filter.** Black level and peak are measured per line and
   the line is low-pass filtered at 3.5 MHz. Everything above that is noise;
   on a noisy recording the filter alone raised the share of good packets
   from 32 % to 57 %.
2. **Find the packet.** The start is located by correlating the line with
   the expected waveform of the run-in, framing code and packet address
   (`55 55 27 9B EA`). The bit clock phase is taken from the zero crossings
   of the whole line.
3. **Detect.** One sample per bit is fed to a 256-state Viterbi detector
   (channel memory ±4 bits) that is constrained to start with the known 40
   bits. Neighbouring alignments (±1 bit) are tried if the CRC fails.
4. **Estimate the channel per recording.** The first estimate comes from the
   known 64-bit packet header (`55 55 27 9B EA A1 02 2F`) on a sample of
   lines; it is refined twice from the packets that pass the CRC.
5. **Repair.** Packets that still fail the CRC are repaired when the CRC
   syndrome points to exactly one short error burst (1–5 bits, the typical
   error events of the detector). Measured against the copy in the other
   field, about 1 % of these repairs are wrong; repaired packets are only
   used where the other copy is missing.
6. **Merge the two copies** (see section 3).

| recording | lines | CRC ok, detected | after repair |
| --- | --- | --- | --- |
| January 1994, good signal | 18 194 | 98.3 % | 99.6 % |
| December 1993, noisy | 21 104 | 65 % | 86 % |

## 3. Teletext packets: Independent Data Lines

All packets of the feed have the same address:

| byte | value | meaning |
| --- | --- | --- |
| 3 | Hamming 8/4 → 11 | data channel 11 (EN 300 708 table 16: IDL Format A or B) |
| 4 | Hamming 8/4 → 15 | designation code 15 (packet 31) |

In the magazine/row view of normal teletext this is "magazine 3, packet 31".

The packets follow **IDL Format A** (EN 300 708 clause 6.5):

| byte | field | value in this feed |
| --- | --- | --- |
| 5 | FT, format type (Hamming 8/4) | 12: Format A, no repeat indicator, explicit continuity indicator, data length byte present |
| 6 | IAL (Hamming 8/4) | 1: service packet address of one nibble |
| 7 | SPA (Hamming 8/4) | 7 |
| 8 | CI, continuity indicator | 8-bit counter, +1 per packet |
| 9 | DL, data length | bits 0–5: number of user data bytes (33 for a full packet) |
| 10–42 | user data | |
| 43–44 | CRC | |

**CRC.** The polynomial is x¹⁶ + x⁹ + x⁷ + x⁴ + 1. The register starts at
zero; the bits are fed in transmission order (LSB of each byte first)
starting with the CI byte and ending with the two CRC bytes; a valid packet
leaves the register at zero. For full packets the CRC covers bytes 8–44.
For packets with DL < 33 the CRC covers CI, DL and only the DL user data
bytes, followed by the CRC bytes at 43–44; the filler bytes (`80`) are not
included. This differs from the current version of EN 300 708, which
includes the filler.

**Two copies.** The second field carries the same feed again as a separate
stream with its own continuity counter. The user data of the two copies is
identical except for the first byte (see below), which is XORed with `C5` in
the second-field copy. The two copies are sent at almost the same time
(within one frame), and their continuity indicators differ by a constant
(e.g. 24 or 26). The decoder finds that offset from packets with identical
data and fills every gap in one copy from the other copy.

## 4. Byte stream

The first user data byte of each packet is a block flag (`15` for most
packets, `02` and `49` at some message boundaries in the first-field copy).
Its exact meaning is not known; the decoder ignores it. The remaining user
data bytes of consecutive packets (by continuity indicator) form one
continuous byte stream.

## 5. Messages

The byte stream carries messages, each preceded by two bytes `32 32` whose
meaning is not known:

```
10 02  07  SQ  NN 00  T1 T2  01 06  LL LH  KD  FR  00   payload ...   00 10 03
DLE STX    seq        type          length kind frag     (LL|LH<<8)   end
```

When there is nothing to send, the feed carries short idle messages
instead: `10 02 07 SQ NN 00 00 00 00 00 10 03`. Recordings in which the
feed lines are active but no data was being sent contain only these.

| field | meaning |
| --- | --- |
| `10 02` | DLE STX, start of message |
| `SQ` | sequence number, +1 per message, modulo 256 |
| `NN` | 05 or 06 in data messages, other small values in idle messages; meaning unknown |
| `T1 T2` | record type: `10 10` Reuters data, `10 20` GIS screen, `50 30` packed record |
| `LL LH` | payload length, little endian |
| `KD` | 1 or 2; meaning unknown (possibly a priority) |
| `FR` | fragment: high nibble = fragment number, low nibble = 0 single, 2 first, 3 middle, 1 last |
| `00 10 03` | end of message (`10 03` = DLE ETX) |

Payloads longer than 494 bytes are split into fragments with consecutive
sequence numbers and are joined to one record by the decoder. The payload
length makes byte stuffing unnecessary, and none is used: `10` can occur
freely inside the payload.

## 6. Records

### 6.1 Reuters data (`10 10`)

The payload starts with a two-letter tag:

* `PR` *b* *text* – a full Reuters Monitor page. *text* starts with an
  8-character header, the 4-letter page code and the time of the last update
  as HHMM (GMT), e.g. `IDXA1936`. The page itself follows: 64 columns ×
  14 rows. Row 0 has only 60 columns of text; the last four columns of row 0
  show the time from the header. The byte *b* after `PR` is 1, 2 or 3
  (meaning unknown).
* `UP` `00` *code* { `10` *row* *col* *text* } – an update of an existing
  page. *code* is the page code with the new time (for news pages only the
  4-letter code). Each `10` starts an edit: *row* and *col* are given as
  value + `20`, followed by the new text, which overwrites the page from that
  position.
* `HL` *text* – a news headline.
* other tags (e.g. `W4`) occur rarely and are not decoded.

In all page text, `1A` *n* stands for *n* − `1F` spaces; all other
characters are ASCII.

Page codes seen in the January 1994 recording include FXFX, FXFY, FXGF
(spot FX), IDXA, INUE, WALL, SCWI (US indices), LQAQ (UK indices), LFEA
(LIFFE Euromark futures), EDLR (Eurodollar deposits), MMKU, GLDA, GBGB,
WXWX, SPIE, DGCC, DGXX, DGIY, DGFX (DG Bank), ABOL, ABOM, ABOP (ABN AMRO,
Bunds), WLBU (WestLB warrants), TUBE, TUBF, TUBG (Trinkaus & Burkhardt),
MWBB–MWBE (MWB, Munich), BEXF (Banco Exterior), OPPG and MRUC, and the news
pages AASS and AAUU.

### 6.2 GIS screens (`10 20`)

`X5nn` `01` followed by a 78 × 22 character screen in code page 437 (box
drawing characters such as `C4` = `─`). The screens seen so far belong to
an address list of the banks in the *GENO-KIS* pilot test.

### 6.3 Packed records (`50 30`)

The largest part of the feed. Each record starts with a header such as
`7x AF 58 E6/E7 82 xA ...`. The content is nibble oriented: read as a
stream of 4-bit units (low nibble of each byte first), ASCII text appears at
both even and odd nibble positions. A frequent field is the nibble `E`
followed by seven characters. The readable text shows securities data:
issuer names (e.g. LANDESBANK HESSEN-THUERINGEN, B.A.T. INDUSTRIES PLC,
CATHAY PACIFIC FIN.), currencies (DMK, USD, NLG, DKK, AUD), months,
maturities (e.g. 88/95) and certificate types. The numeric fields and the
exact record layout have not been decoded; the decoder outputs the raw
payload and the readable text fragments.

### 6.4 Control block (`50 10`)

A 100-byte binary record without any text, found in the recordings from
July 1992, April 1992 and April 1993, but not in those from December 1993
and January 1994. It is sent about every 205 seconds (median 5120 frames
between copies) and has exactly the same content in every recording, over
a period of a year. The few differing copies differ by one to three bits,
which are bit errors left over from the CRC repair.

```
00  92 61 00 00 01 00 1c 84 98 00 00 00 00 00 08 00 07 99 00 00
20  04 00 00 00 01 00 00 00 00 00 00 d0 04 00 00 00 00 00 00 00
40  00 00 00 00 00 00 00 e0 01 00 af 00 00 01 00 00 00 00 95 00
60  00 00 00 00 80 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
80  00 99 00 00 9a 00 00 00 00 00 00 00 00 00 5f 00 00 00 00 00
```

Mostly zero bytes with a few scattered values, it looks like a static
configuration or parameter table of the receiving system (for example a
service or channel table) that new receivers pick up after switching on.
It contains no dates, counters or text, so its fields cannot be matched to
anything else in the feed, and their meaning is not known.

Other record types (`00 11`, `20 20`, ...) occur only a few times per
recording and are most likely damaged records whose errors passed the CRC.

## 7. Identification

Text found in the feed:

* "Genossenschaftlicher Informations Service GIS GmbH" on the GIS screens,
  with "ADRESSEN DER GENO-KIS-PILOTTESTER-BANKEN"
* Reuters page headers and dealing codes of DG Bank, ABN AMRO, WestLB,
  Trinkaus & Burkhardt and others
* "DOW JONES AS OF 14:35 6-JAN-94" and "RATES AT CLOSE THUR" in the
  recording dated January 1994 (6 January 1994 was a Thursday)

It is inferred from this that the feed was GIS's service distributing market
data to cooperative banks over ProSieben's VBI.
