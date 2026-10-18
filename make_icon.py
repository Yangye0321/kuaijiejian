# -*- coding: utf-8 -*-
"""生成图标（.ico）+ 改成带图标启动的辅助文件。纯标准库，无需 Pillow。

图标含义：左边一个按键（小键盘点），右边一个按键组（Ctrl+Alt+0），中间一个箭头
——「按一个键，代替一串组合键」的直观表达。

用法：
    python make_icon.py                 # 只生成 icon\\kuaijiejian.ico
    python make_icon.py --batch 文件     # 顺手给某个 .bat 头部加上 ICON= 字段
"""

import ctypes
import math
import os
import shutil
import struct
import sys

# 控制台代码页可能不是 UTF-8（中文 Windows 常见 cp936），
# 直接打印 ✓ 之类字符会抛 UnicodeEncodeError，把生成流程整个打断。
# 被别的脚本以管道方式调用时改写成 UTF-8，方便调用方按 UTF-8 解码。
try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    else:
        sys.stdout.reconfigure(errors="replace")
except Exception:
    pass


def out(text):
    """安全打印：编码不了就替换，绝不因为输出问题中断生成。"""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except Exception:
        try:
            sys.stdout.write(text.encode("ascii", "replace").decode() + "\n")
        except Exception:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "icon")
ICON_PATH = os.path.join(ICON_DIR, "kuaijiejian.ico")
SIZES = (16, 24, 32, 48, 64, 128, 256)
SS = 4  # 超采样倍数（抗锯齿）

# 配色
BG_TOP = (58, 140, 255, 255)      # 蓝
BG_BOTTOM = (26, 82, 178, 255)    # 深蓝
CAP_FACE = (252, 253, 255, 255)   # 键帽白
CAP_SIDE = (176, 197, 226, 255)   # 键帽侧边
CAP_TEXT = (30, 62, 120, 255)     # 键帽文字
ARROW = (255, 214, 92, 255)       # 箭头金色
TEXT = (255, 255, 255, 255)


class Surface(object):
    """极简 RGBA 画布。

    内部以 size*SS 的超采样分辨率存储，对外仍用「逻辑像素」坐标（0..size-1），
    导出时按 SS×SS 块降采样，得到平滑边缘。
    """

    def __init__(self, size):
        self.logical = size
        self.size = size * SS
        self.px = [[(0, 0, 0, 0)] * self.size for _ in range(self.size)]

    def _blend(self, x, y, color):
        if not (0 <= x < self.size and 0 <= y < self.size):
            return
        r, g, b, a = color
        if a <= 0:
            return
        if a >= 255:
            self.px[y][x] = (r, g, b, 255)
            return
        dr, dg, db, da = self.px[y][x]
        af = a / 255.0
        na = int(a + da * (1 - af))
        self.px[y][x] = (
            int(r * af + dr * (1 - af)),
            int(g * af + dg * (1 - af)),
            int(b * af + db * (1 - af)),
            min(255, na),
        )

    def blend(self, x, y, color):
        """把一个逻辑像素细分成 SS×SS 个采样点着色。"""
        bx = int(x) * SS
        by = int(y) * SS
        for sy in range(SS):
            for sx in range(SS):
                self._blend(bx + sx, by + sy, color)

    def downsample(self):
        """降采样成逻辑分辨率，返回 [(r,g,b,a)] 的二维列表。

        颜色按 alpha 预乘后平均，避免边缘出现黑边。
        """
        out = []
        n = SS * SS
        for ly in range(self.logical):
            row = []
            for lx in range(self.logical):
                sr = sg = sb = sa = 0
                for sy in range(SS):
                    for sx in range(SS):
                        r, g, b, a = self.px[ly * SS + sy][lx * SS + sx]
                        sr += r * a
                        sg += g * a
                        sb += b * a
                        sa += a
                if sa == 0:
                    row.append((0, 0, 0, 0))
                else:
                    row.append((int(sr / sa), int(sg / sa), int(sb / sa),
                                int(sa / n)))
            out.append(row)
        return out

    def fill_round_rect(self, x0, y0, x1, y1, radius, color):
        for y in range(int(y0), int(y1)):
            for x in range(int(x0), int(x1)):
                if _in_round_rect(x + 0.5, y + 0.5, x0, y0, x1, y1, radius):
                    self.blend(x, y, color)

    def fill_circle(self, cx, cy, r, color):
        for y in range(int(cy - r), int(cy + r) + 1):
            for x in range(int(cx - r), int(cx + r) + 1):
                if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r:
                    self.blend(x, y, color)

    def fill_triangle(self, p0, p1, p2, color):
        xs = [p[0] for p in (p0, p1, p2)]
        ys = [p[1] for p in (p0, p1, p2)]
        for y in range(int(min(ys)), int(max(ys)) + 1):
            for x in range(int(min(xs)), int(max(xs)) + 1):
                if _point_in_triangle((x + 0.5, y + 0.5), p0, p1, p2):
                    self.blend(x, y, color)

    def fill_rect(self, x0, y0, x1, y1, color):
        for y in range(int(y0), int(y1)):
            for x in range(int(x0), int(x1)):
                self.blend(x, y, color)

    def fill_glyph(self, glyph, ox, oy, scale, color, thickness=1):
        """用 5x7 点阵写字符。glyph 是 7 行、每行 5 个 '0/1' 的字符串列表。"""
        for row, line in enumerate(glyph):
            for col, ch in enumerate(line):
                if ch == "1":
                    x0 = ox + col * scale
                    y0 = oy + row * scale
                    self.fill_rect(x0, y0, x0 + scale * thickness,
                                   y0 + scale * thickness, color)


