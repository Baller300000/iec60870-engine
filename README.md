# IEC 60870 Parser

Pure-Python parsing for IEC 60870-5-101, -103, and -104 frames. This package is meant to be embedded inside other applications as a simple library: it validates wire data, reports whether a buffer is complete or corrupted, and exposes structured fields for downstream protocol logic.

It does not open sockets, manage connections, or perform I/O. Instead, it provides small building blocks that your application can use with serial reads, TCP sockets, or any other byte source.

## Features

- Parse single IEC 60870 frames from raw bytes
- Handle incomplete buffers without raising exceptions
- Support FT1.2 for T101/T103 and APCI for T104
- Decode common ASDU payloads and known object types
- Work on partial reads and multiple frames in one buffer
- Zero runtime dependencies

## Installation

```bash
python -m pip install iec60870-parser
```

From a local checkout:

```bash
python -m pip install -e .
```

## Quick start

```python
from iec60870 import FrameStatus, parse_frame

raw = b"\x68\x0a\x0a\x68\x00\x00\x01\x00\x16"
result = parse_frame(raw, protocol="t101")

if result.status is FrameStatus.VALID:
    print(result.frame_type)
    print(result.fields)
elif result.status is FrameStatus.INCOMPLETE:
    print("Need more bytes")
else:
    print(result.error)
```

Typical results are:

- `FrameStatus.VALID`: the frame is complete and matches the protocol rules
- `FrameStatus.INCOMPLETE`: the buffer is short and more bytes are required
- `FrameStatus.CORRUPTED`: the bytes do not match the encoding or checksum rules

## Library usage patterns

### 1. Parse one frame

```python
from iec60870 import parse_frame

result = parse_frame(data, protocol="t104")
```

Use `protocol="t101"`, `protocol="t103"`, or `protocol="t104"` when you know the protocol in advance. When the buffer is ambiguous, `protocol="auto"` will infer the format when enough bytes are present.

For FT1.2 frames, the default link address width is:

- T101: 1 byte
- T103: 2 bytes

You can override it explicitly with `link_address_size=1` or `link_address_size=2`.

### 2. Parse a stream of chunks

This is the recommended pattern for socket reads, serial ports, or other incremental byte sources.

```python
from iec60870 import FrameStatus, FrameStream

stream = FrameStream(protocol="t104")

for chunk in socket_like_source:
    for result in stream.feed(chunk):
        if result.status is FrameStatus.VALID:
            print(result.frame_type, result.fields)
        elif result.status is FrameStatus.CORRUPTED:
            print("Corrupted frame:", result.error)

print(stream.buffered_bytes)
```

Notes:

- `FrameStream.feed()` accepts bytes or byte-like input
- It returns all complete or corrupted frame results found in the current buffer
- Any trailing partial frame remains in `stream.buffered_bytes`
- Corrupted data triggers a resync by discarding one byte and continuing from the next position

### 3. Handle APCI sequence state for T104 connections

```python
from iec60870 import T104Session

session = T104Session()
result = session.parse(apdu_bytes)

if result.status is not None:
    print(result.fields)
```

`T104Session` tracks the receive sequence expected for the next I-format APDU and validates it automatically. This is useful when you are parsing a TCP stream and want to enforce standard T104 sequence behavior.

## Supported protocols

### T101 / T103 FT1.2

The library understands the FT1.2 framing used by T101 and T103:

- fixed frames: `10 [control] [link address] [CS] 16`
- variable frames: `68 L L 68 [control] [link address] [ASDU] [CS] 16`

The parser validates the repeating length marker and checksum. It also decodes the common ASDU header and known object types when `parse_asdu_data=True`.

### T104 APCI

For T104, the parser reads APDUs with the APCI structure:

- start byte: `0x68`
- length byte: `L`
- 4 APCI control bytes
- optional ASDU payload

It identifies:

- I-format frames: send and receive sequence numbers
- S-format frames: receive sequence number
- U-format frames: STARTDT, STOPDT, TESTFR activation/confirmation

## ASDU decoding

```python
from iec60870 import parse_asdu

asdu = parse_asdu(raw_asdu_bytes)
print(asdu["type_id"])
print(asdu["information_objects"])
```

`parse_asdu()` returns a dictionary describing the ASDU, including:

- `type_id`
- `vsq`
- `count`
- `sequence`
- `cot`
- `common_address`
- `information_objects`

Known object types include:

- Type 1: single-point information
- Type 30: CP56Time2a timestamped value

`decode_cp56time2a()` is also available directly for timestamp bytes.

## Error handling strategy

For library use, the parser is intentionally non-throwing:

- If the input is incomplete, it returns `FrameStatus.INCOMPLETE`
- If the input is malformed, it returns `FrameStatus.CORRUPTED`
- The returned `ParseResult` includes the protocol, frame length, frame type, decoded fields, and an optional `error` message

This makes it appropriate for embedded systems and network stacks where invalid or partial input is expected.

## Example: reading from a socket

```python
import socket
from iec60870 import FrameStatus, FrameStream

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("127.0.0.1", 2404))

stream = FrameStream(protocol="t104")

while True:
    chunk = sock.recv(4096)
    if not chunk:
        break

    for result in stream.feed(chunk):
        if result.status is FrameStatus.VALID:
            print(result.frame_type, result.fields)
        elif result.status is FrameStatus.CORRUPTED:
            print("Socket data was malformed:", result.error)
```

## API reference

```python
from iec60870 import (
    FrameStatus,
    FrameStream,
    ParseResult,
    T104Session,
    decode_cp56time2a,
    parse_asdu,
    parse_frame,
)
```

### `parse_frame(...)`

```python
parse_frame(
    data,
    *,
    protocol="auto",
    link_address_size=None,
    expected_receive_sequence=None,
    parse_asdu_data=True,
) -> ParseResult
```

### `FrameStream`

```python
stream = FrameStream(protocol="t104")
for result in stream.feed(chunk):
    ...
```

`stream.buffered_bytes` exposes any unconsumed trailing bytes.

### `T104Session`

```python
session = T104Session()
result = session.parse(data)
```

Validates expected T104 receive sequence numbers for I-format frames.

### `parse_asdu(...)`

```python
parse_asdu(data, *, ioa_size=3, cot_size=2, coa_size=2)
```

Decodes ASDU header and known information object payloads.

## Development

```bash
python -m pip install pytest build
python -m pytest
python -m build
```

## License

MIT
