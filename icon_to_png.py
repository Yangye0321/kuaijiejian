# -*- coding: utf-8 -*-
"""把 .ico 里的某一档图像导成 PNG（用于人工检查图标效果）。"""
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, "icon", "kuaijiejian.ico")


def read_entries(data):
    count = struct.unpack_from("<H", data, 4)[0]
    out = []
    for i in range(count):
        off = 6 + 16 * i
        (w, h, colors, _res, planes, bpp, size, offset) = struct.unpack_from(
            "<BBBBHHII", data, off)
        out.append({"w": w or 256, "h": h or 256, "bpp": bpp, "size": size,
                    "offset": offset})
    return out


def to_png(entry, data, path):
    w = entry["w"]
    h = entry["h"]
    off = entry["offset"]
    header = struct.unpack_from("<IiiHHIIiiII", data, off)
    (_hs, _wid, height2, _planes, bpp, _comp, _imgsize, *_rest) = header
    if bpp != 32:
        raise SystemExit("只支持 32 位图标，当前 %d bpp" % bpp)
    pix_off = off + 40
    xor_size = w * h * 4
    raw = data[pix_off:pix_off + xor_size]
    # BGRA 自下而上 → RGBA 自上而下
    rows = []
    for y in range(h):
        src_y = h - 1 - y
        line = bytearray()
        for x in range(w):
            i = (src_y * w + x) * 4
            b, g, r, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
            line += bytes((r, g, b, a))
        rows.append(bytes(line))

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    scanlines = b"".join(b"\x00" + r for r in rows)
    out += chunk(b"IDAT", zlib.compress(scanlines, 9))
    out += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(out)
    return w, h


def main():
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 256
    data = open(ICO, "rb").read()
    for entry in read_entries(data):
        if entry["w"] == size:
            out = os.path.join(HERE, "icon", "preview-%d.png" % size)
            w, h = to_png(entry, data, out)
            print("已导出 %s（%dx%d）" % (out, w, h))
            return 0
    print("没有 %d 这一档" % size)
    return 1


if __name__ == "__main__":
    sys.exit(main())