GLYPHS = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "dot": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
}


def _in_round_rect(x, y, x0, y0, x1, y1, r):
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r),
                   (x0 + r, y1 - r), (x1 - r, y1 - r)):
        if (x < x0 + r or x > x1 - r) and (y < y0 + r or y > y1 - r):
            return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    return True


def _point_in_triangle(p, a, b, c):
    def sign(p1, p2, p3):
        return ((p1[0] - p3[0]) * (p2[1] - p3[1])
                - (p2[0] - p3[0]) * (p1[1] - p3[1]))
    d1 = sign(p, a, b)
    d2 = sign(p, b, c)
    d3 = sign(p, c, a)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def draw(size):
    """画一张 size×size 的图标。

    构图：蓝色圆角底 + 左侧大键帽（小键盘点）+ 金色箭头 + 右侧三个键帽
    （代表 Ctrl / Alt / 小键盘0 这一串组合键）。
    """
    s = Surface(size)
    u = size / 100.0

    # 背景：圆角方块 + 竖向渐变
    radius = 20 * u
    for y in range(size):
        t = y / float(size - 1) if size > 1 else 0.0
        color = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t)
                      for i in range(4))
        for x in range(size):
            if _in_round_rect(x + 0.5, y + 0.5, 0, 0, size - 1, size - 1,
                              radius):
                s.blend(x, y, color)

    # 左侧大键帽（小键盘 .）
    lx0, ly0, lx1, ly1 = 9 * u, 26 * u, 41 * u, 58 * u
    s.fill_round_rect(lx0 + 2.5 * u, ly0 + 3.5 * u, lx1 + 2.5 * u,
                      ly1 + 3.5 * u, 6 * u, CAP_SIDE)
    s.fill_round_rect(lx0, ly0, lx1, ly1, 6 * u, CAP_FACE)
    gscale = max(1, int(round(size / 42.0)))
    gw = 5 * gscale
    gh = 7 * gscale
    s.fill_glyph(GLYPHS["dot"], (lx0 + lx1) / 2 - gw / 2,
                 (ly0 + ly1) / 2 - gh / 2 + 0.5 * gscale, gscale, CAP_TEXT)

    # 中间金色箭头
    cy = 42 * u
    s.fill_rect(43 * u, cy - 3 * u, 60 * u, cy + 3 * u, ARROW)
    s.fill_triangle((57 * u, cy - 9 * u), (57 * u, cy + 9 * u), (70 * u, cy),
                    ARROW)

    # 右侧三个键帽：Ctrl / Alt / 小键盘0（最下一个高亮，代表要按的那个键）
    caps = [
        (62 * u, 16 * u, 26 * u, 15 * u, CAP_FACE, CAP_TEXT, "0"),
        (70 * u, 35 * u, 24 * u, 14 * u, CAP_FACE, CAP_TEXT, "1"),
        (62 * u, 55 * u, 26 * u, 15 * u, ARROW, (60, 44, 10, 255), "0"),
    ]
    for x, y, cw, ch, face, text, label in caps:
        s.fill_round_rect(x, y, x + cw, y + ch, 3 * u, face)
        gs = max(1, int(round(size / 66.0)))
        gw = 5 * gs
        gh = 7 * gs
        s.fill_glyph(GLYPHS[label], x + (cw - gw) / 2, y + (ch - gh) / 2,
                     gs, text)

    return s


