"""Golden-frame tests for the Pixoo wire protocol (pure, no HA needed)."""

import importlib.util
from pathlib import Path

_PROTO = Path(__file__).resolve().parent.parent / "custom_components" / "pixoo_spp" / "protocol.py"
_spec = importlib.util.spec_from_file_location("pixoo_protocol", _PROTO)
p = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p)


def hx(b: bytes) -> str:
    return b.hex()


def test_handshake_golden():
    assert hx(p.handshake_frame()) == "010400af01b40002"


def test_brightness_golden():
    assert hx(p.brightness_frame(50)) == "0104007432aa0002"
    assert hx(p.brightness_frame(100)) == "0104007464dc0002"


def test_brightness_clamps():
    assert hx(p.brightness_frame(999)) == hx(p.brightness_frame(100))
    assert hx(p.brightness_frame(-5)) == hx(p.brightness_frame(0))


def test_solid_still_golden():
    assert hx(p.solid_still_frame((255, 0, 0), timecode=100)) == (
        "01110044000a0a04aa0a0064000001ff0000850202"
    )
    assert hx(p.solid_still_frame((255, 255, 255), timecode=500)) == (
        "01110044000a0a04aa0a00f4010001ffffff140502"
    )


def test_still_frame_solid_matches_solid_helper():
    # a 1-colour palette still == the solid helper (bpp 0, no index data)
    a = p.still_frame([(255, 0, 0)], [0] * 256, timecode=100)
    assert a == p.solid_still_frame((255, 0, 0), timecode=100)


def test_bpp_thresholds():
    assert p.bpp_for(2) == 1
    assert p.bpp_for(4) == 2
    assert p.bpp_for(16) == 4
    assert p.bpp_for(17) == 8


def test_iter_frames_splits_state_echo_and_leftover():
    # two state echoes + a trailing partial frame
    echo = bytes.fromhex("011b00044655000000ff5000640001036400ffffff00010000000000d30502")
    buf = echo + echo + b"\x01\x1b\x00\x04"  # last is incomplete
    frames, leftover = p.iter_frames(buf)
    assert len(frames) == 2
    assert leftover == b"\x01\x1b\x00\x04"
    for type_byte, args in frames:
        reply = p.parse_reply(type_byte, args)
        assert reply is not None
        opcode, data = reply
        assert opcode == p.OP_STATE_ECHO
        assert p.decode_state_echo(data)["brightness"] == 100


def test_decode_state_echo_brightness_levels():
    for level, hexframe in (
        (50, "011b00044655000000ff5000320001033200ffffff000100000000006f0502"),
        (20, "011b00044655000000ff5000140001031400ffffff00010000000000330502"),
    ):
        frames, _ = p.iter_frames(bytes.fromhex(hexframe))
        (_op, data) = p.parse_reply(*frames[0])
        assert p.decode_state_echo(data)["brightness"] == level


def test_anim_round_trip_shapes():
    palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    per_frame = [[i % 4 for _ in range(256)] for i in range(4)]
    payload = p.encode_anim_payload(palette, per_frame, timecode=150)
    assert payload[:1] == b"\xaa"  # starts with an aa-frame
    chunks = p.anim_chunks(payload, chunk=256)
    assert len(chunks) >= 1
    start = p.anim_start_frame(len(payload))
    # START frame carries the total length little-endian after 0x8b 0x00
    assert start[3] == p.OP_ANIM and start[4] == 0x00
    # a resend request for chunk 2 round-trips
    assert p.parse_resend_index(b"\x01\x00\x00\x00\x00\x02\x00") == 2
