# -*- coding: utf-8 -*-
"""检查图标四角/中心像素的原始 BGRA 值，确认 alpha 与形状是否正确。"""
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, "icon", "kuaijiejian.ico")

data = open(ICO, "rb").read()
count = struct.unpack_from("<H", data, 4)[0]
# 取 256 那档
off = 6 + 16 * (count - 1)
(w, h, colors, res, planes, bpp, size, offset) = struct.unpack_from(
    "<BBBBHHII", data, off)
w = w or 256
h = h or 256
raw = data[offset + 40:offset + 40 + w * h * 4]


def pixel(x, y):
    """y 从顶部算起。"""
    src_y = h - 1 - y
    i = (src_y * w + x) * 4
    b, g, r, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
    return (r, g, b, a)


points = [("左上角", 2, 2), ("右上角", 253, 2), ("左下角", 2, 253),
          ("右下角", 253, 253), ("顶边中央", 128, 2), ("中心", 128, 128),
          ("左侧键帽", 40, 110), ("箭头", 140, 112), ("右上小键帽", 175, 60)]
for name, x, y in points:
    print("  %-10s (%3d,%3d) = %s" % (name, x, y, pixel(x, y)))