def surface_to_bgra(surf, size):
    """转成 ICO 需要的 BGRA 行序（自下而上），size 为逻辑尺寸。"""
    pixels = surf.downsample()
    rows = []
    for y in range(size - 1, -1, -1):
        row = bytearray()
        for x in range(size):
            r, g, b, a = pixels[y][x]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    return b"".join(rows)


def surface_to_mask(surf, size):
    """1 位 AND 掩码：alpha < 128 记为透明。每行按 4 字节对齐。"""
    pixels = surf.downsample()
    stride = ((size + 31) // 32) * 4
    out = bytearray()
    for y in range(size - 1, -1, -1):
        bits = bytearray(stride)
        for x in range(size):
            if pixels[y][x][3] < 128:
                bits[x // 8] |= 0x80 >> (x % 8)
        out += bits
    return bytes(out)


def build_ico(sizes=SIZES):
    entries = []
    images = []
    for size in sizes:
        surf = draw(size)
        bgra = surface_to_bgra(surf, size)
        mask = surface_to_mask(surf, size)
        header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                             len(bgra) + len(mask), 0, 0, 0, 0)
        images.append(header + bgra + mask)
        entries.append((size, len(header) + len(bgra) + len(mask)))
    out = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    offset = 6 + 16 * len(entries)
    for (size, length) in entries:
        dim = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, length, offset)
        offset += length
    for img in images:
        out += img
    return bytes(out)


def find_powershell():
    for name in ("powershell.exe", "powershell"):
        for base in (os.environ.get("SystemRoot", r"C:\Windows"),):
            path = os.path.join(base, "System32", "WindowsPowerShell", name)
            if os.path.isfile(path):
                return path
    return "powershell.exe"


def make_shortcut(lnk_path, target_bat, workdir, icon_path, args=""):
    """用 PowerShell 的 WScript.Shell 生成 .lnk（Windows 唯一可靠的换图标方式）。"""
    ps = find_powershell()
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut('%s'); "
        "$s.TargetPath = '%s'; "
        "$s.WorkingDirectory = '%s'; "
        "$s.Arguments = '%s'; "
        "$s.IconLocation = '%s'; "
        "$s.Description = 'Shortcut Manager'; "
        "$s.Save()" % (lnk_path, target_bat, workdir, args, icon_path)
    )
    import subprocess
    try:
        proc = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
                               "-Command", script],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return proc.returncode == 0, proc.stdout.decode("utf-8", "replace")
    except Exception as exc:
        return False, str(exc)


