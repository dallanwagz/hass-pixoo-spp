"""Exhaustive protocol battery for the Pixoo wire protocol.

Encodes the *requirements* of the protocol so any robustness gap surfaces as a failure:
frame/CRC correctness (incl. sum>0xFFFF carry), every still bpp with index round-trip,
animation encoding + chunking at size boundaries, and a hardened inbound frame parser
(partial reads, split buffers, leading garbage, stray 0x01 in the byte stream).
"""

import importlib.util
import struct
from pathlib import Path

import pytest

_PROTO = Path(__file__).resolve().parent.parent / "custom_components" / "pixoo_spp" / "protocol.py"
_spec = importlib.util.spec_from_file_location("pixoo_protocol", _PROTO)
p = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p)


# ----------------------------- helpers -----------------------------
def crc_of_inner(inner: bytes) -> int:
    return sum(inner) & 0xFFFF


def decode_frame(frame: bytes):
    """Independently parse one 01|LEN16|body|CRC16|02 frame -> (type, args)."""
    assert frame[0] == 0x01 and frame[-1] == 0x02
    ln = frame[1] | (frame[2] << 8)
    inner = frame[1:3] + frame[3 : 3 + (ln - 2)]
    body = frame[3 : 3 + (ln - 2)]
    crc = frame[3 + (ln - 2)] | (frame[3 + (ln - 2) + 1] << 8)
    assert crc == crc_of_inner(inner), "CRC mismatch in independent decode"
    return body[0], body[1:]


def unpack_indices(data: bytes, bpp: int, n: int) -> list[int]:
    bits = []
    for byte in data:
        for b in range(8):
            bits.append((byte >> b) & 1)
    out = []
    for i in range(n):
        v = 0
        for b in range(bpp):
            v |= bits[i * bpp + b] << b
        out.append(v)
    return out


def parse_aa_frames(payload: bytes) -> list[dict]:
    frames = []
    i = 0
    while i < len(payload):
        assert payload[i] == 0xAA, f"expected AA at {i}"
        ln = payload[i + 1] | (payload[i + 2] << 8)  # whole aa block length
        block = payload[i : i + ln]
        body = block[3:]
        tc = body[0] | (body[1] << 8)
        reset, palcount = body[2], body[3]
        palette = body[4 : 4 + palcount * 3]
        index_bytes = body[4 + palcount * 3 :]
        frames.append(
            dict(tc=tc, reset=reset, palcount=palcount, palette=palette, index_bytes=index_bytes)
        )
        i += ln
    return frames


# ----------------------------- frame / CRC -----------------------------
@pytest.mark.parametrize("payload", [b"", b"\x00", b"\xff" * 1, b"\x74\x32", bytes(range(50)), b"\xaa" * 600])
def test_frame_structure_invariants(payload):
    f = p.build_frame(0x44, payload)
    body = bytes([0x44]) + payload
    ln = len(body) + 2
    assert f[0] == 0x01 and f[-1] == 0x02
    assert f[1] == (ln & 0xFF) and f[2] == ((ln >> 8) & 0xFF)
    inner = f[1:3] + body
    crc = f[-3] | (f[-2] << 8)
    assert crc == crc_of_inner(inner)


def test_crc_carry_past_0xffff():
    # 600 * 0xFF + LEN bytes >> 0xFFFF — proves the &0xFFFF masking is applied
    payload = b"\xff" * 600
    f = p.build_frame(0x44, payload)
    inner = f[1:3] + bytes([0x44]) + payload
    assert sum(inner) > 0xFFFF
    crc = f[-3] | (f[-2] << 8)
    assert crc == sum(inner) & 0xFFFF


def test_len16_spans_two_bytes():
    payload = b"\x00" * 300
    f = p.build_frame(0x44, payload)
    ln = len(payload) + 1 + 2
    assert (f[1] | (f[2] << 8)) == ln and f[2] != 0


