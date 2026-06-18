"""Pure Divoom Pixoo 16 wire protocol — no Home Assistant or I/O deps.

Frame ("NewMode", as used by the Pixoo — no byte-stuffing)::

    01 | LEN16(LE) | <type> <args...> | CRC16(LE) | 02
       LEN = len(type + args) + 2          (the body plus the 2 CRC bytes)
       CRC = sum(LEN bytes + body) & 0xFFFF

This module is the durable, unit-tested artifact: every encoder is pinned to a golden
byte vector captured from the real device / the reference encoder, and the inbound
parser decodes the device's ``04 <op> 55 <data>`` status echoes. The transport (a TCP
socket to an ``untether_spp`` ESP32 bridge) lives in the coordinator; this is transport
agnostic.
"""

from __future__ import annotations

import struct

SIZE = 16
SOI = 0x01
EOI = 0x02
REPLY = 0x04  # inbound frames are typed 0x04 (a response/echo)
REPLY_OK = 0x55  # 'U' — the device's "ok" marker in 04 <op> 55 <data>

# Opcodes
OP_HANDSHAKE = 0xAF
OP_BRIGHTNESS = 0x74
OP_STILL = 0x44
OP_ANIM = 0x8B
OP_STATE_ECHO = 0x46  # the box pushes this on a brightness/state change; carries brightness

_STILL_HEADER = b"\x00\x0A\x0A\x04"


def build_frame(type_byte: int, payload: bytes = b"") -> bytes:
    """01 | LEN16 | type+payload | CRC16 | 02 (no byte-stuffing on the Pixoo)."""
    body = bytes([type_byte]) + bytes(payload)
    ln = len(body) + 2
    inner = bytes([ln & 0xFF, (ln >> 8) & 0xFF]) + body
    crc = sum(inner) & 0xFFFF
    return bytes([SOI]) + inner + bytes([crc & 0xFF, (crc >> 8) & 0xFF]) + bytes([EOI])


def handshake_frame() -> bytes:
    """Send right after the link opens, or the screen stays on the BT icon."""
    return build_frame(OP_HANDSHAKE, b"\x01")  # -> 01 04 00 AF 01 B4 00 02


def brightness_frame(level: int) -> bytes:
    """0x74 | level(0..100)."""
    return build_frame(OP_BRIGHTNESS, bytes([max(0, min(100, int(level)))]))


def bpp_for(palette_size: int) -> int:
    """Bits-per-pixel the device expects for a palette of this size."""
    n = palette_size
    return 1 if n <= 2 else 2 if n <= 4 else 4 if n <= 16 else 8


def pack_indices(indices: list[int], bpp: int) -> bytes:
    """Pack palette indices LSB-first, row-major (same packing as a still)."""
    bits: list[int] = []
    for v in indices:
        bits += [(v >> b) & 1 for b in range(bpp)]
    out = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for j, bit in enumerate(bits[i : i + 8]):
            byte |= bit << j
        out.append(byte)
    return bytes(out)


def aa_frame(
    palette: list[tuple[int, int, int]],
    index_bytes: bytes,
    timecode: int = 500,
    reset: int = 0,
) -> bytes:
    """AA | LEN16(whole aa block) | timecode16 | reset | palCount | palette | indices."""
    body = (
        struct.pack("<H", timecode)
        + bytes([reset, len(palette)])
        + b"".join(bytes(c) for c in palette)
        + index_bytes
    )
    return bytes([0xAA]) + struct.pack("<H", 3 + len(body)) + body


def solid_still_frame(rgb: tuple[int, int, int], timecode: int = 100) -> bytes:
    """Fill the whole 16x16 with one colour (palCount=1, no index data)."""
    aa = aa_frame([tuple(rgb)], b"", timecode=timecode, reset=0)  # type: ignore[arg-type]
    return build_frame(OP_STILL, _STILL_HEADER + aa)


def still_frame(
    palette: list[tuple[int, int, int]], indices: list[int], timecode: int = 500
) -> bytes:
    """A 16x16 still from a palette + 256 row-major palette indices."""
    pc = len(palette)
    bpp = 0 if pc <= 1 else bpp_for(pc)
    index_bytes = pack_indices(indices, bpp) if bpp else b""
    aa = aa_frame(palette, index_bytes, timecode=timecode, reset=0)
    return build_frame(OP_STILL, _STILL_HEADER + aa)


def encode_anim_payload(
    palette: list[tuple[int, int, int]],
    per_frame_indices: list[list[int]],
    timecode: int = 100,
) -> bytes:
    """N frames -> the 0x8b payload. ONE shared palette (frame 0 carries it; frames 1..N
    are reset=1/palCount=0 and index into it). <=16 colours (bpp4) — more renders garbage."""
    bpp = bpp_for(len(palette))  # one shared bpp for the whole clip
    payload = b""
    for i, idxmap in enumerate(per_frame_indices):
        index_bytes = pack_indices(idxmap, bpp)
        if i == 0:
            payload += aa_frame(palette, index_bytes, timecode=timecode, reset=0)
        else:
            payload += aa_frame([], index_bytes, timecode=timecode, reset=1)
    return payload


def anim_start_frame(total: int) -> bytes:
    """0x8b | 00 | total32 — the START frame; wait the device's ready-ACK after this."""
    return build_frame(OP_ANIM, b"\x00" + struct.pack("<I", total))


def anim_chunks(payload: bytes, chunk: int = 256) -> list[bytes]:
    """Split the animation payload into 0x8b | 01 | total32 | idx16 | <=256B chunks."""
    total = len(payload)
    return [
        build_frame(
            OP_ANIM,
            b"\x01" + struct.pack("<I", total) + struct.pack("<H", idx) + payload[off : off + chunk],
        )
        for idx, off in enumerate(range(0, total, chunk))
    ]


def parse_resend_index(args: bytes) -> int | None:
    """For an inbound 0x8b resend request (01 | total32 | idx16), return the chunk index."""
    if len(args) >= 7 and args[0] == 0x01:
        return args[5] | (args[6] << 8)
    return None


def iter_frames(buf: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """Split a byte buffer into complete (type, args) frames; return (frames, leftover).

    Leftover is the trailing partial frame to carry into the next read.
    """
    frames: list[tuple[int, bytes]] = []
    i = 0
    n = len(buf)
    while i < n:
        if buf[i] != SOI:
            i += 1
            continue
        if i + 3 > n:
            break  # need LEN16
        ln = buf[i + 1] | (buf[i + 2] << 8)
        end = i + 3 + ln  # body(ln-2) + CRC(2) ends here; EOI at `end`
        if end >= n:
            break  # incomplete
        body = buf[i + 3 : i + 1 + ln]  # type + args (excludes CRC)
        if body:
            frames.append((body[0], body[1:]))
        i = end + 1
    return frames, buf[i:]


def parse_reply(type_byte: int, args: bytes) -> tuple[int, bytes] | None:
    """An inbound frame is ``04 <op> 55 <data>``; return (opcode, data) or None."""
    if type_byte == REPLY and len(args) >= 2 and args[1] == REPLY_OK:
        return args[0], args[2:]
    return None


def decode_state_echo(data: bytes) -> dict[str, int]:
    """Decode the 0x46 state echo. Verified: brightness lives at data[6].

    Example (bri=100): 04 46 55 | 00 00 00 ff 50 00 64 00 01 03 64 ...  -> data[6]=0x64.
    """
    out: dict[str, int] = {}
    if len(data) >= 7:
        out["brightness"] = data[6]
    return out