def add_icon_field(bat_path, icon_path):
    """在 bat 里加一行「图标位置」的记录（注释形式）。

    说明：现代 Windows 资源管理器**不会**读 .bat 里的图标声明，唯一可靠的方式是
    快捷方式（.lnk，见 make_shortcut）。早年的约定是写一行 `ICON=xxx.ico`，
    但 cmd.exe 会把 `ICON=` 当命令执行并报「不是内部或外部命令」——
    所以这里写成 rem 注释：既记录了图标位置，又不产生报错。
    """
    with open(bat_path, "r", encoding="ascii", newline="") as fh:
        text = fh.read()
    if "kuaijiejian.ico" in text:
        return False
    rel = os.path.relpath(icon_path, os.path.dirname(os.path.abspath(bat_path)))
    rel = rel.replace("/", "\\")
    lines = text.split("\r\n") if "\r\n" in text else text.split("\n")
    insert_at = 1 if lines and lines[0].strip().lower().startswith("@echo") else 0
    lines.insert(insert_at,
                 "rem icon: %s   (Explorer ignores this; use the .lnk)" % rel)
    joined = "\r\n".join(lines)
    with open(bat_path, "w", encoding="ascii", newline="") as fh:
        fh.write(joined)
    return True


def main():
    argv = sys.argv[1:]

    if "--shortcut" in argv:
        idx = argv.index("--shortcut")
        raw_dir = argv[idx + 1] if len(argv) > idx + 1 else HERE
        # 批处理里写 "%~dp0" 会带上引号和结尾反斜杠，这里去掉并规范化
        target_dir = os.path.abspath(
            raw_dir.strip().strip('"').strip("'").rstrip("\\/") or HERE)
        if not os.path.isdir(target_dir):
            out("  目录不存在：%s（原始参数：%r）" % (target_dir, raw_dir))
            return 1
        icon_src = os.path.join(HERE, "icon", "kuaijiejian.ico")
        if not os.path.isfile(icon_src):
            data = build_ico()
            os.makedirs(os.path.dirname(icon_src), exist_ok=True)
            with open(icon_src, "wb") as fh:
                fh.write(data)
            out("  OK 先生成了 %s" % icon_src)
        dest_icon_dir = os.path.join(target_dir, "icon")
        if os.path.abspath(dest_icon_dir) != os.path.abspath(
                os.path.dirname(icon_src)):
            os.makedirs(dest_icon_dir, exist_ok=True)
            shutil.copy2(icon_src, os.path.join(dest_icon_dir,
                                                "kuaijiejian.ico"))
        icon = os.path.join(dest_icon_dir, "kuaijiejian.ico")
        bat = os.path.join(target_dir, "start-kuaijiejian.bat")
        if not os.path.isfile(bat):
            out("  ERR 目标目录里没有 start-kuaijiejian.bat：%s" % target_dir)
            return 1
        lnk = os.path.join(target_dir, "快捷键管理工具.lnk")
        ok, message = make_shortcut(lnk, bat, target_dir, icon)
        if ok:
            out("  OK %s" % lnk)
            try:
                add_icon_field(bat, icon)
            except Exception:
                pass
        else:
            out("  ERR 生成快捷方式失败：%s" % message.strip()[:300])
            return 1
        return 0

    if not os.path.isdir(ICON_DIR):
        os.makedirs(ICON_DIR, exist_ok=True)
    data = build_ico()
    with open(ICON_PATH, "wb") as fh:
        fh.write(data)
    out("  OK %s（%d 字节，%d 种尺寸：%s）"
        % (ICON_PATH, len(data), len(SIZES),
           ", ".join(str(s) for s in SIZES)))

    if "--batch" in sys.argv:
        idx = sys.argv.index("--batch")
        for bat in sys.argv[idx + 1:]:
            if os.path.isfile(bat):
                changed = add_icon_field(bat, ICON_PATH)
                out("  %s %s" % ("OK 已加 ICON= 字段：" if changed
                                 else "- 已有 ICON= 字段，跳过：", bat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