@pytest.mark.parametrize("t,payload", [(0x74, b"\x32"), (0xAF, b"\x01"), (0x44, bytes(range(80))), (0x8B, b"\x00\x10\x00\x00\x00")])
def test_frame_round_trip_through_iter(t, payload):
    frames, leftover = p.iter_frames(p.build_frame(t, payload))
    assert leftover == b""
    assert frames == [(t, payload)]


# ----------------------------- brightness -----------------------------
def test_brightness_type_and_clamp():
    assert p.brightness_frame(50).hex() == "0104007432aa0002"
    assert p.brightness_frame(100).hex() == "0104007464dc0002"
    for over in (101, 255, 9999):
        assert p.brightness_frame(over) == p.brightness_frame(100)
    for under in (0, -1, -50):
        assert p.brightness_frame(under) == p.brightness_frame(0)


# ----------------------------- stills -----------------------------
def test_bpp_table():
    assert [p.bpp_for(n) for n in (1, 2, 3, 4, 5, 16, 17, 256)] == [1, 1, 2, 2, 4, 4, 8, 8]


@pytest.mark.parametrize("ncolors,exp_bpp,exp_idx_bytes", [(2, 1, 32), (4, 2, 64), (16, 4, 128), (200, 8, 256)])
def test_still_index_packing_round_trip(ncolors, exp_bpp, exp_idx_bytes):
    palette = [(i, (i * 7) & 0xFF, (i * 13) & 0xFF) for i in range(ncolors)]
    indices = [i % ncolors for i in range(256)]
    frame = p.still_frame(palette, indices)
    typ, args = decode_frame(frame)
    assert typ == p.OP_STILL
    assert args[:4] == b"\x00\x0a\x0a\x04"
    aa = parse_aa_frames(args[4:])
    assert len(aa) == 1 and aa[0]["reset"] == 0 and aa[0]["palcount"] == ncolors
    assert len(aa[0]["index_bytes"]) == exp_idx_bytes
    assert unpack_indices(aa[0]["index_bytes"], exp_bpp, 256) == indices


def test_solid_still_has_no_index_data():
    typ, args = decode_frame(p.solid_still_frame((10, 20, 30)))
    aa = parse_aa_frames(args[4:])[0]
    assert aa["palcount"] == 1 and aa["index_bytes"] == b""
    assert aa["palette"] == bytes([10, 20, 30])


def test_solid_golden_frames():
    assert p.solid_still_frame((255, 0, 0), 100).hex() == "01110044000a0a04aa0a0064000001ff0000850202"
    assert p.solid_still_frame((255, 255, 255), 500).hex() == "01110044000a0a04aa0a00f4010001ffffff140502"


# ----------------------------- animation -----------------------------
def test_anim_shared_palette_and_reset_flags():
    palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    per_frame = [[0] * 256, [1] * 256, [2] * 256]
    aa = parse_aa_frames(p.encode_anim_payload(palette, per_frame))
    assert len(aa) == 3
    assert aa[0]["reset"] == 0 and aa[0]["palcount"] == 3  # frame 0 carries the palette
    for fr in aa[1:]:
        assert fr["reset"] == 1 and fr["palcount"] == 0  # later frames index frame 0's palette
    bpp = p.bpp_for(3)
    for k, fr in enumerate(aa):
        assert unpack_indices(fr["index_bytes"], bpp, 256) == per_frame[k]


def test_anim_rejects_more_than_16_colors():
    palette = [(i, 0, 0) for i in range(17)]
    with pytest.raises(ValueError):
        p.encode_anim_payload(palette, [[0] * 256])


@pytest.mark.parametrize("total,exp_chunks", [(0, 0), (1, 1), (255, 1), (256, 1), (257, 2), (512, 2), (1000, 4)])
def test_anim_chunk_count_and_headers(total, exp_chunks):
    payload = bytes(total)
    chunks = p.anim_chunks(payload, chunk=256)
    assert len(chunks) == exp_chunks
    seen = 0
    for idx, c in enumerate(chunks):
        typ, args = decode_frame(c)
        assert typ == p.OP_ANIM and args[0] == 0x01
        assert struct.unpack("<I", args[1:5])[0] == total
        assert struct.unpack("<H", args[5:7])[0] == idx
        seen += len(args[7:])
    assert seen == total


