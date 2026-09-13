# iec60870-parser

A pure-Python, zero-runtime-dependency parser and validator for IEC 60870-5-101, -103, and -104 telemetry frames. It consumes one raw byte buffer at a time and returns a structured `ParseResult` with `VALID`, `INCOMPLETE`, or `CORRUPTED` status.

## Install

```text
python -m pip install iec60870-parser
```

For a source checkout:

```text
python -m pip install -e .
```

## API

```python
from iec60870 import FrameStatus, T104Session, parse_frame

result = parse_frame(raw_bytes, protocol="t104")
if result.status is FrameStatus.VALID:
    print(result.frame_type, result.fields)
elif result.status is FrameStatus.INCOMPLETE:
    # Retain the buffer and read more bytes.
    pass
else:
    print(result.error)
```

`protocol` accepts `t101`, `t103`, `t104`, or `auto`. Explicit protocol selection is preferred for short/incomplete buffers. T101 uses a one-byte link address by default; T103 uses two bytes. Override either with `link_address_size=1` or `2`.

For a stream connection, `T104Session` checks the receive sequence number of I-format APDUs and advances it after each valid telemetry frame:

```python
session = T104Session()
result = session.parse(apdu_bytes)
```

## Wire formats

### T101 and T103 FT1.2

Variable frames are `68 L L 68 [control] [link address] [ASDU] [CS] 16`. `L` is the number of bytes from control through ASDU, and `CS` is the sum of those bytes modulo 256. Fixed frames are `10 [control] [link address] [CS] 16`; the fixed-frame helper uses the one-byte control/address layout defined by this package API.

T103 uses the same FT1.2 framing and checksum but defaults to a two-byte link address. Companion-standard ASDU variations that are not Type 1 or Type 30 remain available through raw frame fields and can be decoded with `parse_asdu` using the relevant address widths.

### T104 APCI

An APDU is `68 L [four APCI control bytes] [ASDU]`. I-format control fields expose `send_sequence` and `receive_sequence`; S-format exposes the receive sequence; U-format exposes `STARTDT`, `STOPDT`, and `TESTFR` activation/confirmation names. APCI integers are decoded in little-endian wire order and sequence values are 15-bit values.

## ASDU schema

`parse_asdu` returns a dictionary containing `type_id`, `vsq`, `count`, `sequence`, `cot`, `common_address`, and `information_objects`. Type 1 objects decode a one-byte single-point value. Type 30 objects decode the one-byte value plus the seven raw CP56Time2a bytes. Unknown type identifiers are validated at the header/address level and leave their remaining bytes in `unparsed`.

The parser validates bounds before every read, rejects trailing bytes when parsing a single frame, and does not perform socket or stream I/O.

## Development

```text
python -m pip install pytest build
python -m pytest
python -m build
```
