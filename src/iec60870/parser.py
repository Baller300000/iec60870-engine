"""Bounded, allocation-conscious parsers for IEC 60870-5 FT1.2 and APCI.

The parser never raises for malformed wire data. It returns INCOMPLETE when more
bytes may make a frame valid and CORRUPTED when the bytes contradict the format.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence


class FrameStatus(str, Enum):
    VALID = "VALID"
    INCOMPLETE = "INCOMPLETE"
    CORRUPTED = "CORRUPTED"


@dataclass(frozen=True)
class ParseResult:
    status: FrameStatus
    protocol: str
    frame_length: Optional[int] = None
    frame_type: Optional[str] = None
    fields: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class T104Session:
    """Optional APCI sequence state for a TCP connection."""

    next_receive_sequence: int = 0
    next_send_sequence: int = 0

    def parse(self, data: bytes, *, parse_asdu_data: bool = True) -> ParseResult:
        result = parse_frame(
            data,
            protocol="t104",
            expected_receive_sequence=self.next_receive_sequence,
            parse_asdu_data=parse_asdu_data,
        )
        if result.status is FrameStatus.VALID and result.frame_type == "I":
            self.next_receive_sequence = (result.fields["receive_sequence"] + 1) & 0x7FFF
        return result


class FrameStream:
    """Incremental tokenizer for buffers containing zero or more frames."""

    def __init__(
        self,
        *,
        protocol: str = "auto",
        link_address_size: Optional[int] = None,
        parse_asdu_data: bool = True,
    ) -> None:
        self.protocol = protocol
        self.link_address_size = link_address_size
        self.parse_asdu_data = parse_asdu_data
        self._buffer = bytearray()

    @property
    def buffered_bytes(self) -> bytes:
        """Return the unconsumed partial frame held by the tokenizer."""
        return bytes(self._buffer)

    def feed(self, data: bytes) -> List[ParseResult]:
        """Consume bytes and return every complete or corrupted frame result."""
        try:
            self._buffer.extend(data)
        except (TypeError, ValueError):
            return [_result(FrameStatus.CORRUPTED, self.protocol, error="data must be bytes-like")]
        results: List[ParseResult] = []
        while self._buffer:
            candidate_length = self._candidate_length()
            if candidate_length is None:
                break
            result = parse_frame(
                self._buffer[:candidate_length],
                protocol=self.protocol,
                link_address_size=self.link_address_size,
                parse_asdu_data=self.parse_asdu_data,
            )
            if result.status is FrameStatus.INCOMPLETE:
                break
            if result.status is FrameStatus.VALID:
                results.append(result)
                del self._buffer[:result.frame_length or len(self._buffer)]
                continue
            results.append(result)
            del self._buffer[0]
        return results

    def _candidate_length(self) -> Optional[int]:
        """Return the first frame length when its header is available."""
        if not self._buffer:
            return None
        selected = self.protocol.lower()
        if selected in {"t101", "t103"} or selected == "auto":
            if self._buffer[0] not in (0x10, 0x68):
                return 1
            address_size = self.link_address_size or (2 if self.protocol.lower() == "t103" else 1)
            if self._buffer[0] == 0x10:
                return 4 + address_size if len(self._buffer) >= 4 + address_size else None
            if len(self._buffer) < 4:
                return None
            if self._buffer[3] == 0x68:
                return self._buffer[1] + 6 if len(self._buffer) >= self._buffer[1] + 6 else None
            if selected == "auto":
                return self._buffer[1] + 2
            return 1
        if len(self._buffer) < 2:
            return None
        return self._buffer[1] + 2


def _u16le(data: Sequence[int], offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


def _u24le(data: Sequence[int], offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16)


def _result(status: FrameStatus, protocol: str, **kwargs: Any) -> ParseResult:
    return ParseResult(status=status, protocol=protocol, **kwargs)


def _parse_ft12(
    data: bytes,
    protocol: str,
    *,
    link_address_size: int,
    parse_asdu_data: bool,
) -> ParseResult:
    if not data:
        return _result(FrameStatus.INCOMPLETE, protocol, error="empty buffer")
    if data[0] == 0x10:
        total = 4 + link_address_size
        if len(data) < total:
            return _result(FrameStatus.INCOMPLETE, protocol, frame_type="fixed")
        if data[total - 1] != 0x16:
            return _result(FrameStatus.CORRUPTED, protocol, frame_type="fixed", error="bad fixed-frame stop")
        checksum_offset = total - 2
        checksum = sum(data[1:checksum_offset]) & 0xFF
        if checksum != data[checksum_offset]:
            return _result(FrameStatus.CORRUPTED, protocol, frame_type="fixed", error="checksum mismatch")
        if len(data) > total:
            return _result(FrameStatus.CORRUPTED, protocol, frame_type="fixed", error="trailing bytes")
        return _result(
            FrameStatus.VALID,
            protocol,
            frame_length=total,
            frame_type="fixed",
            fields={
                "control": data[1],
                "link_address": int.from_bytes(data[2:checksum_offset], "little"),
                "checksum": data[checksum_offset],
            },
        )
    if data[0] != 0x68:
        return _result(FrameStatus.CORRUPTED, protocol, error="unknown FT1.2 start")
    if len(data) < 4:
        return _result(FrameStatus.INCOMPLETE, protocol, frame_type="variable")
    length = data[1]
    if data[2] != length or data[3] != 0x68:
        return _result(FrameStatus.CORRUPTED, protocol, frame_type="variable", error="length/start repetition mismatch")
    total = length + 6
    if length < 1 + link_address_size:
        return _result(FrameStatus.CORRUPTED, protocol, frame_type="variable", error="length excludes control/address")
    if len(data) < total:
        return _result(FrameStatus.INCOMPLETE, protocol, frame_type="variable", frame_length=total)
    if len(data) > total:
        return _result(FrameStatus.CORRUPTED, protocol, frame_type="variable", error="trailing bytes")
    if data[total - 1] != 0x16:
        return _result(FrameStatus.CORRUPTED, protocol, frame_type="variable", error="bad variable-frame stop")
    body_start = 4
    checksum_offset = 4 + length
    if (sum(data[body_start:checksum_offset]) & 0xFF) != data[checksum_offset]:
        return _result(FrameStatus.CORRUPTED, protocol, frame_type="variable", error="checksum mismatch")
    control = data[body_start]
    address = int.from_bytes(data[body_start + 1:body_start + 1 + link_address_size], "little")
    asdu_start = body_start + 1 + link_address_size
    fields: Dict[str, Any] = {
        "control": control,
        "link_address": address,
        "checksum": data[checksum_offset],
    }
    if parse_asdu_data and asdu_start < checksum_offset:
        asdu = parse_asdu(data[asdu_start:checksum_offset])
        if asdu["status"] != FrameStatus.VALID.value:
            return _result(FrameStatus.CORRUPTED, protocol, frame_type="variable", error=asdu["error"])
        fields["asdu"] = asdu
    return _result(FrameStatus.VALID, protocol, frame_length=total, frame_type="variable", fields=fields)


def _parse_t104(
    data: bytes,
    *,
    expected_receive_sequence: Optional[int],
    parse_asdu_data: bool,
) -> ParseResult:
    protocol = "t104"
    if not data:
        return _result(FrameStatus.INCOMPLETE, protocol, error="empty buffer")
    if data[0] != 0x68:
        return _result(FrameStatus.CORRUPTED, protocol, error="missing APCI start")
    if len(data) < 2:
        return _result(FrameStatus.INCOMPLETE, protocol)
    apdu_length = data[1]
    if apdu_length < 4:
        return _result(FrameStatus.CORRUPTED, protocol, error="APCI length is below four control bytes")
    if apdu_length > 253:
        return _result(FrameStatus.CORRUPTED, protocol, error="APCI length exceeds 253-byte limit")
    total = apdu_length + 2
    if len(data) < total:
        return _result(FrameStatus.INCOMPLETE, protocol, frame_length=total)
    if len(data) > total:
        return _result(FrameStatus.CORRUPTED, protocol, error="multiple APDUs or trailing bytes")
    control = data[2:6]
    if (control[0] & 1) == 0:
        frame_type = "I"
        send_sequence = ((control[1] << 8) | control[0]) >> 1
        receive_sequence = ((control[3] << 8) | control[2]) >> 1
        if expected_receive_sequence is not None and receive_sequence != expected_receive_sequence:
            return _result(FrameStatus.CORRUPTED, protocol, frame_type=frame_type, error="receive sequence mismatch")
        fields: Dict[str, Any] = {"send_sequence": send_sequence, "receive_sequence": receive_sequence}
        asdu_start = 6
    elif (control[0] & 3) == 1:
        frame_type = "S"
        if control[1] != 0:
            return _result(FrameStatus.CORRUPTED, protocol, frame_type=frame_type, error="non-zero reserved S-format bits")
        receive_sequence = ((control[3] << 8) | control[2]) >> 1
        fields = {"receive_sequence": receive_sequence}
        asdu_start = total
    else:
        frame_type = "U"
        if control[1] != 0 or control[2] != 0 or control[3] != 0:
            return _result(FrameStatus.CORRUPTED, protocol, frame_type=frame_type, error="non-zero reserved U-format bits")
        code = control[0] & 0xFC
        u_names = {0x04: "STARTDT act", 0x08: "STARTDT con", 0x10: "STOPDT act", 0x20: "STOPDT con", 0x40: "TESTFR act", 0x80: "TESTFR con"}
        fields = {"u_function": u_names.get(code, "UNKNOWN"), "u_code": code}
        asdu_start = total
    if parse_asdu_data and frame_type == "I":
        asdu = parse_asdu(data[asdu_start:total])
        if asdu["status"] != FrameStatus.VALID.value:
            return _result(FrameStatus.CORRUPTED, protocol, frame_type=frame_type, error=asdu["error"])
        fields["asdu"] = asdu
    return _result(FrameStatus.VALID, protocol, frame_length=total, frame_type=frame_type, fields=fields)


def parse_frame(
    data: bytes,
    *,
    protocol: str = "auto",
    link_address_size: Optional[int] = None,
    expected_receive_sequence: Optional[int] = None,
    parse_asdu_data: bool = True,
) -> ParseResult:
    """Parse exactly one frame from a bytes-like buffer.

    ``protocol`` is ``"t101"``, ``"t103"``, ``"t104"``, or ``"auto"``.
    Auto mode distinguishes FT1.2 by its repeated 0x68 marker; explicit mode is
    recommended when an incomplete buffer contains fewer than four bytes.
    """
    try:
        raw = bytes(data)
    except (TypeError, ValueError):
        return _result(FrameStatus.CORRUPTED, protocol, error="data must be bytes-like")
    selected = protocol.lower()
    if selected not in {"auto", "t101", "t103", "t104"}:
        return _result(FrameStatus.CORRUPTED, selected, error="unsupported protocol")
    if selected == "auto":
        if len(raw) >= 4 and raw[0] == 0x68 and raw[3] == 0x68:
            selected = "t101"
        elif raw and raw[0] in (0x10,):
            selected = "t101"
        elif raw and raw[0] == 0x68:
            selected = "t104"
        else:
            selected = "t101"
    if selected in {"t101", "t103"}:
        address_size = link_address_size if link_address_size is not None else (1 if selected == "t101" else 2)
        if address_size not in (1, 2):
            return _result(FrameStatus.CORRUPTED, selected, error="link_address_size must be 1 or 2")
        return _parse_ft12(raw, selected, link_address_size=address_size, parse_asdu_data=parse_asdu_data)
    return _parse_t104(raw, expected_receive_sequence=expected_receive_sequence, parse_asdu_data=parse_asdu_data)


def parse_asdu(data: bytes, *, ioa_size: int = 3, cot_size: int = 2, coa_size: int = 2) -> Dict[str, Any]:
    """Decode the common ASDU header and known Type 1 and Type 30 objects."""
    raw = bytes(data)
    if ioa_size not in (1, 2, 3) or cot_size not in (1, 2) or coa_size not in (1, 2):
        return {"status": FrameStatus.CORRUPTED.value, "error": "unsupported ASDU address width"}
    header_size = 2 + cot_size + coa_size
    if len(raw) < header_size:
        return {"status": FrameStatus.INCOMPLETE.value, "error": "short ASDU header"}
    type_id = raw[0]
    vsq = raw[1]
    count = vsq & 0x7F
    sequence = bool(vsq & 0x80)
    cot = int.from_bytes(raw[2:2 + cot_size], "little")
    coa_start = 2 + cot_size
    common_address = int.from_bytes(raw[coa_start:coa_start + coa_size], "little")
    offset = header_size
    objects = []
    value_size = 1 + (7 if type_id == 30 else 0)
    if type_id not in (1, 30):
        value_size = 0
    for index in range(count):
        if not sequence or index == 0:
            if offset + ioa_size > len(raw):
                return {"status": FrameStatus.INCOMPLETE.value, "error": "short information object address"}
            ioa = int.from_bytes(raw[offset:offset + ioa_size], "little")
            offset += ioa_size
        else:
            ioa += 1
        item: Dict[str, Any] = {"ioa": ioa}
        if value_size:
            if offset + value_size > len(raw):
                return {"status": FrameStatus.INCOMPLETE.value, "error": "short information object value"}
            item["value"] = raw[offset]
            offset += 1
            if type_id == 30:
                timestamp = raw[offset:offset + 7]
                item["cp56time2a"] = timestamp
                item["timestamp"] = decode_cp56time2a(timestamp)
                offset += 7
        objects.append(item)
    return {"status": FrameStatus.VALID.value, "type_id": type_id, "vsq": vsq, "count": count, "sequence": sequence, "cot": cot, "common_address": common_address, "information_objects": objects, "unparsed": raw[offset:]}


def decode_cp56time2a(data: bytes) -> Dict[str, int]:
    """Decode CP56Time2a calendar fields without timezone conversion."""
    raw = bytes(data)
    if len(raw) != 7:
        raise ValueError("CP56Time2a requires exactly 7 bytes")
    milliseconds = raw[0] | (raw[1] << 8)
    return {
        "millisecond": milliseconds % 1000,
        "second": milliseconds // 1000,
        "minute": raw[2] & 0x3F,
        "hour": raw[3] & 0x1F,
        "day": raw[4] & 0x1F,
        "month": raw[5] & 0x0F,
        "year": 2000 + (raw[6] & 0x7F),
    }
