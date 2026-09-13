from iec60870 import FrameStatus, FrameStream, T104Session, decode_cp56time2a, parse_asdu, parse_frame


def variable_frame(asdu: bytes, address: bytes = b"\x01") -> bytes:
    body = b"\x00" + address + asdu
    checksum = bytes([sum(body) & 0xFF])
    return b"\x68" + bytes([len(body)]) * 2 + b"\x68" + body + checksum + b"\x16"


def t104_i_frame(asdu: bytes, send: int = 0, receive: int = 0) -> bytes:
    c0 = (send << 1) & 0xFF
    c1 = (send >> 7) & 0xFF
    c2 = (receive << 1) & 0xFF
    c3 = (receive >> 7) & 0xFF
    body = bytes([c0, c1, c2, c3]) + asdu
    return b"\x68" + bytes([len(body)]) + body


def single_point_asdu(ioa: int = 1) -> bytes:
    return bytes([1, 1, 3, 0, 1, 0]) + ioa.to_bytes(3, "little") + b"\x01"


def test_t101_variable_and_asdu():
    result = parse_frame(variable_frame(single_point_asdu()), protocol="t101")
    assert result.status is FrameStatus.VALID
    assert result.fields["asdu"]["type_id"] == 1
    assert result.fields["asdu"]["information_objects"][0]["ioa"] == 1


def test_t101_checksum_corruption():
    frame = bytearray(variable_frame(single_point_asdu()))
    frame[-2] ^= 0x01
    assert parse_frame(frame, protocol="t101").status is FrameStatus.CORRUPTED


def test_fixed_frame_and_short_frame():
    assert parse_frame(b"\x10\x00\x01\x01\x16", protocol="t101").status is FrameStatus.VALID
    assert parse_frame(b"\x10\x00\x34\x12\x46\x16", protocol="t103").fields["link_address"] == 0x1234
    assert parse_frame(b"\x68\x04\x04", protocol="t101").status is FrameStatus.INCOMPLETE


def test_t103_two_byte_link_address():
    result = parse_frame(variable_frame(single_point_asdu(), b"\x34\x12"), protocol="t103")
    assert result.status is FrameStatus.VALID
    assert result.fields["link_address"] == 0x1234


def test_t104_i_sequence_and_session():
    session = T104Session()
    result = session.parse(t104_i_frame(single_point_asdu()))
    assert result.status is FrameStatus.VALID
    assert session.next_receive_sequence == 1
    assert parse_frame(t104_i_frame(single_point_asdu(), receive=3), protocol="t104", expected_receive_sequence=0).status is FrameStatus.CORRUPTED


def test_t104_s_and_u_formats():
    assert parse_frame(b"\x68\x04\x01\x00\x00\x00", protocol="t104").frame_type == "S"
    assert parse_frame(b"\x68\x04\x07\x00\x00\x00", protocol="t104").fields["u_function"] == "STARTDT act"
    assert parse_frame(b"\x68\x04\x13\x00\x00\x00", protocol="t104").fields["u_function"] == "STOPDT act"


def test_type_30_sequence_objects():
    header = bytes([30, 0x82, 3, 0, 1, 0])
    data = header + (10).to_bytes(3, "little") + b"\x01" + bytes(7) + b"\x00" + bytes(7)
    result = parse_asdu(data)
    assert result["status"] == "VALID"
    assert [item["ioa"] for item in result["information_objects"]] == [10, 11]


def test_cp56time2a_decoding():
    assert decode_cp56time2a(bytes([0xD2, 0x04, 0x2A, 0x0D, 0x09, 0x06, 0x1A])) == {
        "millisecond": 234,
        "second": 1,
        "minute": 42,
        "hour": 13,
        "day": 9,
        "month": 6,
        "year": 2026,
    }


def test_frame_stream_handles_chunks_and_multiple_frames():
    first = variable_frame(single_point_asdu(1))
    second = variable_frame(single_point_asdu(2))
    stream = FrameStream(protocol="t101")
    assert stream.feed(first[:4]) == []
    results = stream.feed(first[4:] + second)
    assert [result.status for result in results] == [FrameStatus.VALID, FrameStatus.VALID]
    assert results[1].fields["asdu"]["information_objects"][0]["ioa"] == 2
    assert stream.buffered_bytes == b""


def test_frame_stream_resynchronizes_after_corruption():
    stream = FrameStream(protocol="t101")
    results = stream.feed(b"\x00" + variable_frame(single_point_asdu()))
    assert results[0].status is FrameStatus.CORRUPTED
    assert results[1].status is FrameStatus.VALID
