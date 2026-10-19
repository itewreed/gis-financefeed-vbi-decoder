# gis-financefeed-vbi-decoder

Decoder for a financial data broadcast that was hidden in the vertical
blanking interval (VBI) of the German TV channel **ProSieben** in the early
1990s, recovered from home VHS recordings.

Besides the well-known *Videodat* software broadcast, ProSieben carried a
second data service on VBI lines 14, 15, 20 and 21 (and 327, 328, 333, 334).
It looks like teletext, but it contains no teletext pages. It is a teletext
**Independent Data Line** (IDL, EN 300 708 Format A) on data channel 11 that
carries a datacast of the **GIS – Genossenschaftlicher Informations Service
GmbH**, the information service of the German cooperative banking group
(DG Bank, Volksbanken and Raiffeisenbanken). The feed contains:

* **Reuters Monitor pages** (64 × 14 characters) as full pages and as live
  updates: spot FX rates (FXFX), world indices (IDXA), Dow Jones indices
  (INUE), LIFFE futures, Eurodollar deposits, Bund prices, warrant quotes from
  German banks and brokers, ...
* **news headlines** (Reuters and Dow Jones)
* **GIS screens** (78 × 22, code page 437), e.g. the address list of the
  banks taking part in the *GENO-KIS* pilot test
* **packed securities records** (bonds, certificates, currencies) in a
  nibble-oriented format that is only partly understood

Example: Reuters page IDXA, reconstructed from a recording made on
6 January 1994, 19:39 GMT, with the live updates applied:

```
+----------------------------------------------------------------+
| WORLD INDICES - US                  DELAYED 30 MINUTES IDXA1939|
|  INDEX       LATEST   NET.CHNG     PR.CLS    YR.HIGH  CLS.PR.YR|
|AMEX STK      478.02      -0.77     478.79     478.82     477.15|
|COM RE BUR    227.74      +0.43     227.31     227.51     226.31|
|DJ COMP      1393.01      +3.36    1389.65    1398.47    1381.03|
|DJ INDS      3803.60      +4.78    3798.82    3821.33    3754.09|
|DJ TRANS     1795.34     +16.51    1778.83    1788.70    1762.32|
|DJ UTILS      221.79      -2.24     224.03     229.77     229.30|
...
```

## Why a special decoder is needed

Teletext is sent at 6.9375 Mbit/s, but a VHS recording in LP mode keeps only
about 3 MHz of luminance bandwidth. The recorded signal is so smeared that a
normal teletext slicer finds almost nothing. This decoder treats each line as
a partial-response channel instead:

1. the line is low-pass filtered and the packet start is found by
   correlation with the known run-in, framing code and address,
2. the bit clock phase is recovered from the zero crossings and the line is
   sampled once per bit,
3. a Viterbi (maximum-likelihood sequence) detector finds the bit sequence,
   using a channel model that is fitted to each recording,
4. short bit error bursts are repaired through the CRC, and packets lost in
   one field are taken from the copy of the feed in the other field.

On a good recording 99.6 % of all data lines yield a valid packet; on a
noisy one 86 %.

## Requirements

* Python 3.9 or newer
* numpy

```
pip install .
```

or run it in place with `python3 -m gisfeed`.

## Input

Raw VBI captures as written by Linux `bttv` style capture cards
(Bt848/Bt878): per frame 32 lines (16 per field) of 2048 unsigned 8-bit
samples at 35.468950 MHz, first field first, starting at TV line 7, as read
from `/dev/vbi` of a bttv card. The lines carrying the feed are found
automatically.

## Usage

```
python3 -m gisfeed decode capture.vbi -o outdir
```

Options:

| option | meaning |
| --- | --- |
| `--rows 7,8,13,14` | decode only these rows (default: detect) |
| `--start N`, `--stop N` | frame range |
| `--workers N` | number of processes (default: all CPUs) |
| `--no-correction` | do not repair packets through the CRC |

Output in `outdir`:

| file | content |
| --- | --- |
| `summary.txt` | detected lines, channel model, packet and record counts |
| `records.txt` | every decoded record in time order, rendered as text |
| `records.jsonl` | the same, one JSON object per record, with the raw payload |
| `pages/XXXX.txt` | final state of every Reuters page (`pages/GIS_*.txt` for GIS screens) |
| `packed/*.bin` | raw payload of every packed record |
| `packets.t42` | all decoded lines as 42-byte teletext packets (t42 format) |

Times in the output are positions in the recording (frame / 25).

## Tests

```
python3 -m unittest discover -s tests
```

## How the feed is built

See [docs/FORMAT.md](docs/FORMAT.md) for the full description of every
layer: the signal, the IDL packets, the byte stream, the message framing and
the record formats.

## Status

* Signal recovery, IDL packets, CRC, message framing, fragment reassembly:
  done.
* Reuters pages and updates, headlines, GIS screens: done.
* Packed records (type `50 30`): text fields are extracted, the numeric
  fields and the record layout are not decoded yet. Contributions welcome.

## Recordings

The decoder was developed on ProSieben recordings from 1992 to 1994 made on
consumer VHS recorders in LP mode. If you have other recordings of ProSieben
(or other channels) from that time with data in the VBI, the decoder may
work on them too.
