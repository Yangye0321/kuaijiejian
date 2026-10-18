# -*- coding: utf-8 -*-
"""检查生成的 ICO 内部像素是否正常（用于排查导出问题）。"""
import collections
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, "icon", "kuaijiejian.ico")

data = open(ICO, "rb").read()
count = struct.unpack_from("<H", data, 4)[0]
print("ICO 共 %d 档：" % count)
for i in range(count):
    off = 6 + 16 * i
    (w, h, colors, res, planes, bpp, size, offset) = struct.unpack_from(
        "<BBBBHHII", data, off)
    print("  %3dx%-3d bpp=%d 数据=%d 偏移=%d" % (w or 256, h or 256, bpp, size, offset))

# 取最大的一档，统计颜色
big = max(range(count), key=lambda i: struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)[0] or 256)
off = 6 + 16 * big
(w, h, colors, res, planes, bpp, size, offset) = struct.unpack_from("<BBBBHHII", data, off)
w = w or 256
h = h or 256
print("\n检查 %dx%d：" % (w, h))
header = struct.unpack_from("<IiiHHIIiiII", data, offset)
print("  DIB 头：size=%s w=%s h=%s planes=%s bpp=%s imgsize=%s"
      % header[:6])
pix_off = offset + 40
raw = data[pix_off:pix_off + w * h * 4]
counter = collections.Counter()
opaque = 0
for i in range(0, len(raw), 4):
    b, g, r, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
    counter[(r // 32 * 32, g // 32 * 32, b // 32 * 32, a // 64 * 64)] += 1
    if a > 128:
        opaque += 1
print("  不透明像素：%d / %d（%.1f%%)" % (opaque, w * h, opaque * 100.0 / (w * h)))
print("  主要颜色（R,G,B,A 量化后 → 数量）：")
for color, n in counter.most_common(8):
    print("    %s → %d" % (str(color), n))