def test_anim_start_frame():
    typ, args = decode_frame(p.anim_start_frame(4096))
    assert typ == p.OP_ANIM and args[0] == 0x00
    assert struct.unpack("<I", args[1:5])[0] == 4096


def test_parse_resend_index():
    assert p.parse_resend_index(b"\x01\x00\x00\x00\x00\x05\x00") == 5
    assert p.parse_resend_index(b"\x01\x00\x00") is None  # too short
    assert p.parse_resend_index(b"\x00\x00\x00\x00\x00\x05\x00") is None  # wrong leading byte


# ----------------------------- inbound parser robustness -----------------------------
ECHO = bytes.fromhex("011b00044655000000ff5000640001036400ffffff00010000000000d30502")


def test_device_echo_satisfies_our_crc():
    # If the device's frames satisfy our CRC formula, the parser may validate CRC to resync.
    ln = ECHO[1] | (ECHO[2] << 8)
    inner = ECHO[1 : 3 + (ln - 2)]
    crc = ECHO[3 + (ln - 2)] | (ECHO[3 + (ln - 2) + 1] << 8)
    assert crc == crc_of_inner(inner)


def test_split_byte_by_byte_yields_each_frame_once():
    stream = ECHO + p.brightness_frame(50) + ECHO  # 3 frames back-to-back
    got = []
    leftover = b""
    for byte in stream:
        leftover += bytes([byte])
        frames, leftover = p.iter_frames(leftover)
        got.extend(frames)
    assert leftover == b""
    assert len(got) == 3


def test_leading_garbage_resyncs():
    # random junk (incl. a stray 0x01 with a bogus length) before a real frame
    junk = b"\x99\x01\x05\x77\x88\x00\xab\x02\x13"
    frames, leftover = p.iter_frames(junk + ECHO)
    assert (p.OP_STATE_ECHO, ) == tuple(p.parse_reply(*frames[0])[:1]) if frames else False
    assert len(frames) == 1 and leftover == b""


def test_stray_soi_with_bad_eoi_is_skipped():
    # a 0x01 whose claimed length lands on a non-0x02 byte must not derail the real frame
    bad = b"\x01\x04\x00\xde\xad\xbe\xef"  # end byte != 0x02
    frames, leftover = p.iter_frames(bad + ECHO)
    assert len(frames) == 1
    assert p.parse_reply(*frames[0])[0] == p.OP_STATE_ECHO


def test_absurd_length_soi_does_not_stall_real_frame():
    # stray 0x01 claiming a huge length must be skipped, not swallow the following real frame
    bad = b"\x01\xff\xff"  # claims ~64KB
    frames, leftover = p.iter_frames(bad + ECHO)
    assert len(frames) == 1 and p.parse_reply(*frames[0])[0] == p.OP_STATE_ECHO


def test_partial_trailing_frame_is_carried():
    frames, leftover = p.iter_frames(ECHO + b"\x01\x1b\x00\x04")
    assert len(frames) == 1 and leftover == b"\x01\x1b\x00\x04"


# ----------------------------- replies / state echo -----------------------------
@pytest.mark.parametrize("level,hexframe", [
    (100, "011b00044655000000ff5000640001036400ffffff00010000000000d30502"),
    (50, "011b00044655000000ff5000320001033200ffffff000100000000006f0502"),
    (20, "011b00044655000000ff5000140001031400ffffff00010000000000330502"),
])
def test_state_echo_brightness(level, hexframe):
    frames, _ = p.iter_frames(bytes.fromhex(hexframe))
    op, data = p.parse_reply(*frames[0])
    assert op == p.OP_STATE_ECHO
    assert p.decode_state_echo(data)["brightness"] == level


def test_parse_reply_rejects_non_ok():
    assert p.parse_reply(0x04, b"\x46\x00\x00") is None  # marker != 0x55
    assert p.parse_reply(0x04, b"\x46") is None  # too short


def test_decode_state_echo_short_data():
    assert p.decode_state_echo(b"\x00\x00") == {}
