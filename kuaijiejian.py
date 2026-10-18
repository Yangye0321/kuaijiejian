# -*- coding: utf-8 -*-
"""快捷键管理工具（Windows / Python 3.9 + tkinter，零第三方依赖）

功能：
  1. 自定义「触发键 → 输出组合键」规则，按一个键代替一串组合键。
     例：小键盘 . → Alt+Ctrl+小键盘0
  2. 「替代模式 / 正常输入」用一个开关热键切换（默认启动为正常输入）。
  3. 生效范围可按 exe 白名单限定（例如只对网易云 cloudmusic.exe 生效）。
     例：触发键 F8 → 网易云「喜欢」全局快捷键，只在网易云前台时替代。

实现要点：
  - WH_KEYBOARD_LL 底层键盘钩子在独立线程 + 消息循环中运行，钩子回调内直接 SendInput，
    保证注入事件与物理按键事件的先后顺序正确（否则可能出现「键抬起早于键按下」的错序）。
  - 注入事件带固定签名（dwExtraInfo），钩子识别后直接放行，杜绝自触发死循环。
  - 只吞「首次 KEYDOWN」，KEYUP 永远透传，保证不卡键、不连发。
  - 所有 Win32 调用都用 try/except 包裹，任何异常都降级为「不吞键」，绝不卡死键盘。

命令行：
  python kuaijiejian.py                 启动图形界面
  python kuaijiejian.py --check-config  校验配置文件并打印摘要
  python kuaijiejian.py --simulate      用假按键跑一遍规则，打印决策与注入序列（不真的注入）
  python kuaijiejian.py --list-keys     打印内置键名表
  python kuaijiejian.py --probe-app     等待 3 秒后打印当前前台进程名
  python kuaijiejian.py --config X.json 指定配置文件
"""

import argparse
import ctypes
import json
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

APP_NAME = "快捷键管理工具"
APP_VERSION = "1.0.1"
APP_TITLE = "%s v%s" % (APP_NAME, APP_VERSION)
CONFIG_FILENAME = "config.json"
MUTEX_NAME = "Global\\kuaijiejian_tool_single_instance_v1"
MUTEX_NAME_LOCAL = "Local\\kuaijiejian_tool_single_instance_v1"

# 本工具注入事件的签名，用于识别「这不是物理按键」
INJECT_TAG = b"\x00\x00\x42\x42"
INJECT_TAG_INT = 0x42420000
INJECT_TAG_BYTES = b"\x00\x00\x42\x42"

# 日志走后台线程：键盘钩子回调里不能做同步磁盘写入（超时会被系统摘钩子）
_LOG_QUEUE: "queue.Queue" = queue.Queue(maxsize=10000)
_LOG_THREAD: Optional[threading.Thread] = None

IS_WINDOWS = os.name == "nt"
GUI_AVAILABLE = False


def setup_console_encoding() -> None:
    """让命令行输出在这些环境里也不会崩：

    * GitHub Actions / 英文 Windows 的控制台默认是 cp1252、cp437 甚至 ascii；
    * 本工具的命令行输出含中文，直接 print 会抛 UnicodeEncodeError。

    做法：能设控制台代码页就设成 UTF-8（65001），然后把 stdout/stderr
    重新配置为 UTF-8 + errors="replace" 兜底。失败也绝不抛异常。
    """
    if IS_WINDOWS:
        try:
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is None:
                continue
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is None:
                continue
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


if IS_WINDOWS:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        GUI_AVAILABLE = True
    except Exception:  # pragma: no cover - 无 tkinter 的环境
        tk = None  # type: ignore
else:  # pragma: no cover
    tk = None  # type: ignore


# ---------------------------------------------------------------------------
# 第一区：虚拟键码常量与中文键名表
# ---------------------------------------------------------------------------

# 修饰键（按键种类）
MOD_NONE = 0
MOD_VK = 1        # 形如 VK_CONTROL 的「通用修饰键」，注入前需解析成左右具体键
MOD_LSHIFT = 2
MOD_RSHIFT = 3
MOD_LCTRL = 4
MOD_RCTRL = 5
MOD_LALT = 6
MOD_RALT = 7
MOD_LWIN = 8
MOD_RWIN = 9
MOD_SHIFT = 10
MOD_CTRL = 11
MOD_ALT = 12

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_CANCEL = 0x03
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06
VK_BACK = 0x08
VK_TAB = 0x09
VK_CLEAR = 0x0C
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_KANA = 0x15
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_SELECT = 0x29
VK_PRINT = 0x2A
VK_EXECUTE = 0x2B
VK_SNAPSHOT = 0x2C
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_HELP = 0x2F
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_APPS = 0x5D
VK_SLEEP = 0x5F
VK_NUMPAD0 = 0x60
VK_NUMPAD1 = 0x61
VK_NUMPAD2 = 0x62
VK_NUMPAD3 = 0x63
VK_NUMPAD4 = 0x64
VK_NUMPAD5 = 0x65
VK_NUMPAD6 = 0x66
VK_NUMPAD7 = 0x67
VK_NUMPAD8 = 0x68
VK_NUMPAD9 = 0x69
VK_MULTIPLY = 0x6A
VK_ADD = 0x6B
VK_SEPARATOR = 0x6C
VK_SUBTRACT = 0x6D
VK_DECIMAL = 0x6E
VK_DIVIDE = 0x6F
VK_F1 = 0x70
VK_F2 = 0x71
VK_F3 = 0x72
VK_F4 = 0x73
VK_F5 = 0x74
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_F11 = 0x7A
VK_F12 = 0x7B
VK_F13 = 0x7C
VK_F14 = 0x7D
VK_F15 = 0x7E
VK_F16 = 0x7F
VK_F17 = 0x80
VK_F18 = 0x81
VK_F19 = 0x82
VK_F20 = 0x83
VK_F21 = 0x84
VK_F22 = 0x85
VK_F23 = 0x86
VK_F24 = 0x87
VK_NUMLOCK = 0x90
VK_SCROLL = 0x91
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_BROWSER_BACK = 0xA6
VK_BROWSER_FORWARD = 0xA7
VK_BROWSER_REFRESH = 0xA8
VK_BROWSER_STOP = 0xA9
VK_BROWSER_SEARCH = 0xAA
VK_BROWSER_FAVORITES = 0xAB
VK_BROWSER_HOME = 0xAC
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_LAUNCH_MAIL = 0xB4
VK_LAUNCH_MEDIA_SELECT = 0xB5
VK_LAUNCH_APP1 = 0xB6
VK_LAUNCH_APP2 = 0xB7
VK_OEM_1 = 0xBA
VK_OEM_PLUS = 0xBB
VK_OEM_COMMA = 0xBC
VK_OEM_MINUS = 0xBD
VK_OEM_PERIOD = 0xBE
VK_OEM_2 = 0xBF
VK_OEM_3 = 0xC0
VK_OEM_4 = 0xDB
VK_OEM_5 = 0xDC
VK_OEM_6 = 0xDD
VK_OEM_7 = 0xDE
VK_OEM_8 = 0xDF
VK_OEM_102 = 0xE2

# 快捷键录制时会被忽略的键（会破坏键盘/系统状态，不适合作为触发键）
FORBIDDEN_TRIGGER_VKS = {
    VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_XBUTTON1, VK_XBUTTON2,
    VK_CANCEL, VK_CLEAR, VK_HELP, VK_SLEEP, VK_SELECT, VK_EXECUTE, VK_PRINT,
}

# 需要 KEYEVENTF_EXTENDEDKEY 的键（这是 Windows 的既有约定）
EXTENDED_VKS = set(
    [
        VK_RMENU, VK_RCONTROL, VK_INSERT, VK_DELETE, VK_HOME, VK_END,
        VK_PRIOR, VK_NEXT, VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN,
        VK_NUMLOCK, VK_DIVIDE, VK_SNAPSHOT, VK_RWIN, VK_APPS,
        VK_BROWSER_BACK, VK_BROWSER_FORWARD, VK_BROWSER_REFRESH,
        VK_BROWSER_STOP, VK_BROWSER_SEARCH, VK_BROWSER_FAVORITES,
        VK_BROWSER_HOME, VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP,
        VK_MEDIA_NEXT_TRACK, VK_MEDIA_PREV_TRACK, VK_MEDIA_STOP,
        VK_MEDIA_PLAY_PAUSE, VK_LAUNCH_MAIL, VK_LAUNCH_MEDIA_SELECT,
        VK_LAUNCH_APP1, VK_LAUNCH_APP2,
    ]
)

# 通用修饰键 → 默认（左侧）具体键
GENERIC_MODIFIER_TO_VK = {
    VK_SHIFT: VK_LSHIFT,
    VK_CONTROL: VK_LCONTROL,
    VK_MENU: VK_LMENU,
}

# 全部修饰键 VK
MODIFIER_VKS = {
    VK_SHIFT, VK_CONTROL, VK_MENU,
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
    VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN,
}

# 左右修饰键按下时，系统会同时把「通用修饰键」置为按下
VK_TO_GENERIC_MODIFIER = {
    VK_LSHIFT: VK_SHIFT, VK_RSHIFT: VK_SHIFT,
    VK_LCONTROL: VK_CONTROL, VK_RCONTROL: VK_CONTROL,
    VK_LMENU: VK_MENU, VK_RMENU: VK_MENU,
}
GENERIC_TO_VARIANTS = {
    VK_SHIFT: (VK_LSHIFT, VK_RSHIFT),
    VK_CONTROL: (VK_LCONTROL, VK_RCONTROL),
    VK_MENU: (VK_LMENU, VK_RMENU),
}

# 修饰键注入排序（Win → Ctrl → Alt → Shift）
MODIFIER_DOWN_ORDER = [
    VK_LWIN, VK_RWIN,
    VK_LCONTROL, VK_RCONTROL, VK_CONTROL,
    VK_LMENU, VK_RMENU, VK_MENU,
    VK_LSHIFT, VK_RSHIFT, VK_SHIFT,
]

# NumLock 关闭时，小键盘发出的其实是导航簇 VK
NUMPAD_TO_NAV_WHEN_NUMLOCK_OFF = {
    VK_NUMPAD0: VK_INSERT,
    VK_NUMPAD1: VK_END,
    VK_NUMPAD2: VK_DOWN,
    VK_NUMPAD3: VK_NEXT,
    VK_NUMPAD4: VK_LEFT,
    VK_NUMPAD5: VK_CLEAR,
    VK_NUMPAD6: VK_RIGHT,
    VK_NUMPAD7: VK_HOME,
    VK_NUMPAD8: VK_UP,
    VK_NUMPAD9: VK_PRIOR,
    VK_DECIMAL: VK_DELETE,
}
NAV_TO_NUMPAD_WHEN_NUMLOCK_OFF = dict(
    (v, k) for k, v in NUMPAD_TO_NAV_WHEN_NUMLOCK_OFF.items()
)

_VK_NAMES: Dict[int, str] = {
    VK_BACK: "退格",
    VK_TAB: "Tab",
    VK_CLEAR: "Clear",
    VK_RETURN: "回车",
    VK_SHIFT: "Shift",
    VK_CONTROL: "Ctrl",
    VK_MENU: "Alt",
    VK_PAUSE: "Pause",
    VK_CAPITAL: "CapsLock",
    VK_KANA: "Kana",
    VK_ESCAPE: "Esc",
    VK_SPACE: "空格",
    VK_PRIOR: "PageUp",
    VK_NEXT: "PageDown",
    VK_END: "End",
    VK_HOME: "Home",
    VK_LEFT: "左方向键",
    VK_UP: "上方向键",
    VK_RIGHT: "右方向键",
    VK_DOWN: "下方向键",
    VK_SELECT: "Select",
    VK_PRINT: "Print",
    VK_EXECUTE: "Execute",
    VK_SNAPSHOT: "PrintScreen",
    VK_INSERT: "Insert",
    VK_DELETE: "Delete",
    VK_HELP: "Help",
    VK_LWIN: "左Win",
    VK_RWIN: "右Win",
    VK_APPS: "菜单键",
    VK_SLEEP: "Sleep",
    VK_NUMPAD0: "小键盘 0",
    VK_NUMPAD1: "小键盘 1",
    VK_NUMPAD2: "小键盘 2",
    VK_NUMPAD3: "小键盘 3",
    VK_NUMPAD4: "小键盘 4",
    VK_NUMPAD5: "小键盘 5",
    VK_NUMPAD6: "小键盘 6",
    VK_NUMPAD7: "小键盘 7",
    VK_NUMPAD8: "小键盘 8",
    VK_NUMPAD9: "小键盘 9",
    VK_MULTIPLY: "小键盘 *",
    VK_ADD: "小键盘 +",
    VK_SEPARATOR: "小键盘 分隔符",
    VK_SUBTRACT: "小键盘 -",
    VK_DECIMAL: "小键盘 .",
    VK_DIVIDE: "小键盘 /",
    VK_NUMLOCK: "NumLock",
    VK_SCROLL: "ScrollLock",
    VK_LSHIFT: "左Shift",
    VK_RSHIFT: "右Shift",
    VK_LCONTROL: "左Ctrl",
    VK_RCONTROL: "右Ctrl",
    VK_LMENU: "左Alt",
    VK_RMENU: "右Alt",
    VK_BROWSER_BACK: "浏览器后退",
    VK_BROWSER_FORWARD: "浏览器前进",
    VK_BROWSER_REFRESH: "浏览器刷新",
    VK_BROWSER_STOP: "浏览器停止",
    VK_BROWSER_SEARCH: "浏览器搜索",
    VK_BROWSER_FAVORITES: "浏览器收藏",
    VK_BROWSER_HOME: "浏览器主页",
    VK_VOLUME_MUTE: "静音",
    VK_VOLUME_DOWN: "音量-",
    VK_VOLUME_UP: "音量+",
    VK_MEDIA_NEXT_TRACK: "下一曲",
    VK_MEDIA_PREV_TRACK: "上一曲",
    VK_MEDIA_STOP: "停止",
    VK_MEDIA_PLAY_PAUSE: "播放/暂停",
    VK_LAUNCH_MAIL: "打开邮件",
    VK_LAUNCH_MEDIA_SELECT: "打开媒体",
    VK_LAUNCH_APP1: "启动应用1",
    VK_LAUNCH_APP2: "启动应用2",
    VK_OEM_1: "; :",
    VK_OEM_PLUS: "= +",
    VK_OEM_COMMA: ", <",
    VK_OEM_MINUS: "- _",
    VK_OEM_PERIOD: ". >",
    VK_OEM_2: "/ ?",
    VK_OEM_3: "` ~",
    VK_OEM_4: "[ {",
    VK_OEM_5: "\\ |",
    VK_OEM_6: "] }",
    VK_OEM_7: "' \"",
    VK_OEM_8: "OEM_8",
    VK_OEM_102: "OEM_102",
}
for _c in range(ord("A"), ord("Z") + 1):
    _VK_NAMES[_c] = chr(_c)
for _d in range(ord("0"), ord("9") + 1):
    _VK_NAMES[_d] = chr(_d)
for _i in range(24):
    _VK_NAMES[VK_F1 + _i] = "F%d" % (_i + 1)

NAME_TO_VK: Dict[str, int] = {}
for _vk, _name in _VK_NAMES.items():
    NAME_TO_VK[_name] = _vk
# 常见别名
_NAME_ALIASES = {
    "esc": VK_ESCAPE, "escape": VK_ESCAPE, "回车": VK_RETURN, "enter": VK_RETURN,
    "return": VK_RETURN, "空格": VK_SPACE, "space": VK_SPACE,
    "np0": VK_NUMPAD0, "np1": VK_NUMPAD1, "np2": VK_NUMPAD2, "np3": VK_NUMPAD3,
    "np4": VK_NUMPAD4, "np5": VK_NUMPAD5, "np6": VK_NUMPAD6, "np7": VK_NUMPAD7,
    "np8": VK_NUMPAD8, "np9": VK_NUMPAD9,
    "小键盘0": VK_NUMPAD0, "小键盘1": VK_NUMPAD1, "小键盘2": VK_NUMPAD2,
    "小键盘3": VK_NUMPAD3, "小键盘4": VK_NUMPAD4, "小键盘5": VK_NUMPAD5,
    "小键盘6": VK_NUMPAD6, "小键盘7": VK_NUMPAD7, "小键盘8": VK_NUMPAD8,
    "小键盘9": VK_NUMPAD9, "小键盘点": VK_DECIMAL,
    "ctrl": VK_LCONTROL, "control": VK_LCONTROL,
    "shift": VK_LSHIFT, "alt": VK_LMENU,
    "win": VK_LWIN, "numlock": VK_NUMLOCK,
}
for _a, _vk in _NAME_ALIASES.items():
    NAME_TO_VK.setdefault(_a, _vk)
    NAME_TO_VK.setdefault(_a.upper(), _vk)


def vk_to_name(vk: int, extended: bool = False) -> str:
    """虚拟键码 → 中文名。未知键回退为 VK_0x 形式。"""
    name = _VK_NAMES.get(int(vk))
    if name is None:
        return "VK_0x%02X" % (int(vk) & 0xFF)
    if extended and vk == VK_RETURN:
        return "小键盘 回车"
    return name


def name_to_vk(name: str) -> int:
    """中文/英文键名 → 虚拟键码，找不到抛 KeyError。"""
    key = name.strip()
    if key in NAME_TO_VK:
        return NAME_TO_VK[key]
    if key.upper() in NAME_TO_VK:
        return NAME_TO_VK[key.upper()]
    low = key.lower()
    for alias, vk in _NAME_ALIASES.items():
        if alias.lower() == low:
            return vk
    if low.startswith("vk_0x"):
        return int(low[5:], 16)
    raise KeyError("未知键名：%s" % name)


def is_modifier(vk: int) -> bool:
    return int(vk) in MODIFIER_VKS


def requires_extended(vk: int) -> bool:
    return int(vk) in EXTENDED_VKS


def is_injected(extra_info: int) -> bool:
    """判断事件是否由本工具注入。"""
    try:
        if int(extra_info) == INJECT_TAG_INT:
            return True
        raw = int(extra_info).to_bytes(8, "little", signed=False)[:4]
        return raw == INJECT_TAG_BYTES
    except Exception:
        return False


def normalize_trigger_vk(vk: int, numlock_on: Optional[bool],
                         follow_numpad: bool) -> int:
    """NumLock 关闭时小键盘发导航簇 VK；按小键盘语义归一化。

    例：NumLock 关时按小键盘 . 实际发 VK_DELETE → 归一化回 VK_DECIMAL。
    """
    vk = int(vk)
    if follow_numpad and numlock_on is False and vk in NAV_TO_NUMPAD_WHEN_NUMLOCK_OFF:
        return NAV_TO_NUMPAD_WHEN_NUMLOCK_OFF[vk]
    return vk


def resolve_modifier_vk(vk: int) -> int:
    """通用修饰键 → 左侧具体修饰键。"""
    return GENERIC_MODIFIER_TO_VK.get(int(vk), int(vk))


def combo_text(keys: Sequence[Dict], joiner: str = "+") -> str:
    """把键列表渲染成 "Alt+Ctrl+小键盘 0" 形式。"""
    return joiner.join(vk_to_name(int(k.get("vk", 0)), bool(k.get("extended"))) for k in keys)


_GENERIC_MOD_NAME = {
    VK_LWIN: "Win", VK_RWIN: "Win",
    VK_LCONTROL: "Ctrl", VK_RCONTROL: "Ctrl",
    VK_LMENU: "Alt", VK_RMENU: "Alt",
    VK_LSHIFT: "Shift", VK_RSHIFT: "Shift",
    VK_SHIFT: "Shift", VK_CONTROL: "Ctrl", VK_MENU: "Alt",
}


def hotkey_text(spec: Dict) -> str:
    """单个热键的显示文本（修饰键用通用名，例如 "Alt+F12"）。"""
    vk = int(spec.get("vk", 0))
    mod = _GENERIC_MOD_NAME.get(vk)
    if mod:
        return mod
    return vk_to_name(vk, bool(spec.get("extended")))


def build_output_sequence(output_keys: Sequence[Dict],
                          held_vks: Optional[Set[int]] = None,
                          hold_ms: int = 0) -> List[Dict]:
    """构造完整的注入序列（纯函数，便于单测）。

    返回 [{"vk", "extended", "up", "delay_ms"}...]。规则：
      1. 输出组合之外、用户当前按住的修饰键先强行抬起，避免干扰；
      2. 输出组合按「修饰键 Win→Ctrl→Alt→Shift → 主键」顺序按下；
      3. 主键抬起（hold_ms > 0 时先停留一会儿），再逆序抬起输出用的修饰键；
      4. 最后把第 1 步抬起的修饰键重新按下，恢复用户的长按状态。

    held_vks 为空时退化为「单纯按下再抬起全部输出键」，作为兜底。
    """
    output_keys = [dict(k) for k in output_keys if int(k.get("vk", 0)) != 0]
    if not output_keys:
        return []
    held = set(int(v) for v in (held_vks or set()))

    out_vks = set()
    for k in output_keys:
        out_vks.add(int(k["vk"]))
        generic = VK_TO_GENERIC_MODIFIER.get(int(k["vk"]))
        if generic is not None:
            out_vks.add(generic)

    extra_held = sorted(
        (v for v in held if v not in out_vks and is_modifier(v)),
        key=lambda v: MODIFIER_DOWN_ORDER.index(v)
        if v in MODIFIER_DOWN_ORDER else 99,
    )

    def key(vk: int, up: bool = False, delay: int = 0) -> Dict:
        return {
            "vk": int(vk),
            "extended": requires_extended(vk),
            "up": up,
            "delay_ms": delay,
        }

    mods = [k for k in output_keys if is_modifier(int(k["vk"]))]
    normals = [k for k in output_keys if not is_modifier(int(k["vk"]))]

    def mod_sort(k: Dict) -> int:
        vk = int(k["vk"])
        return MODIFIER_DOWN_ORDER.index(vk) if vk in MODIFIER_DOWN_ORDER else 99

    mods.sort(key=mod_sort)
    hold = max(0, int(hold_ms))

    seq: List[Dict] = []
    # 1) 抬起输出组合之外、用户正按住的修饰键
    for vk in extra_held:
        seq.append(key(vk, up=True))
    # 2) 按下输出组合的修饰键
    for k in mods:
        seq.append(key(int(k["vk"])))
    # 3) 主键：按下 →（停留 hold_ms）→ 抬起；输出只有修饰键时，最后一个修饰键充当主键
    if normals:
        for k in normals:
            seq.append(key(int(k["vk"])))
        if hold:
            # 主键按下后停留一会儿再抬起：延时挂在主键的「按下」这一步上
            for step in reversed(seq):
                if step["vk"] == int(normals[-1]["vk"]) and not step["up"]:
                    step["delay_ms"] = hold
                    break
        for k in normals:
            seq.append(key(int(k["vk"]), up=True))
    elif mods:
        seq.append(key(int(mods[-1]["vk"]), up=True))
    # 4) 逆序抬起输出组合的修饰键
    for k in reversed(mods):
        ups = set(x["vk"] for x in seq if x["up"])
        if int(k["vk"]) in ups:
            continue
        seq.append(key(int(k["vk"]), up=True))
    # 5) 恢复第 1 步抬起的修饰键
    for vk in reversed(extra_held):
        seq.append(key(vk))

    return seq


# ---------------------------------------------------------------------------
# 第二区：配置模型与读写
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict = {
    "version": 1,
    "engine_running": True,
    "mode": "normal",
    "toggle_hotkey": {"vk": VK_F12, "extended": False},
    "panic_hotkey": {"vk": VK_PAUSE, "extended": False},
    "apply_to_all_apps": False,
    "app_whitelist": ["cloudmusic.exe"],
    "numlock_off_follows_numpad": True,
    "hold_ms": 0,
    "debug_log": True,
    "log_all_keys": False,
    "debug_file_log": False,
    "debug_raw_keys": False,
    "hotkey_debounce_ms": 300,
    "mode_restore_on_start": False,
    "rules": [],
}

# 预置示例规则：默认关闭，避免一启动就改变键位
DEFAULT_RULES: List[Dict] = [
    {
        "enabled": False,
        "note": "场景A：小键盘 . → Alt+Ctrl+小键盘0",
        "trigger": {"vk": VK_DECIMAL, "extended": False},
        "output": [
            {"vk": VK_LMENU, "extended": False},
            {"vk": VK_LCONTROL, "extended": False},
            {"vk": VK_NUMPAD0, "extended": False},
        ],
        "app_filter": False,
    },
    {
        "enabled": False,
        "note": "场景B：F8 → 网易云【喜欢】(默认 Ctrl+Alt+L，请按你机器上的实际键位改)",
        "trigger": {"vk": VK_F8, "extended": False},
        "output": [
            {"vk": VK_LCONTROL, "extended": False},
            {"vk": VK_LMENU, "extended": False},
            {"vk": ord("L"), "extended": False},
        ],
        "app_filter": True,
    },
]


class ConfigError(Exception):
    pass


def _as_key_spec(raw, where: str) -> Dict:
    if not isinstance(raw, dict):
        raise ConfigError("%s 必须是对象" % where)
    try:
        vk = int(raw.get("vk"))
    except Exception:
        raise ConfigError("%s.vk 必须是整数" % where)
    if not (1 <= vk <= 0xFF):
        raise ConfigError("%s.vk 超出范围：%s" % (where, vk))
    return {"vk": vk, "extended": bool(raw.get("extended", False))}


def validate_config(cfg: Dict) -> Dict:
    """校验并补全配置，返回规范化后的新配置。出错抛 ConfigError。"""
    if not isinstance(cfg, dict):
        raise ConfigError("配置文件根节点必须是对象")
    out: Dict = {}
    out["version"] = int(cfg.get("version", 1) or 1)
    out["engine_running"] = bool(cfg.get("engine_running", True))
    mode = str(cfg.get("mode", "normal")).lower()
    out["mode"] = mode if mode in ("normal", "replace") else "normal"
    restore_mode = bool(cfg.get("mode_restore_on_start", False))
    if not restore_mode:
        # 默认「启动时总是正常输入」：替代模式只在这一会话内有效，
        # 避免上一次开启的替代模式在下次开机后意外接管键盘。
        out["mode"] = "normal"
    out["mode_restore_on_start"] = restore_mode
    out["toggle_hotkey"] = _as_key_spec(
        cfg.get("toggle_hotkey", DEFAULT_CONFIG["toggle_hotkey"]), "toggle_hotkey")
    out["panic_hotkey"] = _as_key_spec(
        cfg.get("panic_hotkey", DEFAULT_CONFIG["panic_hotkey"]), "panic_hotkey")
    out["apply_to_all_apps"] = bool(cfg.get("apply_to_all_apps", False))
    out["numlock_off_follows_numpad"] = bool(
        cfg.get("numlock_off_follows_numpad", True))
    try:
        out["hold_ms"] = max(0, min(200, int(cfg.get("hold_ms", 0) or 0)))
    except Exception:
        out["hold_ms"] = 0
    out["debug_log"] = bool(cfg.get("debug_log", True))
    out["log_all_keys"] = bool(cfg.get("log_all_keys", False))
    out["debug_file_log"] = bool(cfg.get("debug_file_log", False))
    out["debug_raw_keys"] = bool(cfg.get("debug_raw_keys", False))
    try:
        out["hotkey_debounce_ms"] = max(0, min(5000, int(
            cfg.get("hotkey_debounce_ms", 300) or 0)))
    except Exception:
        out["hotkey_debounce_ms"] = 300
    out["mode_restore_on_start"] = bool(cfg.get("mode_restore_on_start", False))

    wl = cfg.get("app_whitelist", [])
    if isinstance(wl, str):
        wl = [x for x in wl.replace(";", ",").split(",")]
    if not isinstance(wl, list):
        raise ConfigError("app_whitelist 必须是列表")
    clean_wl: List[str] = []
    for item in wl:
        name = str(item).strip().lower()
        if name and name not in clean_wl:
            clean_wl.append(name)
    out["app_whitelist"] = clean_wl

    raw_rules = cfg.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ConfigError("rules 必须是列表")
    rules: List[Dict] = []
    for i, raw in enumerate(raw_rules):
        where = "rules[%d]" % i
        if not isinstance(raw, dict):
            raise ConfigError("%s 必须是对象" % where)
        trigger = _as_key_spec(raw.get("trigger"), where + ".trigger")
        raw_output = raw.get("output")
        if not isinstance(raw_output, list) or not raw_output:
            raise ConfigError("%s.output 必须是非空列表" % where)
        if len(raw_output) > 4:
            raise ConfigError("%s.output 最多 4 个键" % where)
        output = [_as_key_spec(k, "%s.output[%d]" % (where, j))
                  for j, k in enumerate(raw_output)]
        rules.append({
            "_index": i,  # 仅供引擎内部按原始顺序比较，保存时会去掉
            "enabled": bool(raw.get("enabled", True)),
            "note": str(raw.get("note", "")),
            "trigger": trigger,
            "output": output,
            "app_filter": bool(raw.get("app_filter", False)),
        })
    out["rules"] = rules
    check_hotkey_conflicts(out)
    return out


def check_hotkey_conflicts(cfg: Dict) -> List[str]:
    """返回冲突/警告文本列表（不抛异常，供 GUI 提示）。"""
    warnings: List[str] = []
    toggles = [("开关热键", cfg["toggle_hotkey"]), ("暂停热键", cfg["panic_hotkey"])]
    for name, spec in toggles:
        for i, rule in enumerate(cfg.get("rules", [])):
            if rule["trigger"]["vk"] == spec["vk"]:
                warnings.append("%s 与第 %d 条规则的触发键（%s）相同"
                                % (name, i + 1, vk_to_name(spec["vk"])))
    if cfg["toggle_hotkey"]["vk"] == cfg["panic_hotkey"]["vk"]:
        warnings.append("开关热键与暂停热键相同")
    # 同一触发键 + 生效范围重叠
    seen: Dict[int, List[int]] = {}
    for i, rule in enumerate(cfg.get("rules", [])):
        if not rule["enabled"]:
            continue
        seen.setdefault(rule["trigger"]["vk"], []).append(i)
    for vk, idxs in seen.items():
        if len(idxs) > 1:
            has_global = any(not cfg["rules"][i]["app_filter"] for i in idxs)
            has_app = any(cfg["rules"][i]["app_filter"] for i in idxs)
            if has_global or has_app:
                warnings.append(
                    "触发键 %s 有 %d 条启用规则，生效范围重叠；"
                    "「仅本规则限定程序」的规则优先匹配"
                    % (vk_to_name(vk), len(idxs)))
    return warnings


def config_for_save(cfg: Dict) -> Dict:
    """去掉仅引擎内部使用的字段（rules[i]._index），再落盘。"""
    clean = dict(cfg)
    clean["rules"] = [
        dict((k, v) for k, v in rule.items() if not k.startswith("_"))
        for rule in cfg.get("rules", [])
    ]
    return clean


def write_json_atomic(path: str, data: Dict) -> None:
    """临时文件 + 替换，避免写入半截文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(config_for_save(data), fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_config(path: str, create_if_missing: bool = True) -> Dict:
    """读取配置；损坏时备份为 .bad-<时间戳> 并返回默认配置。"""
    if not os.path.exists(path):
        cfg = validate_config(dict(DEFAULT_CONFIG, rules=[dict(r) for r in DEFAULT_RULES]))
        if create_if_missing:
            write_json_atomic(path, cfg)
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return validate_config(raw)
    except Exception as exc:
        backup = "%s.bad-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
        try:
            os.replace(path, backup)
        except Exception:
            pass
        cfg = validate_config(dict(DEFAULT_CONFIG, rules=[dict(r) for r in DEFAULT_RULES]))
        if create_if_missing:
            try:
                write_json_atomic(path, cfg)
            except Exception:
                pass
        sys.stderr.write("配置读取失败（%s），已备份到 %s 并加载默认配置\n"
                         % (exc, backup))
        return cfg


# ---------------------------------------------------------------------------
# 第三区：Win32 API 声明
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t
    LRESULT = ctypes.c_ssize_t

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105
    WM_QUIT = 0x0012
    WM_APP = 0x8000
    WM_CMD_RELOAD = WM_APP + 1
    WM_CMD_QUIT = WM_APP + 2
    HC_ACTION = 0

    LLKHF_EXTENDED = 0x01
    LLKHF_INJECTED = 0x10
    LLKHF_LOWER_IL_INJECTED = 0x02

    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MAPVK_VK_TO_VSC = 0

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_ALREADY_EXISTS = 183
    VK_NUMLOCK = 0x90

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    # 注意：Win32 的 INPUT 结构在 x64 上是 40 字节（KEYBDINPUT 24 字节，
    # dwExtraInfo 位于偏移 16）。SendInput 的 cbSize 必须正好是 40，
    # 传 32/28/24 都会被拒绝并返回 0、GetLastError=87（参数错误）。
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _INPUTUNIONFILLER(ctypes.Structure):
        """仅用来占位：Windows 的 INPUT 结构是 40 字节。

        实测 SendInput 的 cbSize 必须是 40，传 32/28/24 都会返回 0、
        GetLastError=87（参数错误）。ctypes 里只声明 KEYBDINPUT 时结构只有
        32 字节，所以补 8 字节填充，让 sizeof(INPUT)==40。
        """
        _fields_ = [("pad", ctypes.c_byte * 32)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("filler", _INPUTUNIONFILLER)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    INPUT_KEYBOARD = 1

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", POINT),
        ]

    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM,
                                  wintypes.LPARAM)

    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                         wintypes.HINSTANCE, wintypes.DWORD]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.CallNextHookEx.restype = LRESULT
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetKeyState.restype = ctypes.c_short
    user32.GetKeyState.argtypes = [ctypes.c_int]
    user32.MapVirtualKeyW.restype = wintypes.UINT
    user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    user32.GetProcessWindowStation.argtypes = []
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetUserObjectInformationW.restype = wintypes.BOOL
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                     ctypes.c_int]
    user32.SetProcessDPIAware.restype = wintypes.BOOL
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                      wintypes.LPCWSTR]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ULONG_PTR),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]
    try:
        _QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
        _QueryFullProcessImageNameW.restype = wintypes.BOOL
        _QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
    except AttributeError:
        _QueryFullProcessImageNameW = None
    try:
        _SendInput = user32.SendInput
        _SendInput.restype = wintypes.UINT
        _SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT),
                               ctypes.c_int]
    except AttributeError:
        _SendInput = None
else:  # pragma: no cover - 非 Windows 平台只有纯逻辑可用
    user32 = None
    kernel32 = None
    _SendInput = None
    _QueryFullProcessImageNameW = None

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        pass


# ---------------------------------------------------------------------------
# 第四区：前台进程查询（带缓存）
# ---------------------------------------------------------------------------

class ForegroundApp(object):
    """查询当前前台窗口所属进程的可执行文件名，带短时缓存（性能关键路径）。"""

    def __init__(self, ttl: float = 0.5):
        self.ttl = ttl
        self._last_time = 0.0
        self._last_hwnd = 0
        self._last_name: Optional[str] = None
        self._last_pid = 0

    def _query(self) -> Tuple[int, Optional[str], int]:
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return 0, None, 0
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid = int(pid.value)
        if not pid:
            return hwnd, None, 0
        name = None
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if _QueryFullProcessImageNameW and _QueryFullProcessImageNameW(
                        handle, 0, buf, ctypes.byref(size)):
                    name = os.path.basename(buf.value)
            finally:
                kernel32.CloseHandle(handle)
        return hwnd, (name or None), pid

    def get(self) -> Tuple[Optional[str], int, int]:
        """返回 (进程名, hwnd, pid)。失败时名称为 None。"""
        if not IS_WINDOWS:
            return None, 0, 0
        now = time.time()
        if now - self._last_time < self.ttl and self._last_name is not None:
            return self._last_name, self._last_hwnd, self._last_pid
        try:
            hwnd, name, pid = self._query()
        except Exception:
            hwnd, name, pid = 0, None, 0
        self._last_time = now
        self._last_hwnd = hwnd
        self._last_pid = pid
        if name is not None:
            self._last_name = name
        return name, hwnd, pid

    def force_refresh(self) -> Tuple[Optional[str], int, int]:
        self._last_time = 0.0
        self._last_name = None
        return self.get()


def get_numlock_on() -> Optional[bool]:
    if not IS_WINDOWS:
        return None
    try:
        return bool(user32.GetKeyState(VK_NUMLOCK) & 1)
    except Exception:
        return None


def _window_class_name(hwnd: int) -> str:
    """取窗口类名，用来识别 UAC 确认框（#32770 / TaskDialog）。"""
    if not IS_WINDOWS or not hwnd:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        n = user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
        return buf.value if n else ""
    except Exception:
        return ""


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def enable_dpi_awareness() -> None:
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def describe_runtime() -> str:
    """启动环境摘要，用于对比「控制台启动能收键、pythonw 启动收不到」这类差异。"""
    parts = ["exe=%s" % (sys.executable or "?"),
             "argv0=%s" % (sys.argv[0] if sys.argv else "?"),
             "pid=%s" % os.getpid(),
             "gui=%s" % ("有 Tk" if GUI_AVAILABLE else "无 Tk")]
    if not IS_WINDOWS:
        return " ｜ ".join(parts)
    try:
        sid = ctypes.c_uint()
        kernel32.ProcessIdToSessionId(ctypes.c_uint(os.getpid()), ctypes.byref(sid))
        parts.append("session=%s" % sid.value)
    except Exception:
        pass
    try:
        h = user32.GetProcessWindowStation()
        need = ctypes.c_ulong(0)
        # UOI_NAME = 2
        user32.GetUserObjectInformationW(h, 2, None, 0, ctypes.byref(need))
        buf = ctypes.create_unicode_buffer(max(2, need.value // 2))
        if user32.GetUserObjectInformationW(h, 2, buf, need.value,
                                            ctypes.byref(need)):
            parts.append("winsta=%s" % buf.value)
        hd = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        user32.GetUserObjectInformationW(hd, 2, None, 0, ctypes.byref(need))
        buf2 = ctypes.create_unicode_buffer(max(2, need.value // 2))
        if user32.GetUserObjectInformationW(hd, 2, buf2, need.value,
                                            ctypes.byref(need)):
            parts.append("desktop=%s" % buf2.value)
    except Exception:
        pass
    try:
        parts.append("标准输出=%s" % ("有" if sys.stdout else "无"))
    except Exception:
        pass
    return " ｜ ".join(parts)


# ---------------------------------------------------------------------------
# 第五区：键盘钩子线程 + 注入
# ---------------------------------------------------------------------------

# 钩子过程中一定用得到、必须留引用的对象
_hookproc_ref = None


class HookThread(threading.Thread):
    """在独立线程里安装 WH_KEYBOARD_LL 并跑消息循环。

    handler(state, is_keydown, is_repeat) -> bool
        返回 True 表示吞掉该事件（不再传给其他程序）。
    注入在 handler 内部同步完成，保证事件顺序正确。
    """

    def __init__(self, handler, log=None):
        threading.Thread.__init__(self, name="kbd-hook", daemon=True)
        self.handler = handler
        self.log = log or (lambda msg: None)
        self.thread_id = 0
        self.hook = None
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.error: Optional[str] = None
        self._pressed: Set[int] = set()
        self._hook_calls = 0

    # -- 生命周期 ---------------------------------------------------------
    def install(self) -> bool:
        """阻塞等待钩子安装结果。"""
        self.start()
        self.ready.wait(3.0)
        return self.hook is not None

    def run(self) -> None:
        global _hookproc_ref
        self.thread_id = int(kernel32.GetCurrentThreadId())
        try:
            self._proc_ref = HOOKPROC(self._hook_proc)
            _hookproc_ref = self._proc_ref
            hmod = kernel32.GetModuleHandleW(None)
            self.hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc_ref,
                                                 hmod, 0)
            if not self.hook:
                self.error = "SetWindowsHookExW 失败，错误码 %s" % ctypes.get_last_error()
        except Exception as exc:
            self.error = str(exc)
        self.ready.set()
        if not self.hook:
            self.log("钩子安装失败：%s" % self.error)
            log_line("钩子安装失败：%s" % self.error)
            return
        log_line("键盘钩子已安装（线程 %s）" % self.thread_id)
        msg = MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self.log("消息循环异常：%s" % exc)
            log_line("钩子消息循环异常：%s" % exc)
        finally:
            self.uninstall()
            self.stopped.set()
            log_line("键盘钩子已卸载")

    def uninstall(self) -> None:
        try:
            if self.hook:
                user32.UnhookWindowsHookEx(self.hook)
        except Exception:
            pass
        self.hook = None

    def request_stop(self) -> None:
        try:
            user32.PostThreadMessageW(self.thread_id, WM_CMD_QUIT, 0, 0)
        except Exception:
            pass

    def reload(self) -> None:
        """让钩子线程回到消息循环顶部（用于处理配置变更后的无操作同步）。"""
        try:
            user32.PostThreadMessageW(self.thread_id, WM_CMD_RELOAD, 0, 0)
        except Exception:
            pass

    def is_alive_hook(self) -> bool:
        return bool(self.hook) and self.is_alive() and not self.stopped.is_set()

    def call_count(self) -> int:
        return self._hook_calls

    # -- 钩子过程 ---------------------------------------------------------
    def _hook_proc(self, ncode, wparam, lparam):
        if ncode != HC_ACTION:
            return user32.CallNextHookEx(None, ncode, wparam, lparam)
        self._hook_calls += 1
        try:
            state = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            message = int(wparam)
            is_down = message in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = message in (WM_KEYUP, WM_SYSKEYUP)
            if is_down or is_up:
                # lParam 的 bit30 = 上一次按键状态：为 1 说明这是长按自动重复。
                # 只有「按下」才有自动重复的概念，抬起一定是 False，
                # 否则长按后松手永远不会触发「松开才切换」。
                is_repeat = bool((int(lparam) >> 30) & 1) if is_down else False
                swallow = bool(self.handler(state, is_down, is_repeat))
                if swallow:
                    return 1
        except Exception as exc:  # 绝不因为异常卡键盘
            self.log("钩子回调异常（已放行该按键）：%s" % exc)
        return user32.CallNextHookEx(None, ncode, wparam, lparam)


class KeySender(object):
    """SendInput 封装；带签名，注入事件会被钩子识别后放行。"""

    def __init__(self, log=None):
        self.log = log or (lambda msg: None)
        self._lock = threading.Lock()
        self.last_error: Optional[str] = None
        size = ctypes.sizeof(INPUT)
        if size != 40:
            # 结构布局不对时提前暴露问题，避免「按键没反应但看不出原因」
            self.last_error = ("INPUT 结构大小异常（%d，Win32 应为 40），"
                               "注入可能不可用。" % size)

    def send(self, sequence: Sequence[Dict]) -> Tuple[int, int]:
        """执行注入序列，返回 (成功发送的事件数, 总事件数)。"""
        if not IS_WINDOWS or not sequence:
            return 0, len(sequence or [])
        items = []
        for step in sequence:
            items.append(self._make_input(int(step["vk"]), bool(step.get("up")),
                                          bool(step.get("extended")),
                                          int(step.get("scan_only", 0))))
        return self._send_inputs(items)

    def _make_input(self, vk: int, up: bool, extended: bool,
                    scan_only: int = 0) -> "INPUT":
        flags = 0
        if up:
            flags |= KEYEVENTF_KEYUP
        if extended or requires_extended(vk):
            flags |= KEYEVENTF_EXTENDEDKEY
        scan = 0
        if scan_only:
            flags |= KEYEVENTF_SCANCODE
            scan = scan_only
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki.wVk = vk & 0xFFFF
        inp.ki.wScan = scan & 0xFFFF
        inp.ki.dwFlags = flags
        inp.ki.time = 0
        inp.ki.dwExtraInfo = INJECT_TAG_INT
        return inp

    def _send_inputs(self, items: List["INPUT"]) -> Tuple[int, int]:
        n = len(items)
        with self._lock:
            try:
                arr = (INPUT * n)(*items)
                if _SendInput is not None:
                    sent = int(_SendInput(n, arr, ctypes.sizeof(INPUT)))
                else:  # 后备方案
                    sent = self._send_keybd_event(items)
            except Exception as exc:
                self.last_error = str(exc)
                self.log("注入失败：%s" % exc)
                return 0, n
        if sent != n:
            err = ctypes.get_last_error() if IS_WINDOWS else 0
            self.last_error = ("SendInput 只发送了 %d/%d 个事件（错误码 %s）。"
                               "目标程序若以管理员身份运行，请以管理员身份重启本工具。"
                               % (sent, n, err))
            self.log(self.last_error)
        else:
            self.last_error = None
        return sent, n

    def _send_keybd_event(self, items: List["INPUT"]) -> int:
        """SendInput 不可用时的后备（老 API）。"""
        count = 0
        for inp in items:
            vk = inp.ki.wVk
            flags = inp.ki.dwFlags
            bscan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
            legacy = 0
            if flags & KEYEVENTF_EXTENDEDKEY:
                legacy |= 0x0001
            if flags & KEYEVENTF_KEYUP:
                legacy |= 0x0002
            try:
                user32.keybd_event(vk, bscan, legacy, INJECT_TAG_INT)
                count += 1
            except Exception:
                break
        return count


# ---------------------------------------------------------------------------
# 第六区：替代引擎
# ---------------------------------------------------------------------------

EVENT_IGNORED = "忽略"
EVENT_PASS = "透传"
EVENT_SWALLOW = "吞键+注入"
EVENT_MODE = "模式切换"


@dataclass
class EngineEvent:
    at: str
    text: str
    injected: str = ""
    app: str = ""
    kind: str = EVENT_PASS


class ReplacementEngine(object):
    """模式状态机 + 规则匹配 + 注入决策。纯逻辑为主，便于命令行模拟。"""

    def __init__(self, config: Dict, sender=None, app_query=None,
                 log=None, on_event=None):
        self.config = validate_config(config)
        self.sender = sender if sender is not None else KeySender(log=log)
        self.app_query = app_query if app_query is not None else ForegroundApp()
        self.log = log or (lambda msg: None)
        self.on_event = on_event or (lambda ev: None)
        self.config["mode"] = self._norm_mode(self.config.get("mode", "normal"))
        self.running = bool(self.config.get("engine_running", True))
        self.held_modifiers: Set[int] = set()
        self._last_app: Optional[str] = None
        self._last_hotkey_at = 0.0
        self._last_hotkey_vk = 0
        # 方案 A：开关热键「按下武装、松开切换」的状态
        self._toggle_armed = False
        self._toggle_armed_vk = 0
        self._toggle_armed_at = 0.0
        # 规则触发键同样「按下武装、松开注入」
        self._trigger_armed = False
        self._trigger_vk = 0
        self._trigger_rule: Optional[Dict] = None
        self.inject_count = 0
        self.swallow_count = 0
        self.last_error: Optional[str] = None
        self.hook_calls = 0

    @staticmethod
    def _norm_mode(mode) -> str:
        return "replace" if mode == "replace" else "normal"

    # 模式状态保存进 self.config，保证「切换模式」和「保存配置」用的是同一份状态。
    # 历史缺陷：模式只存在引擎的独立属性里，界面一旦保存配置，
    # apply_config 就会用配置文件里的旧 mode 把它覆盖回去（表现为按开关热键没反应）。
    @property
    def mode(self) -> str:
        return self._norm_mode(self.config.get("mode", "normal"))

    @mode.setter
    def mode(self, value) -> None:
        self.config["mode"] = self._norm_mode(value)

    # -- 配置 -------------------------------------------------------------
    def apply_config(self, config: Dict) -> None:
        """应用新配置，但保留当前模式（模式是运行状态，不该被旧文件覆盖）。"""
        mode = self.mode
        cfg = validate_config(config)
        cfg["mode"] = mode
        self.config = cfg
        self.running = bool(cfg.get("engine_running", True))
        # 热键可能变了：清掉「按下已武装」的残留状态
        self.reset_hotkey_state()

    def set_mode(self, mode: str) -> str:
        self.mode = mode
        log_line("模式切换为：%s" % ("替代模式" if self.mode == "replace"
                                     else "正常输入"))
        return self.mode

    def toggle_mode(self) -> str:
        return self.set_mode("normal" if self.mode == "replace" else "replace")

    # -- 对外状态 ---------------------------------------------------------
    def status(self) -> Dict:
        app = getattr(self.app_query, "_last_name", None)
        return {
            "mode": self.mode,
            "running": self.running,
            "app": app,
            "numlock": get_numlock_on(),
            "hook_calls": self.hook_calls,
            "injected": self.inject_count,
            "swallowed": self.swallow_count,
            "error": self.last_error,
        }

    # -- 规则匹配 ---------------------------------------------------------
    def os_hotkey_trigger(self, vk: int) -> Optional[str]:
        """判断是否命中开关/暂停热键，返回 'toggle'/'panic'/None。"""
        if int(vk) == int(self.config["toggle_hotkey"]["vk"]):
            return "toggle"
        if int(vk) == int(self.config["panic_hotkey"]["vk"]):
            return "panic"
        return None

    def _remember_hotkey_press(self, vk: int) -> None:
        self._last_hotkey_vk = int(vk)
        self._last_hotkey_at = time.time()

    def reset_hotkey_state(self) -> None:
        """清空热键的「武装」状态。

        钩子重装、切换热键、重新加载配置时都要调用：
        否则一次没等到 KEYUP 的旧按压状态会残留，导致下一次按键行为怪异。
        """
        self._toggle_armed = False
        self._toggle_armed_vk = 0
        self._toggle_armed_at = 0.0
        self._trigger_armed = False
        self._trigger_vk = 0
        self._trigger_rule = None
        self._last_hotkey_vk = 0
        self._last_hotkey_at = 0.0

    def _match_rule(self, trigger_vk: int, app_name: Optional[str]):
        """匹配规则，返回 (命中索引, 原因)。索引 -1 表示没有这条触发键的规则。

        原因用于日志：「未限定程序」的规则因为前台程序不在白名单里被卡住时，
        要让用户一眼看懂为什么按了没反应。
        """
        app = (app_name or "").lower()
        wl = [str(x).lower() for x in self.config.get("app_whitelist", [])]
        scoped_hit = -1
        scoped_vks: List[int] = []
        global_first = -1
        for i, rule in enumerate(self.config.get("rules", [])):
            if not rule.get("enabled"):
                continue
            if int(rule["trigger"]["vk"]) != int(trigger_vk):
                continue
            if rule.get("app_filter"):
                scoped_vks.append(i)
                if app and app in wl:
                    if scoped_hit < 0:
                        scoped_hit = i
                continue
            if global_first < 0:
                global_first = i
        if scoped_hit >= 0:
            return scoped_hit, ""
        if global_first >= 0:
            if self.config.get("apply_to_all_apps"):
                return global_first, ""
            if not wl:
                return -1, "白名单为空且未勾选「所有程序」"
            return -1, ("前台是 %s，不在白名单（%s）里"
                        % (app_name or "未知", ", ".join(wl)))
        if scoped_vks:
            return -1, ("该触发键的规则只对白名单程序生效，"
                        "前台是 %s（白名单：%s）"
                        % (app_name or "未知", ", ".join(wl) or "空"))
        return -1, ""

    def find_rule(self, trigger_vk: int, app_name: Optional[str]) -> Optional[Dict]:
        """先匹配「限定程序」的规则，再匹配全局规则；没有命中返回 None。"""
        idx, _ = self._match_rule(trigger_vk, app_name)
        if idx < 0:
            return None
        rules = self.config.get("rules", [])
        return rules[idx] if 0 <= idx < len(rules) else None

    def app_allowed(self) -> bool:
        if self.config.get("apply_to_all_apps"):
            return True
        return bool(self.config.get("app_whitelist"))

    # -- 钩子入口 ---------------------------------------------------------
    def on_hook(self, state, is_down: bool, is_repeat: bool) -> bool:
        self.hook_calls += 1
        try:
            vk = int(state.vkCode)
            extended = bool(int(state.flags) & LLKHF_EXTENDED)
            injected = bool(int(state.flags) & LLKHF_INJECTED) or is_injected(
                int(state.dwExtraInfo))
            extra = int(state.dwExtraInfo)
        except Exception as exc:
            self.log("读取按键结构失败：%s" % exc)
            return False

        if injected:
            # 本工具自己注入的事件：直接放行，绝不参与匹配（防自触发死循环）
            return False

        # 维护按住的修饰键集合
        if is_modifier(vk):
            if is_down:
                if not is_repeat:
                    self.held_modifiers.add(vk)
            else:
                self.held_modifiers.discard(vk)
                for gen, variants in GENERIC_TO_VARIANTS.items():
                    if vk in variants:
                        self.held_modifiers.discard(gen)

        hot = self.os_hotkey_trigger(vk)
        # 前台进程名按需查询：这是钩子回调里唯一可能较慢的系统调用（OpenProcess），
        # 开关/暂停热键的判定不需要它，所以先判热键、再查进程，能省就省。
        need_app = bool(hot) or bool(self.config.get("debug_raw_keys")) \
            or self.mode == "replace"
        app_name = self._last_app
        if need_app or app_name is None:
            try:
                info = self.app_query.get()
                app_name = info[0] if info else None
            except Exception:
                app_name = None
            self._last_app = app_name

        if self.config.get("debug_raw_keys"):
            # 原始键码记录：用来确认「你按的键到底被系统识别成什么」
            log_line("原始按键：vk=0x%02X(%d) %s｜%s｜扩展键=%s｜repeat=%s｜武装=%s｜前台=%s"
                     % (vk, vk, vk_to_name(vk, extended),
                        "按下" if is_down else "抬起", extended, is_repeat,
                        self._toggle_armed, app_name or "?"))

        # ---- 开关热键：按下只「武装」，松开才真正切换（方案 A）----------
        # 这样无论长按几秒、系统补发多少个 KEYDOWN（哪怕不带 repeat 标记），
        # 一次物理按压都只会切换一次模式，且完全不依赖 Windows 的 repeat 标记。
        if hot == "toggle":
            if is_down:
                if is_repeat:
                    # 正常长按的连发：已经武装过就忽略。
                    if self._toggle_armed:
                        self._emit(EVENT_MODE, "长按 %s：自动重复已忽略（等松开才切换）"
                                   % vk_to_name(vk), "", app_name)
                        return True
                    # 关键修复：走到这里说明「按下」那一次事件丢了
                    # （典型场景：关掉工具时手指还按着热键，抬起发生在旧进程退出之后，
                    #  新进程只看到长按连发）。把连发当作这次按住的起点武装起来，
                    # 这样松开时仍然会恰好切换一次。
                    if self._toggle_armed_at and (
                            time.time() - self._toggle_armed_at > 5.0):
                        # 很久以前的残留武装：那是丢掉的抬起，先解除，避免误判
                        log_line("检测到超时的按键武装状态（抬起事件可能丢失），已自动解除")
                        self._toggle_armed = False
                        self._toggle_armed_vk = 0
                    self._toggle_armed = True
                    self._toggle_armed_vk = vk
                    self._toggle_armed_at = time.time()
                    self._emit(EVENT_MODE,
                               "检测到 %s 正处于长按（按下事件丢失），"
                               "松开时切换模式" % vk_to_name(vk), "", app_name)
                    return True
                if not self._toggle_armed:
                    if self._toggle_armed_at and (
                            time.time() - self._toggle_armed_at > 5.0):
                        log_line("检测到超时的按键武装状态（抬起事件可能丢失），已自动解除")
                        self._toggle_armed = False
                        self._toggle_armed_vk = 0
                    self._toggle_armed = True
                    self._toggle_armed_vk = vk
                    self._toggle_armed_at = time.time()
                    self._emit(EVENT_MODE, "%s 已按下，松开时切换模式"
                               % vk_to_name(vk), "", app_name)
                else:
                    self._emit(EVENT_MODE, "%s 仍被按住，重复按下已忽略"
                               % vk_to_name(vk), "", app_name)
                return True
            # 抬起：只有真正武装过的那次按压才切换
            if self._toggle_armed and (
                    not self._toggle_armed_vk or self._toggle_armed_vk == vk):
                self._toggle_armed = False
                self._toggle_armed_vk = 0
                self._toggle_armed_at = 0.0
                self._remember_hotkey_press(vk)
                self.set_mode("replace" if self.mode == "normal" else "normal")
                self._emit(EVENT_MODE, "开关热键：%s"
                           % ("进入替代模式" if self.mode == "replace"
                              else "回到正常输入"), "", app_name)
                return True
            return True

        # ---- 暂停热键：按下即生效（应急永远立即响应）----------------------
        if hot == "panic":
            if is_down and not is_repeat:
                self._remember_hotkey_press(vk)
                self.set_mode("normal")
                self._emit(EVENT_MODE, "暂停热键：切回正常输入", "", app_name)
            return True

        # ---- 规则触发键：与开关热键同一套「按下武装、松开才动作」----------
        # 以前是按下就注入，长按会被系统连发带着反复注入组合键；
        # 现在按下只记住这次按压（并吞掉按键），松开时才注入一次。
        if not is_down:
            if self._trigger_armed and (
                    not self._trigger_vk or self._trigger_vk == vk):
                self._trigger_armed = False
                self._trigger_vk = 0
                if self._trigger_rule is None:
                    return True
                return self._fire(self._trigger_rule, vk, app_name)
            return False

        if self.mode != "replace" or not self.running:
            self._maybe_log_pass(vk, app_name, "当前是正常输入" if self.running
                                 else "替代引擎已停用")
            return False

        numlock = get_numlock_on()
        trigger_vk = normalize_trigger_vk(
            vk, numlock, bool(self.config.get("numlock_off_follows_numpad", True)))
        idx, reason = self._match_rule(trigger_vk, app_name)
        if idx < 0:
            if trigger_vk != vk:
                reason = "（按 NumLock 关闭归一化成 %s）%s" % (
                    vk_to_name(trigger_vk), reason)
            self._maybe_log_pass(vk, app_name, reason)
            return False

        rule = self.config["rules"][idx]
        if is_repeat:
            # 长按的连发：已经武装过就忽略，保证只注入一次
            if self._trigger_armed and self._trigger_vk == vk:
                self._emit(EVENT_MODE, "长按 %s：自动重复已忽略（松开才触发一次）"
                           % vk_to_name(vk), "", app_name)
                return True
            # 走到这里说明最初那次「按下」丢了（例如工具刚启动、或旧进程刚退出），
            # 把连发当作这次按住的起点，松开时照样触发一次
            self._trigger_armed = True
            self._trigger_vk = vk
            self._trigger_rule = rule
            self._emit(EVENT_MODE, "检测到 %s 正处于长按（按下事件丢失），"
                                   "松开时触发一次" % vk_to_name(vk), "", app_name)
            return True

        if self._trigger_armed and self._trigger_vk == vk:
            self._emit(EVENT_MODE, "%s 仍被按住，重复按下已忽略"
                       % vk_to_name(vk), "", app_name)
            return True

        self._trigger_armed = True
        self._trigger_vk = vk
        self._trigger_rule = rule
        self._emit(EVENT_PASS, "触发键 %s 已按下，松开时注入 %s"
                   % (vk_to_name(vk), combo_text(rule["output"])), "", app_name)
        return True

    def _fire(self, rule: Dict, trigger_vk: int, app_name: Optional[str]) -> bool:
        try:
            sequence = build_output_sequence(
                rule["output"], self.held_modifiers,
                int(self.config.get("hold_ms", 0)))
        except Exception as exc:
            self.last_error = "构造注入序列失败：%s" % exc
            self.log(self.last_error)
            return False

        inject_seq = sequence
        try:
            sent, total = self.sender.send(inject_seq)
        except Exception as exc:
            # 注入层异常也必须吞掉该键并继续运行，绝不能让钩子崩掉
            sent, total = 0, len(inject_seq)
            self.last_error = "注入失败：%s" % exc
            self.log(self.last_error)
        self.inject_count += sent
        self.swallow_count += 1
        text = "触发 %s → %s" % (vk_to_name(trigger_vk),
                               combo_text(rule["output"]))
        if sent != total:
            text += "（注入不完整 %d/%d）" % (sent, total)
            self.last_error = getattr(self.sender, "last_error", None)
            self.log("注入失败：%s" % (self.last_error or "未知原因"))
        self._emit(EVENT_SWALLOW, text,
                   " ".join(_step_text(s) for s in inject_seq), app_name,
                   swallow=True)
        return True

    def _maybe_log_pass(self, vk: int, app_name: Optional[str],
                        note: str = "") -> None:
        if not self.config.get("debug_log", True):
            return
        # 「没有命中任何规则」的按键默认不记录，否则日志会被正常打字刷满；
        # 但带原因（例如前台程序不在白名单）的一定记录，这正是排查改键没反应的线索。
        if not note and not self.config.get("log_all_keys"):
            return
        text = "透传 %s" % vk_to_name(vk, False)
        if note:
            text += "｜不生效原因：%s" % note
        self._emit(EVENT_PASS, text, "", app_name)

    def _emit(self, kind: str, text: str, injected: str = "",
              app: Optional[str] = None, swallow: bool = False) -> None:
        if not self.config.get("debug_log", True) and not swallow:
            return
        ev = EngineEvent(at=time.strftime("%H:%M:%S"), text=text,
                         injected=injected, app=app or "", kind=kind)
        if self.config.get("debug_file_log"):
            line = "[%s] %s" % (kind, text)
            if injected:
                line += "｜注入：%s" % injected
            if app:
                line += "｜前台：%s" % app
            log_line(line)
        try:
            self.on_event(ev)
        except Exception:
            pass


def _step_text(step: Dict) -> str:
    arrow = "↓" if step.get("up") else "↑"
    return "%s%s" % (vk_to_name(int(step["vk"]), bool(step.get("extended"))), arrow)


def describe_sequence(sequence: Sequence[Dict]) -> str:
    return " ".join(_step_text(s) for s in sequence)


# ---------------------------------------------------------------------------
# 第七区：桌面控制器（把引擎挂到钩子线程上）
# ---------------------------------------------------------------------------

@dataclass
class HookStatus:
    hook_ok: bool = False
    hook_calls: int = 0
    mode: str = "normal"
    running: bool = True
    app: Optional[str] = None
    numlock: Optional[bool] = None
    injected: int = 0
    swallowed: int = 0
    error: Optional[str] = None


class DesktopController(object):
    """持有钩子线程与引擎，供 GUI 与命令行共用。"""

    def __init__(self, config: Dict, log=None):
        self.log = log or (lambda msg: None)
        self.engine = ReplacementEngine(config, sender=KeySender(log=self.log),
                                        log=self.log)
        self.hook: Optional[HookThread] = None
        self._status = HookStatus(mode=self.engine.mode)
        self._pending_hook = False
        self._retry_at = 0.0
        self._started_at = 0.0
        self._last_hook_ok = False
        self._last_hook_calls = 0
        self._last_heartbeat = 0.0
        # 「静默失效」看门狗
        self._last_stall_calls = 0
        self._last_stall_at = time.time()
        self.on_hook_state = None

    # -- 启停 -------------------------------------------------------------
    def start(self) -> bool:
        if not IS_WINDOWS:
            self.log("非 Windows 平台：键盘钩子不可用")
            return False
        if self.hook and self.hook.is_alive_hook():
            return True
        hook = HookThread(self.engine.on_hook, log=self.log)
        self.engine.reset_hotkey_state()
        ok = hook.install()
        self.hook = hook
        self._status.hook_ok = ok
        self._pending_hook = not ok
        if not ok:
            self.log("钩子安装失败：%s" % (hook.error or "未知原因"))
        self._started_at = time.time()
        return ok

    def stop(self) -> None:
        if self.hook:
            self.hook.request_stop()
            self.hook.join(1.5)
            self.hook.uninstall()
        self.hook = None
        self._status.hook_ok = False

    def apply_config(self, config: Dict) -> None:
        self.engine.apply_config(config)

    def set_mode(self, mode: str) -> str:
        return self.engine.set_mode(mode)

    # -- 状态 -------------------------------------------------------------
    def refresh_status(self) -> HookStatus:
        st = self._status
        eng = self.engine
        st.hook_ok = bool(self.hook and self.hook.is_alive_hook())
        st.hook_calls = eng.hook_calls
        st.mode = eng.mode
        st.running = eng.running
        st.numlock = get_numlock_on()
        try:
            st.app = eng.app_query.get()[0]
        except Exception:
            st.app = None
        st.injected = eng.inject_count
        st.swallowed = eng.swallow_count
        st.error = eng.last_error or (self.hook.error if self.hook else None)
        # 钩子状态变化要留痕：这是「按了没反应」最关键的线索
        if st.hook_ok != self._last_hook_ok:
            if st.hook_ok:
                log_line("钩子运行中（累计回调 %d 次）" % st.hook_calls)
            else:
                log_line("⚠ 钩子已停止运行（累计回调 %d 次，原因：%s）"
                         % (st.hook_calls, self.hook.error if self.hook
                            and self.hook.error else "被系统或安全软件移除"))
            self._last_hook_ok = st.hook_ok
            if self.on_hook_state is not None:
                try:
                    self.on_hook_state(st.hook_ok)
                except Exception:
                    pass
        # 心跳：把「钩子回调次数」定期写进日志。
        # 这能区分两种完全不同的故障：
        #   次数在涨  → 钩子在收键，是规则/注入的问题；
        #   次数不动  → 你的按键根本没到这个进程（安全软件/别的按键工具抢钩子，
        #               或你按的是另一个窗口/另一个进程）。
        now = time.time()
        if st.hook_ok and now - self._last_heartbeat >= 10.0:
            self._last_heartbeat = now
            calls = st.hook_calls
            if calls == self._last_hook_calls:
                log_line("心跳：钩子运行中，但累计只收到 %d 个按键（PID %s）。"
                         "如果你正在按键，说明按键没有到达这个进程。"
                         % (calls, os.getpid()))
            else:
                log_line("心跳：钩子运行中，累计收到 %d 个按键（PID %s）"
                         % (calls, os.getpid()))
            self._last_hook_calls = calls
        if not st.hook_ok and not st.error and IS_WINDOWS:
            st.error = ("键盘钩子当前未在运行（%s）。请点右下角「自检」或运行 "
                        "diagnose-kuaijiejian.bat 查看原因。"
                        % (self.hook.error if self.hook and self.hook.error
                           else "可能被安全软件拦截"))
        # 钩子丢失时自动重装（心跳）
        if IS_WINDOWS and not st.hook_ok and not self._pending_hook \
                and time.time() - self._retry_at > 3.0:
            self._retry_at = time.time()
            self.log("检测到钩子不在运行，尝试重装…")
            self.start()
        # 「静默失效」看门狗：Windows 判定低层钩子回调超时后，会把钩子从链上
        # 摘掉，而且不会通知我们（句柄也不会变成 NULL）。这时程序看着一切正常，
        # 却再也收不到任何按键。判断依据：已经收到过按键、但计数长时间不再增长。
        if st.hook_ok:
            if st.hook_calls != self._last_stall_calls:
                self._last_stall_calls = st.hook_calls
                self._last_stall_at = now
            elif self._last_stall_calls > 0 and \
                    now - self._last_stall_at > 25.0 and \
                    now - self._retry_at > 25.0:
                log_line("⚠ 钩子静默失效：已收到过 %d 个按键，但 25 秒内计数没再增长"
                         "（可能被系统按超时摘掉）。正在重装钩子…"
                         % self._last_stall_calls)
                self._retry_at = now
                self._last_stall_at = now
                self.restart_hook()
        return st

    def restart_hook(self) -> bool:
        """强制卸载并重新安装钩子（用于静默失效后的自愈）。"""
        try:
            if self.hook:
                self.hook.request_stop()
                self.hook.join(1.0)
                self.hook.uninstall()
            self.hook = None
        except Exception:
            pass
        self._last_hook_ok = False
        self._last_stall_calls = 0
        self._last_stall_at = time.time()
        ok = self.start()
        log_line("钩子重装%s" % ("成功" if ok else "失败"))
        return ok


# ---------------------------------------------------------------------------
# 第八区：单实例
# ---------------------------------------------------------------------------

def find_tool_instances() -> List[Tuple[int, str]]:
    """列出正在运行的本工具进程 (pid, exe名)，用于提示残留实例。

    通过逐个进程读取可执行路径，判断是不是 python/pythonw，
    再尝试读取命令行来确认跑的是本脚本；读不到命令行时只按进程名提示。
    """
    if not IS_WINDOWS:
        return []
    result: List[Tuple[int, str]] = []
    me = os.getpid()
    try:
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == wintypes.HANDLE(-1).value:
            return []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                name = str(entry.szExeFile)
                pid = int(entry.th32ProcessID)
                if pid != me and name.lower() in ("python.exe", "pythonw.exe"):
                    result.append((pid, name))
                ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snap)
    except Exception:
        return result
    return result


def acquire_single_instance():
    """返回 (handle, already_running)。"""
    if not IS_WINDOWS:
        return None, False
    for name in (MUTEX_NAME, MUTEX_NAME_LOCAL):
        try:
            handle = kernel32.CreateMutexW(None, False, name)
            if handle and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                return handle, True
        except Exception:
            continue
    return None, False


# ---------------------------------------------------------------------------
# 第九区：图形界面
# ---------------------------------------------------------------------------

class RecordDialog(object):
    """按键录制对话框：按下你要用的键，实时显示。"""

    def __init__(self, parent, title: str, hint: str, allow_combo: bool):
        self.result: Optional[List[Dict]] = None
        self.allow_combo = allow_combo
        self.cancelled = False
        self.keys: List[Dict] = []
        self._note = ""

        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.transient(parent)
        self.top.grab_set()
        self.top.resizable(False, False)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.top.attributes("-topmost", True)

        ttk.Label(self.top, text=hint, wraplength=420).pack(
            padx=14, pady=(12, 6), anchor="w")
        self.var_show = tk.StringVar(value="请按下按键…")
        lbl = ttk.Label(self.top, textvariable=self.var_show, font=("Microsoft YaHei", 15),
                        foreground="#1a6", anchor="center")
        lbl.pack(padx=14, pady=10, fill="x")
        self.var_note = tk.StringVar(value="")
        ttk.Label(self.top, textvariable=self.var_note, foreground="#c60",
                  wraplength=420).pack(padx=14, anchor="w")

        btns = ttk.Frame(self.top)
        btns.pack(padx=14, pady=12, fill="x")
        ttk.Button(btns, text="重录", command=self._clear).pack(side="left")
        ttk.Button(btns, text="清除", command=self._clear_and_blank).pack(
            side="left", padx=6)
        ttk.Button(btns, text="确定", command=self._ok).pack(side="right")
        ttk.Button(btns, text="取消", command=self._cancel).pack(
            side="right", padx=6)

        self.top.bind("<KeyPress>", self._on_key)

    def _refresh_note(self) -> None:
        if not self.keys:
            self.var_note.set(self._note or "")
            return
        if self.allow_combo:
            if len(self.keys) == 1:
                self.var_note.set("提示：只录一个键也可以，这样就等于按同一个键。")
            else:
                self.var_note.set("已录 %d 个键（最多 4 个）" % len(self.keys))
        else:
            self.var_note.set(self._note or "")

    def _on_key(self, event) -> str:
        if event.keysym == "Escape" and not self.keys:
            self._cancel()
            return "break"
        vk = int(event.keycode)
        if vk == VK_ESCAPE and not self.keys:
            self._cancel()
            return "break"
        spec = {"vk": vk, "extended": bool(requires_extended(vk))}
        self._note = ""
        if self.allow_combo:
            if len(self.keys) >= 4:
                self._note = "最多 4 个键，已忽略：%s" % vk_to_name(vk)
            elif all(k["vk"] != vk for k in self.keys):
                self.keys.append(spec)
        else:
            if self.keys:
                self._note = "触发键只能一个键：已用 %s 替换 %s" % (
                    vk_to_name(vk), vk_to_name(self.keys[-1]["vk"]))
            self.keys = [spec]
        self.var_show.set(combo_text(self.keys) if self.keys else "请按下按键…")
        self._refresh_note()
        return "break"

    def _clear(self) -> None:
        self.keys = []
        self._note = ""
        self.var_show.set("请按下按键…")
        self._refresh_note()

    def _clear_and_blank(self) -> None:
        self.keys = []
        self._note = ""
        self.var_show.set("（已清空）")
        self.var_note.set("确认后该位置将为空")

    def _ok(self) -> None:
        if not self.keys:
            messagebox.showwarning(APP_NAME, "还没有录制任何按键。", parent=self.top)
            return
        self.result = list(self.keys)
        self.top.destroy()

    def _cancel(self) -> None:
        self.cancelled = True
        self.result = None
        self.top.destroy()

    def show(self) -> Optional[List[Dict]]:
        self.top.wait_window()
        return self.result

    @staticmethod
    def record(parent, title: str, hint: str,
               allow_combo: bool) -> Optional[List[Dict]]:
        return RecordDialog(parent, title, hint, allow_combo).show()


class App(object):
    """主窗口。"""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = load_config(config_path)
        self.events: "queue.Queue" = queue.Queue()
        self.controller = DesktopController(
            self.config, log=self._thread_log)
        self.controller.on_hook_state = self._on_hook_state
        self.selected_index: Optional[int] = None
        self._log_lines: List[str] = []
        self._selfcheck_lines: List[str] = []
        self._selfcheck_until = 0.0
        self._last_poll_error_at = 0.0
        self._build_ui()
        self.controller.engine.on_event = self._on_engine_event
        self.controller.start()
        self._poll()

    # -- 线程安全日志 -----------------------------------------------------
    def _thread_log(self, msg: str) -> None:
        self.events.put(("log", msg))

    def _on_engine_event(self, ev: EngineEvent) -> None:
        self.events.put(("event", ev))

    def _on_hook_state(self, alive: bool) -> None:
        if alive:
            self._append_log("键盘钩子运行中")
        else:
            self._append_log("⚠ 键盘钩子已停止运行！工具会自动尝试重装；"
                             "若反复出现，多半是安全软件在拦，请加入白名单。")

    # -- 界面 -------------------------------------------------------------
    def _build_ui(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1000x720")
        self.root.minsize(880, 620)
        try:
            self.root.option_add("*Font", ("Microsoft YaHei", 9))
        except Exception:
            pass

        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except Exception:
            pass

        # 状态栏
        status = ttk.LabelFrame(self.root, text="状态")
        status.pack(fill="x", padx=10, pady=(10, 6))
        self.var_mode = tk.StringVar(value="正常输入")
        self.var_num = tk.StringVar(value="-")
        self.var_app = tk.StringVar(value="-")
        self.var_hook = tk.StringVar(value="-")
        self.var_first_key = tk.StringVar(value="")
        self.lbl_mode = ttk.Label(status, textvariable=self.var_mode,
                                  font=("Microsoft YaHei", 11, "bold"))
        self.lbl_mode.grid(row=0, column=0, padx=10, pady=6, sticky="w")
        ttk.Label(status, textvariable=self.var_num).grid(
            row=0, column=1, padx=10, sticky="w")
        ttk.Label(status, textvariable=self.var_app).grid(
            row=0, column=2, padx=10, sticky="w")
        ttk.Label(status, textvariable=self.var_hook).grid(
            row=0, column=3, padx=10, sticky="w")
        self.lbl_first_key = ttk.Label(status, textvariable=self.var_first_key,
                                       font=("Microsoft YaHei", 10, "bold"))
        self.lbl_first_key.grid(row=0, column=4, padx=10, sticky="w")
        self.var_hint = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.var_hint, foreground="#c00",
                  wraplength=940).grid(row=1, column=0, columnspan=5,
                                       padx=10, pady=(0, 6), sticky="w")

        # 模式控制
        mode = ttk.LabelFrame(self.root, text="替代模式开关")
        mode.pack(fill="x", padx=10, pady=6)
        self.btn_toggle = ttk.Button(mode, text="切换到替代模式",
                                     command=self.on_toggle_mode)
        self.btn_toggle.pack(side="left", padx=8, pady=8)
        ttk.Button(mode, text="回到正常输入",
                   command=lambda: self.set_mode("normal")).pack(
            side="left", padx=4, pady=8)
        ttk.Label(mode, text="开关热键：").pack(side="left", padx=(16, 2))
        self.btn_toggle_hot = ttk.Button(
            mode, text="", width=22, command=self.on_set_toggle_hotkey)
        self.btn_toggle_hot.pack(side="left", padx=2)
        ttk.Label(mode, text="暂停热键：").pack(side="left", padx=(16, 2))
        self.btn_panic_hot = ttk.Button(
            mode, text="", width=22, command=self.on_set_panic_hotkey)
        self.btn_panic_hot.pack(side="left", padx=2)
        self.var_running = tk.BooleanVar(value=bool(self.config.get("engine_running", True)))
        ttk.Checkbutton(mode, text="启用替代引擎", variable=self.var_running,
                        command=self.on_toggle_running).pack(side="left", padx=16)

        # 规则表
        rules = ttk.LabelFrame(self.root, text="替代规则（触发键 → 输出组合）")
        rules.pack(fill="both", expand=True, padx=10, pady=6)
        cols = ("enabled", "trigger", "output", "scope", "note")
        self.tree = ttk.Treeview(rules, columns=cols, show="headings",
                                 selectmode="browse", height=9)
        for col, text, width in (
                ("enabled", "启用", 50), ("trigger", "触发键", 120),
                ("output", "输出组合", 230), ("scope", "生效范围", 240),
                ("note", "备注", 240)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb = ttk.Scrollbar(rules, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y", pady=8, padx=(0, 8))
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self.on_edit_rule())

        btns = ttk.Frame(rules)
        btns.pack(side="right", fill="y", padx=8, pady=8)
        for text, cmd in (("新增规则", self.on_add_rule),
                          ("编辑", self.on_edit_rule),
                          ("删除", self.on_delete_rule),
                          ("上移", lambda: self.on_move(-1)),
                          ("下移", lambda: self.on_move(1)),
                          ("全部启用", lambda: self.on_set_all_enabled(True)),
                          ("全部停用", lambda: self.on_set_all_enabled(False)),
                          ("选中规则改为所有程序生效", self.on_make_global)):
            ttk.Button(btns, text=text, width=12, command=cmd).pack(pady=3)

        # 生效范围
        scope = ttk.LabelFrame(self.root, text="生效范围（仅指定程序生效）")
        scope.pack(fill="x", padx=10, pady=6)
        left = ttk.Frame(scope)
        left.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        self.var_all_apps = tk.BooleanVar(value=bool(self.config.get("apply_to_all_apps")))
        ttk.Checkbutton(left, text="所有程序都生效（不推荐，容易误伤输入）",
                        variable=self.var_all_apps,
                        command=self.on_toggle_all_apps).pack(anchor="w")
        ttk.Label(left, text="程序白名单（进程名，例如 cloudmusic.exe；"
                             "规则勾选「限定程序」时按此名单判断）：").pack(
            anchor="w", pady=(6, 2))
        self.list_apps = tk.Listbox(left, height=4, selectmode="extended")
        self.list_apps.pack(fill="x")
        row = ttk.Frame(left)
        row.pack(fill="x", pady=4)
        self.var_new_app = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_new_app, width=28).pack(side="left")
        ttk.Button(row, text="添加", command=self.on_add_app).pack(side="left", padx=4)
        ttk.Button(row, text="删除选中", command=self.on_del_app).pack(side="left")
        ttk.Button(row, text="取当前前台进程名",
                   command=self.on_grab_app).pack(side="left", padx=4)

        right = ttk.LabelFrame(scope, text="选项")
        right.pack(side="right", fill="y", padx=8, pady=8)
        self.var_numpad = tk.BooleanVar(
            value=bool(self.config.get("numlock_off_follows_numpad", True)))
        ttk.Checkbutton(right, text="NumLock 关闭时按小键盘语义匹配\n"
                                    "（小键盘 . = 小键盘 .，而不是 Delete）",
                        variable=self.var_numpad,
                        command=self.on_toggle_numpad).pack(anchor="w", pady=2)
        self.var_debug = tk.BooleanVar(value=bool(self.config.get("debug_log", True)))
        ttk.Checkbutton(right, text="记录调试日志", variable=self.var_debug,
                        command=self.on_toggle_debug).pack(anchor="w", pady=2)
        self.var_log_all = tk.BooleanVar(
            value=bool(self.config.get("log_all_keys", False)))
        ttk.Checkbutton(right, text="记录所有按键（含没命中的，排查用，很啰嗦）",
                        variable=self.var_log_all,
                        command=self.on_toggle_log_all).pack(anchor="w", pady=2)
        self.var_filelog = tk.BooleanVar(
            value=bool(self.config.get("debug_file_log", False)))
        ttk.Checkbutton(right, text="把按键日志写进 startup.log（事后可查）",
                        variable=self.var_filelog,
                        command=self.on_toggle_filelog).pack(anchor="w", pady=2)
        self.var_rawkeys = tk.BooleanVar(
            value=bool(self.config.get("debug_raw_keys", False)))
        ttk.Checkbutton(right, text="记录原始键码（确认系统把你的键识别成什么）",
                        variable=self.var_rawkeys,
                        command=self.on_toggle_rawkeys).pack(anchor="w", pady=2)
        row2 = ttk.Frame(right)
        row2.pack(anchor="w", pady=2)
        ttk.Label(row2, text="按键按住时长(ms)：").pack(side="left")
        self.var_hold = tk.StringVar(value=str(self.config.get("hold_ms", 0)))
        ttk.Spinbox(row2, from_=0, to=60, width=5, textvariable=self.var_hold,
                    command=self.on_hold_changed).pack(side="left")
        row3 = ttk.Frame(right)
        row3.pack(anchor="w", pady=2)
        ttk.Label(row3, text="热键去抖(ms，长按只算一次)：").pack(side="left")
        self.var_debounce = tk.StringVar(
            value=str(self.config.get("hotkey_debounce_ms", 300)))
        ttk.Spinbox(row3, from_=0, to=2000, width=6,
                    textvariable=self.var_debounce,
                    command=self.on_debounce_changed).pack(side="left")
        self.var_restore_mode = tk.BooleanVar(
            value=bool(self.config.get("mode_restore_on_start", False)))
        ttk.Checkbutton(right, text="启动时恢复上次的模式（默认启动总是「正常输入」）",
                        variable=self.var_restore_mode,
                        command=self.on_toggle_restore_mode).pack(anchor="w", pady=2)
        ttk.Label(right, text="开关热键：按下→松开时切换一次（长按不会重复切换）",
                  foreground="#666", wraplength=260).pack(anchor="w", pady=2)
        ttk.Button(right, text="网易云「喜欢」怎么设？",
                   command=self.on_help_cloudmusic).pack(anchor="w", pady=(8, 2))
        ttk.Button(right, text="自检：钩子有没有在收键？",
                   command=self.on_self_check).pack(anchor="w", pady=2)
        ttk.Button(right, text="打开配置文件所在目录",
                   command=self.on_open_folder).pack(anchor="w", pady=2)

        # 日志
        logf = ttk.LabelFrame(self.root, text="调试日志（最近 200 条）")
        logf.pack(fill="both", expand=True, padx=10, pady=(6, 4))
        self.txt_log = tk.Text(logf, height=8, wrap="none")
        self.txt_log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb2 = ttk.Scrollbar(logf, orient="vertical", command=self.txt_log.yview)
        sb2.pack(side="right", fill="y", padx=(0, 8), pady=8)
        self.txt_log.configure(yscrollcommand=sb2.set, state="disabled")

        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        self.var_admin = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.var_admin, foreground="#888").pack(side="left")
        ttk.Button(bottom, text="保存配置", command=self.save_config).pack(side="right")
        ttk.Button(bottom, text="重新加载配置",
                   command=self.on_reload).pack(side="right", padx=6)
        ttk.Button(bottom, text="退出", command=self.on_quit).pack(side="right")

        self.root.protocol("WM_DELETE_WINDOW", self.on_quit)
        self.root.bind("<Escape>", lambda e: None)
        self._refresh_hotkey_buttons()
        self._refresh_app_list()
        self.refresh_rules()
        if IS_WINDOWS and not is_admin():
            self.var_admin.set("提示：目标程序若以管理员运行，需以管理员身份重启本工具才能注入。")

    # -- 渲染 -------------------------------------------------------------
    def _refresh_hotkey_buttons(self) -> None:
        self.btn_toggle_hot.configure(
            text="%s（点击修改）" % hotkey_text(self.config["toggle_hotkey"]))
        self.btn_panic_hot.configure(
            text="%s（点击修改）" % hotkey_text(self.config["panic_hotkey"]))

    def _refresh_app_list(self) -> None:
        self.list_apps.delete(0, "end")
        for item in self.config.get("app_whitelist", []):
            self.list_apps.insert("end", item)

    def refresh_rules(self) -> None:
        self.tree.delete(*self.tree.get_children())
        all_apps = bool(self.config.get("apply_to_all_apps"))
        wl = "、".join(self.config.get("app_whitelist", [])) or "白名单为空"
        for i, rule in enumerate(self.config.get("rules", [])):
            if rule.get("app_filter"):
                scope = "仅本规则限定：%s" % wl
            elif all_apps:
                scope = "所有程序"
            else:
                scope = "跟随全局白名单：%s" % wl
            self.tree.insert(
                "", "end", iid=str(i),
                values=("是" if rule.get("enabled") else "否",
                        vk_to_name(rule["trigger"]["vk"]),
                        combo_text(rule["output"]), scope, rule.get("note", "")))
        warnings = check_hotkey_conflicts(self.config)
        if not self.config.get("app_whitelist") and not all_apps:
            warnings = warnings + ["白名单为空且未勾选「所有程序」：替代不会生效"]
        self.var_hint.set("；".join(warnings) if warnings else "")
        self._refresh_warnings_color()

    def _refresh_warnings_color(self) -> None:
        self.var_hint.set(self.var_hint.get())

    # -- 动作 -------------------------------------------------------------
    def _current_index(self) -> Optional[int]:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _rule_dialog(self, rule: Optional[Dict]) -> Optional[Dict]:
        dlg = tk.Toplevel(self.root)
        dlg.title("新增规则" if rule is None else "编辑规则")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)
        data = dict(rule) if rule else {
            "enabled": True, "note": "", "app_filter": True,
            "trigger": {"vk": VK_F8, "extended": False},
            "output": [{"vk": VK_LCONTROL, "extended": False},
                       {"vk": VK_LMENU, "extended": False}],
        }
        state = {"trigger": dict(data["trigger"]), "output": [dict(k) for k in data["output"]]}
        result: Dict = {}

        var_trigger = tk.StringVar()
        var_output = tk.StringVar()
        var_note = tk.StringVar(value=data.get("note", ""))
        var_enabled = tk.BooleanVar(value=bool(data.get("enabled", True)))
        # 生效范围三选一
        var_scope = tk.StringVar(
            value="scoped" if data.get("app_filter")
            else ("global" if self.var_all_apps.get() else "whitelist"))
        var_scope_hint = tk.StringVar()

        def sync() -> None:
            var_trigger.set(vk_to_name(state["trigger"]["vk"],
                                       state["trigger"].get("extended")))
            var_output.set(combo_text(state["output"]))

        def rec_trigger() -> None:
            got = RecordDialog.record(
                dlg, "录制触发键",
                "按下你要用作「触发键」的那一个键。\n"
                "例如：小键盘 . 、F8。\n（按 Esc 取消录制）", allow_combo=False)
            if got:
                state["trigger"] = got[0]
                sync()

        def rec_output() -> None:
            got = RecordDialog.record(
                dlg, "录制输出组合",
                "按住修饰键的同时按主键，例如 Alt+Ctrl+小键盘0。\n"
                "最多 4 个键；只按一个键也可以。\n（按 Esc 取消录制）",
                allow_combo=True)
            if got:
                state["output"] = got[:4]
                sync()

        sync()
        rows = ttk.Frame(dlg)
        rows.pack(padx=14, pady=12, fill="x")
        ttk.Label(rows, text="触发键（按一下就代替后面这串）：").grid(
            row=0, column=0, sticky="w", pady=4)
        ttk.Entry(rows, textvariable=var_trigger, width=26,
                  state="readonly").grid(row=0, column=1, pady=4)
        ttk.Button(rows, text="录制", command=rec_trigger).grid(
            row=0, column=2, padx=6)
        ttk.Label(rows, text="输出组合：").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(rows, textvariable=var_output, width=26,
                  state="readonly").grid(row=1, column=1, pady=4)
        ttk.Button(rows, text="录制", command=rec_output).grid(
            row=1, column=2, padx=6)
        ttk.Label(rows, text="备注：").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(rows, textvariable=var_note, width=40).grid(
            row=2, column=1, columnspan=2, sticky="we", pady=4)
        ttk.Checkbutton(rows, text="启用本规则", variable=var_enabled).grid(
            row=3, column=1, sticky="w", pady=4)

        scope_box = ttk.LabelFrame(rows, text="生效范围（决定按了有没有反应）")
        scope_box.grid(row=4, column=0, columnspan=3, sticky="we", pady=(8, 4))
        ttk.Radiobutton(
            scope_box, value="global", variable=var_scope,
            text="所有程序都生效（全局：不管前台是什么程序都替换）"
        ).pack(anchor="w", padx=6, pady=1)
        ttk.Radiobutton(
            scope_box, value="whitelist", variable=var_scope,
            text="只在「生效范围」白名单里的程序生效（跟随上方全局设置）"
        ).pack(anchor="w", padx=6, pady=1)
        ttk.Radiobutton(
            scope_box, value="scoped", variable=var_scope,
            text="只在白名单里的程序生效，且本规则单独限定（规则优先匹配）"
        ).pack(anchor="w", padx=6, pady=1)
        ttk.Label(scope_box, textvariable=var_scope_hint, foreground="#c60",
                  wraplength=520).pack(anchor="w", padx=6, pady=(2, 4))

        def sync_scope_hint(*_args) -> None:
            wl = ", ".join(self.config.get("app_whitelist", [])) or "（空）"
            scope = var_scope.get()
            if scope == "global":
                var_scope_hint.set("✓ 任何程序前台时都会替换。")
            elif scope == "whitelist":
                var_scope_hint.set("✓ 只在以下程序前台时替换：%s\n"
                                   "（「所有程序」勾选时这里会变成全局）" % wl)
            else:
                var_scope_hint.set("✓ 只在以下程序前台时替换：%s\n"
                                   "（本规则会优先于同触发键的全局规则）" % wl)

        var_scope.trace_add("write", sync_scope_hint)
        sync_scope_hint()

        def ok() -> None:
            if not state["output"]:
                messagebox.showwarning(APP_NAME, "请先录制输出组合。", parent=dlg)
                return
            if state["trigger"]["vk"] in FORBIDDEN_TRIGGER_VKS:
                messagebox.showwarning(
                    APP_NAME, "该键不适合作为触发键，请换一个。", parent=dlg)
                return
            scope = var_scope.get()
            result.update({
                "enabled": bool(var_enabled.get()),
                "note": var_note.get().strip(),
                "app_filter": scope == "scoped",
                "trigger": dict(state["trigger"]),
                "output": [dict(k) for k in state["output"]],
            })
            if scope == "global":
                # 由调用方在确认后打开全局开关（取消时不能生效）
                result["_make_global"] = True
            elif scope in ("whitelist", "scoped") and not self.config.get(
                    "app_whitelist"):
                messagebox.showwarning(
                    APP_NAME,
                    "白名单是空的，这条规则不会在任何程序里生效。\n"
                    "请先在主界面的白名单里添加程序名（例如 cloudmusic.exe）。",
                    parent=dlg)
                return
            dlg.destroy()

        bar = ttk.Frame(dlg)
        bar.pack(padx=14, pady=(0, 12), fill="x")
        ttk.Button(bar, text="确定", command=ok).pack(side="right")
        ttk.Button(bar, text="取消", command=dlg.destroy).pack(side="right", padx=6)
        dlg.wait_window()
        return result or None

    def _apply_rule_make_global(self, rule: Dict) -> None:
        """规则被设成「所有程序都生效」时，同时打开全局开关。"""
        if rule.pop("_make_global", False):
            self.config["apply_to_all_apps"] = True
            self.var_all_apps.set(True)

    def on_add_rule(self) -> None:
        rule = self._rule_dialog(None)
        if rule:
            self._apply_rule_make_global(rule)
            self.config["rules"].append(rule)
            self.save_config()
            self.refresh_rules()
            self.tree.selection_set(str(len(self.config["rules"]) - 1))

    def on_edit_rule(self) -> None:
        idx = self._current_index()
        if idx is None:
            messagebox.showinfo(APP_NAME, "请先在列表中选择一条规则。")
            return
        rule = self._rule_dialog(self.config["rules"][idx])
        if rule:
            self._apply_rule_make_global(rule)
            self.config["rules"][idx] = rule
            self.save_config()
            self.refresh_rules()
            self.tree.selection_set(str(idx))

    def on_delete_rule(self) -> None:
        idx = self._current_index()
        if idx is None:
            messagebox.showinfo(APP_NAME, "请先在列表中选择一条规则。")
            return
        if not messagebox.askyesno(APP_NAME, "确定删除选中的规则吗？"):
            return
        del self.config["rules"][idx]
        self.tree.selection_remove(*self.tree.selection())
        self.save_config()
        self.refresh_rules()

    def on_move(self, delta: int) -> None:
        idx = self._current_index()
        if idx is None:
            return
        new = idx + delta
        rules = self.config["rules"]
        if not (0 <= new < len(rules)):
            return
        rules[idx], rules[new] = rules[new], rules[idx]
        self.save_config()
        self.refresh_rules()
        self.tree.selection_set(str(new))

    def on_set_all_enabled(self, enabled: bool) -> None:
        for rule in self.config["rules"]:
            rule["enabled"] = enabled
        self.save_config()
        self.refresh_rules()

    def on_make_global(self) -> None:
        """把选中的规则改成「所有程序都生效」。

        对应「在网易云里能用、切到别的程序就不行」这种情况：
        规则原来勾了「仅本规则限定程序」，前台不是白名单程序时自然不替换。
        """
        idx = self._current_index()
        if idx is None:
            messagebox.showinfo(APP_NAME, "请先在列表中选择一条规则。")
            return
        self.config["rules"][idx]["app_filter"] = False
        self.config["apply_to_all_apps"] = True
        self.var_all_apps.set(True)
        self.save_config()
        self.refresh_rules()
        self.tree.selection_set(str(idx))
        messagebox.showinfo(
            APP_NAME,
            "已把这条规则改成「所有程序都生效」，并打开了全局的「所有程序」开关。\n"
            "现在它在任何程序前台时都会替换。若只想在部分程序里生效，"
            "请改用「编辑」里的白名单选项。")
        self._append_log("规则 %d 改为所有程序生效（app_filter=False, apply_to_all_apps=True）"
                         % (idx + 1))

    def _run_action(self, name: str, func) -> None:
        """执行界面动作：任何异常都记日志并提示，绝不让 Tk 回调抛异常。

        Tk 的回调里抛异常会让主循环停下来（窗口看着还在、实际已死），
        这正是「按了没反应」这类问题最难查的形态。
        """
        try:
            func()
        except Exception as exc:
            import traceback
            log_line("界面动作[%s]异常：%s\n%s"
                     % (name, exc, traceback.format_exc()))
            try:
                self._append_log("⚠ 操作「%s」出错：%s（已记入 startup.log）"
                                 % (name, exc))
                messagebox.showerror(APP_NAME, "操作「%s」出错：\n%s" % (name, exc))
            except Exception:
                pass

    def on_toggle_mode(self) -> None:
        self._run_action("切换模式", self._do_toggle_mode)

    def _do_toggle_mode(self) -> None:
        self.set_mode("replace" if self.config.get("mode") != "replace" else "normal")

    def set_mode(self, mode: str) -> None:
        self.config["mode"] = mode
        self.controller.set_mode(mode)
        self.save_config(silent=True)

    def on_toggle_running(self) -> None:
        self.config["engine_running"] = bool(self.var_running.get())
        self.controller.apply_config(self.config)
        self.save_config()

    def on_toggle_all_apps(self) -> None:
        self.config["apply_to_all_apps"] = bool(self.var_all_apps.get())
        self.save_config()

    def on_toggle_numpad(self) -> None:
        self.config["numlock_off_follows_numpad"] = bool(self.var_numpad.get())
        self.save_config()

    def on_toggle_debug(self) -> None:
        self.config["debug_log"] = bool(self.var_debug.get())
        self.save_config()

    def on_toggle_log_all(self) -> None:
        self.config["log_all_keys"] = bool(self.var_log_all.get())
        self.save_config()

    def on_toggle_filelog(self) -> None:
        self.config["debug_file_log"] = bool(self.var_filelog.get())
        self.save_config()
        self._append_log("按键日志写入 startup.log：%s"
                         % ("开" if self.config["debug_file_log"] else "关"))

    def on_toggle_rawkeys(self) -> None:
        self.config["debug_raw_keys"] = bool(self.var_rawkeys.get())
        self.save_config()
        self._append_log("记录原始键码：%s（按一下你的键，再去 startup.log 看 vk=0x??）"
                         % ("开" if self.config["debug_raw_keys"] else "关"))

    def on_hold_changed(self) -> None:
        try:
            value = max(0, min(60, int(self.var_hold.get())))
        except Exception:
            value = 0
        self.config["hold_ms"] = value
        self.save_config()

    def on_debounce_changed(self) -> None:
        try:
            value = max(0, min(2000, int(self.var_debounce.get())))
        except Exception:
            value = 300
        self.config["hotkey_debounce_ms"] = value
        self.save_config()
        self._append_log("热键去抖设为 %d ms（开关/暂停热键长按只算一次）" % value)

    def on_toggle_restore_mode(self) -> None:
        self.config["mode_restore_on_start"] = bool(self.var_restore_mode.get())
        self.save_config()
        self._append_log("启动时恢复上次模式：%s"
                         % ("开" if self.config["mode_restore_on_start"] else "关"))

    def on_add_app(self) -> None:
        name = self.var_new_app.get().strip().lower()
        if not name:
            return
        if name not in self.config["app_whitelist"]:
            self.config["app_whitelist"].append(name)
        self.var_new_app.set("")
        self.save_config()
        self._refresh_app_list()

    def on_del_app(self) -> None:
        for i in reversed(self.list_apps.curselection()):
            del self.config["app_whitelist"][i]
        self.save_config()
        self._refresh_app_list()

    def on_grab_app(self) -> None:
        """取前台进程名。

        注意：点这个按钮的瞬间前台窗口是**本工具自己**，直接取只会拿到
        pythonw.exe。所以这里改成：先提示，等 3 秒让你切到目标程序，再取一次。
        """
        self._append_log("取前台进程名：请在 3 秒内点一下/切到目标程序窗口…")
        self.var_hint.set("取前台进程名：3 秒内切到目标程序窗口（别停在工具窗口上）…")
        self.root.iconify()          # 把自己最小化，避免它一直占着前台
        self.root.after(3000, self._do_grab_app_delayed)

    def _do_grab_app_delayed(self) -> None:
        # 先取前台！此时工具仍是最小化状态，前台就是用户切过去的目标程序。
        # 如果先 deiconify/lift，前台会立刻变回工具自己，永远取到 pythonw.exe。
        name = None
        hwnd = 0
        pid = 0
        try:
            name, hwnd, pid = self.controller.engine.app_query.force_refresh()
        except Exception as exc:
            log_line("取前台进程名失败：%s" % exc)
        window_class = _window_class_name(hwnd) if hwnd else ""
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass
        log_line("取前台进程：name=%s hwnd=%s pid=%s class=%s"
                 % (name, hwnd, pid, window_class or "?"))

        if window_class in ("#32770", "TaskDialog") and not name:
            messagebox.showinfo(
                APP_NAME,
                "当前前台是一个系统确认框（可能正是 UAC「用户账户控制」弹窗），"
                "取不到目标程序。\n\n请先点「是/否」把那个确认框处理掉，"
                "回到目标程序后再点一次这个按钮。")
            self.var_hint.set("")
            return
        if not name:
            extra = ("\n\n（已取到窗口 hwnd=%s pid=%s，但没有权限查询它的程序名——"
                     "它很可能以管理员身份运行。用管理员身份启动本工具后就能取到。）"
                     % (hwnd, pid)) if hwnd else ""
            messagebox.showinfo(APP_NAME, "没取到前台进程名。\n"
                                          "请先切到目标程序窗口，再回来点这个按钮。" + extra)
            self.var_hint.set("")
            return
        if name.lower() in ("python.exe", "pythonw.exe"):
            messagebox.showinfo(
                APP_NAME,
                "取到的是 %s，这看起来还是本工具自己（或另一个 Python 程序），"
                "不是你的目标程序。\n\n用法：点这个按钮后，3 秒内切到目标程序"
                "（例如网易云）窗口，等工具自动取回结果。" % name)
            self.var_hint.set("")
            return
        self.var_new_app.set(name)
        self._append_log("当前前台进程：%s（hwnd=%s pid=%s，可点「添加」加入白名单）"
                         % (name, hwnd, pid))
        self.var_hint.set("已取到前台进程：%s，点「添加」加入白名单" % name)

    def on_set_toggle_hotkey(self) -> None:
        self._run_action("设置开关热键", self._do_set_toggle_hotkey)

    def _do_set_toggle_hotkey(self) -> None:
        got = RecordDialog.record(self.root, "设置开关热键",
                                  "按一下你要用来切换「替代模式 / 正常输入」的键。\n"
                                  "默认 F12；长按只算一次。", allow_combo=False)
        if got:
            self.config["toggle_hotkey"] = got[0]
            self.controller.engine.reset_hotkey_state()
            self._refresh_hotkey_buttons()
            self.save_config()

    def on_set_panic_hotkey(self) -> None:
        self._run_action("设置暂停热键", self._do_set_panic_hotkey)

    def _do_set_panic_hotkey(self) -> None:
        got = RecordDialog.record(self.root, "设置暂停热键",
                                  "按一下应急回到「正常输入」的键。\n默认 Pause。",
                                  allow_combo=False)
        if got:
            self.config["panic_hotkey"] = got[0]
            self.controller.engine.reset_hotkey_state()
            self._refresh_hotkey_buttons()
            self.save_config()

    def on_help_cloudmusic(self) -> None:
        messagebox.showinfo(
            "网易云「喜欢」设置步骤",
            "1) 打开网易云音乐 → 右上角齿轮「设置」→ 左侧「快捷键」；\n"
            "2) 勾选「启用全局快捷键」（不勾选的话，注入的组合键只在网易云窗口聚焦时有效）；\n"
            "3) 找到「喜欢」那一项，记下它当前的组合键（默认通常是 Ctrl+Alt+L）；\n"
            "4) 回到本工具 →「新增规则」→ 触发键录制成你顺手的单键（例如 F8）→ "
            "输出组合按第 3 步实际键位录一遍；\n"
            "5) 勾选「仅本规则限定程序」，并把 cloudmusic.exe 加进白名单；\n"
            "6) 保存后按开关热键进入替代模式，回到网易云按 F8 试试。\n\n"
            "参考：网易云全局快捷键必须含 Ctrl/Alt/Shift 之一 + 一个字母。")

    def on_open_folder(self) -> None:
        try:
            os.startfile(os.path.dirname(os.path.abspath(self.config_path)))
        except Exception as exc:
            messagebox.showerror(APP_NAME, "打开目录失败：%s" % exc)

    def on_self_check(self) -> None:
        """自检：钩子是否在收键 + 让用户按 15 秒看每一步判定。"""
        self._run_action("自检", self._do_self_check)

    def _do_self_check(self) -> None:
        st = self.controller.refresh_status()
        if not st.hook_ok:
            messagebox.showerror(
                APP_NAME,
                "键盘钩子当前没有在运行。\n\n"
                "可能原因：安全软件拦截、系统资源不足，或程序被挂起。\n"
                "处理：把本程序加入杀软白名单后重启；或退出后用命令行运行\n"
                "    python kuaijiejian.py --diag\n"
                "看详细诊断信息。")
            return
        if not messagebox.askyesno(
                APP_NAME,
                "键盘钩子运行正常。\n\n现在开始 15 秒按键观察：\n"
                "点「是」之后，请在 15 秒内按几下你要用的键"
                "（开关热键、规则触发键都按一下）。\n"
                "结束后会给出结论，并说明每个键为什么生效 / 没生效。"):
            return
        self._selfcheck_lines = []
        self._selfcheck_until = time.time() + 15.0
        self._append_log("=== 自检开始：15 秒内请按你要用的键 ===")
        self.root.after(100, self._selfcheck_poll)

    def _selfcheck_poll(self) -> None:
        if time.time() < self._selfcheck_until:
            self.var_hint.set("自检中：请按你要用的键…（剩余 %d 秒）"
                              % max(0, int(self._selfcheck_until - time.time())))
            self.root.after(200, self._selfcheck_poll)
            return
        lines = self._selfcheck_lines
        st = self.controller.refresh_status()
        if not lines:
            body = ("15 秒内钩子一个按键事件都没收到。\n\n"
                    "说明你的按键没有到达本工具的键盘钩子，常见原因：\n"
                    "  1) 安全软件 / 键盘驱动 / 输入法拦掉了底层键盘钩子；\n"
                    "  2) 程序不在你的用户桌面会话里（切换用户或远程桌面后的残留进程）；\n"
                    "  3) 系统里已有别的键盘映射工具（AutoHotkey、游戏键盘驱动）抢占了钩子。\n\n"
                    "处理：把本程序加入杀软白名单 → 彻底退出本程序 → 重新双击启动 → 再自检。")
        else:
            body = "钩子期间收到 %d 个按键事件，逐条判定：\n\n%s\n" % (
                len(lines), "\n".join(lines[-30:]))
            if not any("触发" in ln or "切换" in ln for ln in lines):
                body += ("\n结论：钩子能收到按键，但没有一条规则被触发。\n"
                         "请看每条「不生效原因」；若你按的是开关热键却没看到「模式切换」，"
                         "说明热键键位不对（例如 Fn 锁把 F12 变成了媒体键），"
                         "请在界面上重新录制开关热键。")
            else:
                body += ("\n结论：钩子和规则都正常。若目标程序仍无反应，"
                         "多半是注入被系统拦了（目标程序以管理员运行），"
                         "请以管理员身份重启本工具。")
        self._append_log("=== 自检结束 ===")
        try:
            win = tk.Toplevel(self.root)
            win.title("自检结果")
            win.transient(self.root)
            txt = tk.Text(win, width=96, height=26, wrap="word")
            txt.insert("1.0", body)
            txt.configure(state="disabled")
            txt.pack(padx=10, pady=10)
            ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 10))
        except Exception:
            messagebox.showinfo(APP_NAME, body)

    def on_reload(self) -> None:
        self._run_action("重新加载配置", self._do_reload)

    def _do_reload(self) -> None:
        self.config = load_config(self.config_path)
        self.controller.apply_config(self.config)
        self._refresh_hotkey_buttons()
        self._refresh_app_list()
        self.refresh_rules()
        self.var_all_apps.set(bool(self.config.get("apply_to_all_apps")))
        self.var_running.set(bool(self.config.get("engine_running")))
        self.var_numpad.set(bool(self.config.get("numlock_off_follows_numpad")))
        self.var_debug.set(bool(self.config.get("debug_log")))
        self.var_log_all.set(bool(self.config.get("log_all_keys")))
        self.var_filelog.set(bool(self.config.get("debug_file_log")))
        self.var_rawkeys.set(bool(self.config.get("debug_raw_keys")))
        self.var_hold.set(str(self.config.get("hold_ms", 0)))
        self.var_restore_mode.set(bool(self.config.get("mode_restore_on_start")))
        self._append_log("已重新加载配置")

    def save_config(self, silent: bool = False) -> None:
        # 模式以引擎的运行时状态为准（F12 切换后界面配置里可能还是旧值）
        try:
            self.config["mode"] = self.controller.engine.mode
        except Exception:
            pass
        try:
            cfg = validate_config(self.config)
        except ConfigError as exc:
            messagebox.showerror(APP_NAME, "配置不合法：%s" % exc)
            return
        self.config = cfg
        try:
            write_json_atomic(self.config_path, cfg)
        except Exception as exc:
            messagebox.showerror(APP_NAME, "保存配置失败：%s" % exc)
            return
        self.controller.apply_config(cfg)
        if not silent:
            warnings = check_hotkey_conflicts(cfg)
            self.var_hint.set("；".join(warnings) if warnings else "")
            self._append_log("配置已保存：%s" % self.config_path)

    def on_quit(self) -> None:
        try:
            self.save_config(silent=True)
        except Exception:
            pass
        log_line("用户退出：正在卸载键盘钩子")
        self.controller.stop()
        log_line("键盘钩子已卸载，界面关闭")
        try:
            self.root.destroy()
        except Exception:
            pass

    # -- 轮询 -------------------------------------------------------------
    def _on_engine_event(self, ev: EngineEvent) -> None:
        if time.time() < self._selfcheck_until:
            line = "%s｜%s" % (ev.text, ev.injected or "")
            if ev.app:
                line += "｜前台：%s" % ev.app
            self._selfcheck_lines.append(line)
        self.events.put(("event", ev))

    def _append_log(self, line: str) -> None:
        self._log_lines.append(line)
        if len(self._log_lines) > 200:
            self._log_lines = self._log_lines[-200:]
        try:
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", line + "\n")
            if float(self.txt_log.index("end-1c").split(".")[0]) > 200:
                self.txt_log.delete("1.0", "20.0")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        except Exception:
            pass

    def _poll(self) -> None:
        """GUI 心跳：刷新状态、重装钩子、显示日志。

        关键约束：无论中途发生任何异常，都必须重新排下一次 _poll。
        否则一旦抛异常，界面还在但不再刷新，钩子掉线也不会被重装，
        表现就是「窗口开着，按什么键都没反应」——极难排查。
        """
        try:
            while True:
                try:
                    kind, payload = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    if kind == "log":
                        self._append_log(payload)
                    elif kind == "event":
                        ev = payload
                        extra = ("｜注入：%s" % ev.injected) if ev.injected else ""
                        app = ("｜%s" % ev.app) if ev.app else ""
                        self._append_log("[%s] %s%s%s"
                                         % (ev.at, ev.text, extra, app))
                except Exception as exc:
                    self._append_log("（处理日志条目失败：%s）" % exc)
            try:
                st = self.controller.refresh_status()
            except Exception as exc:
                st = None
                self._poll_error("刷新状态失败：%s" % exc)
            if st is not None:
                self._update_status_widgets(st)
        except Exception as exc:  # 兜底：绝不让心跳断掉
            self._poll_error("_poll 异常：%s" % exc)
        finally:
            try:
                self.root.after(150, self._poll)
            except Exception:
                pass

    def _update_status_widgets(self, st: "HookStatus") -> None:
        mode_text = ("替代模式（规则生效中）" if st.mode == "replace"
                     else "正常输入（按键原样透传）")
        if not st.running:
            mode_text += "｜引擎已停用"
        self.var_mode.set(mode_text)
        self.lbl_mode.configure(
            foreground="#c00" if st.mode == "replace" else "#060")
        self.var_num.set(
            "NumLock：%s" % ("开" if st.numlock else
                             ("关（小键盘走导航键）" if st.numlock is False
                              else "未知")))
        self.var_app.set("前台：%s" % (st.app or "-"))
        self.var_hook.set("钩子：%s｜吞键 %d｜注入 %d"
                          % ("运行中" if st.hook_ok else "未运行",
                             st.swallowed, st.injected))
        # 最直观的存活信号：钩子有没有真的收到过按键
        if st.hook_calls > 0:
            self.var_first_key.set("✓ 已收到按键（%d 次）" % st.hook_calls)
            self.lbl_first_key.configure(foreground="#060")
        elif st.hook_ok:
            self.var_first_key.set("等待第一次按键…（若一直不变，请点自检）")
            self.lbl_first_key.configure(foreground="#c60")
        else:
            self.var_first_key.set("钩子未运行")
            self.lbl_first_key.configure(foreground="#c00")
        self.btn_toggle.configure(
            text="切换到替代模式" if st.mode == "normal" else "回到正常输入")
        if st.error:
            self.var_hint.set(st.error)
        else:
            warnings = check_hotkey_conflicts(self.config)
            self.var_hint.set("；".join(warnings) if warnings else "")

    def _poll_error(self, message: str) -> None:
        """把心跳里的异常留痕（界面 + 日志文件），便于事后定位。"""
        now = time.time()
        if now - getattr(self, "_last_poll_error_at", 0.0) < 5.0:
            return
        self._last_poll_error_at = now
        log_line("心跳异常：%s" % message)
        try:
            self.var_hint.set("内部心跳异常（已记录，工具仍在运行）：%s" % message)
        except Exception:
            pass
        self._append_log("⚠ 心跳异常：%s" % message)

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self.controller.stop()


# ---------------------------------------------------------------------------
# 第十区：命令行入口
# ---------------------------------------------------------------------------

def default_config_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        CONFIG_FILENAME)


def find_bundled_python() -> Optional[str]:
    """便携版：优先使用脚本同目录（或子目录）里自带的 Python。

    这样整个文件夹拷到没装 Python 的电脑上也能直接双击运行。
    查找顺序：python\\pythonw.exe, python\\python.exe,
              pythonw.exe（同目录）, python.exe（同目录）
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for sub in ("python", "Python", "py", "runtime"):
        for exe in ("pythonw.exe", "python.exe"):
            path = os.path.join(here, sub, exe)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return path
    for exe in ("pythonw.exe", "python.exe"):
        path = os.path.join(here, exe)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    return None


def startup_log_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "startup.log")


def log_line(message: str) -> None:
    """异步写启动/运行日志（绝不阻塞调用方）。

    为什么必须异步：日志也会在**键盘钩子回调**里被调用，而 Windows 对低层钩子
    回调有时限（LowLevelHooksTimeout，默认约 300ms）；一旦超时，系统会把钩子
    从链上摘掉且不通知我们，表现为「工具还在跑，但再也收不到任何按键」。
    所以回调里只入队，真正的 open/write/close 交给后台线程做。
    """
    try:
        _LOG_QUEUE.put_nowait(message)
    except Exception:
        pass


def _log_writer_loop():
    path = startup_log_path()
    while True:
        item = _LOG_QUEUE.get()
        if item is None:
            break
        try:
            if os.path.exists(path) and os.path.getsize(path) > 2 * 1024 * 1024:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    tail = fh.readlines()[-500:]
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("[%s] （日志过大，已滚动保留末尾 %d 行）\n"
                             % (time.strftime("%Y-%m-%d %H:%M:%S"), len(tail)))
                    fh.writelines(tail)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("[%s] %s\n"
                         % (time.strftime("%Y-%m-%d %H:%M:%S"), item))
        except Exception:
            pass


def _start_log_writer():
    global _LOG_THREAD
    if _LOG_THREAD is not None and _LOG_THREAD.is_alive():
        return
    _LOG_THREAD = threading.Thread(target=_log_writer_loop, name="log-writer",
                                   daemon=True)
    _LOG_THREAD.start()


def flush_log(timeout: float = 1.5) -> None:
    """退出前把队列里的日志写完（启动/卸载这几条不能丢）。"""
    try:
        deadline = time.time() + timeout
        while not _LOG_QUEUE.empty() and time.time() < deadline:
            time.sleep(0.02)
    except Exception:
        pass


def cmd_list_keys() -> int:
    items = sorted(_VK_NAMES.items())
    for vk, name in items:
        sys.stdout.write("0x%02X  %s\n" % (vk, name))
    return 0


def cmd_check_config(path: str) -> int:
    cfg = load_config(path)
    print("版本：%s（配置格式 version=%s）" % (APP_VERSION, cfg.get("version")))
    print("配置文件：%s" % path)
    print("引擎运行：%s" % cfg["engine_running"])
    print("模式：%s" % cfg["mode"])
    print("开关热键：%s" % hotkey_text(cfg["toggle_hotkey"]))
    print("暂停热键：%s" % hotkey_text(cfg["panic_hotkey"]))
    print("所有程序生效：%s" % cfg["apply_to_all_apps"])
    print("白名单：%s" % (", ".join(cfg["app_whitelist"]) or "（空）"))
    print("规则 %d 条：" % len(cfg["rules"]))
    for i, rule in enumerate(cfg["rules"], 1):
        print("  %d. [%s] %s → %s%s"
              % (i, "启用" if rule["enabled"] else "停用",
                 vk_to_name(rule["trigger"]["vk"]), combo_text(rule["output"]),
                 "（仅限定程序）" if rule["app_filter"] else ""))
    warnings = check_hotkey_conflicts(cfg)
    if warnings:
        print("警告：")
        for w in warnings:
            print("  - %s" % w)
    return 0


def cmd_simulate(path: str) -> int:
    """用假按键跑规则，检查匹配与注入序列（不真的注入）。"""
    cfg = load_config(path, create_if_missing=False)
    print("模拟模式：以下按键会走一遍决策逻辑，但不会真的操作键盘。")
    demo_apps = cfg["app_whitelist"] or ["demo.exe"]
    for rule in cfg["rules"]:
        trigger = rule["trigger"]["vk"]
        app = demo_apps[0] if rule["app_filter"] else None
        held = set()
        for k in rule["output"]:
            vk = int(k["vk"])
            if is_modifier(vk):
                held.add(resolve_modifier_vk(vk))
        seq = build_output_sequence(rule["output"], held, cfg["hold_ms"])
        print("\n规则：%s" % (rule.get("note") or "(无备注)"))
        print("  启用：%s  限定程序：%s" % (rule["enabled"], rule["app_filter"]))
        print("  触发键：%s → 输出组合：%s"
              % (vk_to_name(trigger), combo_text(rule["output"])))
        print("  注入序列：%s" % describe_sequence(seq))
    # 用引擎做一次纯逻辑匹配
    class FakeQuery(object):
        _last_name = "demo.exe"
        def get(self):
            return "demo.exe", 0, 0
        def force_refresh(self):
            return self.get()

    engine = ReplacementEngine(cfg, sender=KeySender(), app_query=FakeQuery())
    for rule in cfg["rules"]:
        for app in ([demo_apps[0]] if rule["app_filter"] else [None, demo_apps[0]]):
            engine.mode = "replace"
            hit = engine.find_rule(int(rule["trigger"]["vk"]), app)
            mark = "命中" if hit is rule else "未命中"
            print("匹配检查：触发键 %s，前台 %s → %s"
                  % (vk_to_name(rule["trigger"]["vk"]), app or "(不限)", mark))
    print("\n提示：真实生效需要图形界面（python kuaijiejian.py）并进入替代模式。")
    return 0


def cmd_probe_app() -> int:
    if not IS_WINDOWS:
        print("仅 Windows 可用")
        return 1
    print("请在 3 秒内点一下目标程序窗口…")
    time.sleep(3)
    name, hwnd, pid = ForegroundApp(ttl=0).force_refresh()
    print("前台进程：%s（hwnd=%s pid=%s）" % (name or "(取不到)", hwnd, pid))
    return 0 if name else 1


def cmd_keys(path: str, seconds: float = 30.0) -> int:
    """键码实时显示：按下任何键都在屏幕上打印它的虚拟键码。

    用来确认「你按的键被系统识别成什么」，以及工具到底收不收得到它。
    """
    if not IS_WINDOWS:
        print("仅 Windows 可用")
        return 1
    print("=" * 70)
    print("键码实时显示：按 Ctrl+C 结束（%s 秒后自动结束）"
          % ("不限时" if seconds <= 0 else "%d" % int(seconds)))
    print("请依次按下：F12、小键盘 . 、Pause，看每个键打印出来的键码。")
    print("=" * 70)
    seen: List[Tuple[int, str]] = []

    def handler(state, is_down, is_repeat):
        if not is_down or is_repeat:
            return False
        vk = int(state.vkCode)
        extended = bool(int(state.flags) & LLKHF_EXTENDED)
        injected = bool(int(state.flags) & LLKHF_INJECTED)
        tag = "注入事件" if injected else "真实按键"
        line = "收到按键：vk=0x%02X (%d) = %s｜扩展键=%s｜%s" % (
            vk, vk, vk_to_name(vk, extended), extended, tag)
        print(line)
        sys.stdout.flush()
        seen.append((vk, line))
        return False

    hook = HookThread(handler, log=lambda m: print("[钩子] %s" % m))
    if not hook.install():
        print("钩子安装失败：%s" % (hook.error or "未知原因"))
        return 2
    print("钩子已安装，请开始按键…\n")
    deadline = time.time() + seconds if seconds > 0 else None
    try:
        while deadline is None or time.time() < deadline:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    hook.request_stop()
    hook.join(1.0)
    print("\n" + "-" * 70)
    if not seen:
        print("⚠ 一个键都没收到：按键没有到达本进程。")
        print("  可能原因：安全软件拦钩子、别的按键工具抢钩子、"
              "或者你按的是另一个窗口/另一个会话。")
        return 2
    names = ", ".join("%s(0x%02X)" % (vk_to_name(vk), vk) for vk, _ in seen)
    print("本次共收到 %d 次按键：%s" % (len(seen), names))
    codes = set(vk for vk, _ in seen)
    if VK_F12 in codes:
        print("✓ 能收到标准 F12（vk=0x7B）。若界面里按 F12 不切换，请把 startup.log 发出来。")
    else:
        print("✗ 没收到标准 F12（0x7B）——你的 F12 被键盘/系统变成了别的键。")
        print("  请把上面打印的键码告诉开发者，或直接把开关热键改成上面能收到的键。")
    return 0


def cmd_diag(path: str, seconds: float = 15.0) -> int:
    """实机诊断：装钩子 → 打印你把每个键按下去时发生了什么 → 给结论。"""
    if not IS_WINDOWS:
        print("仅 Windows 可用")
        return 1
    cfg = load_config(path)
    print("=" * 66)
    print("快捷键管理工具 诊断模式")
    print("=" * 66)
    print("配置文件：%s" % path)
    print("当前模式：%s" % ("替代模式（规则生效）" if cfg["mode"] == "replace"
                            else "正常输入（按键原样透传）"))
    print("引擎运行：%s" % cfg["engine_running"])
    print("开关热键：%s（按一下应切换模式）" % hotkey_text(cfg["toggle_hotkey"]))
    print("暂停热键：%s" % hotkey_text(cfg["panic_hotkey"]))
    print("所有程序生效：%s" % cfg["apply_to_all_apps"])
    print("白名单：%s" % (", ".join(cfg["app_whitelist"]) or "（空）"))
    for i, rule in enumerate(cfg["rules"], 1):
        scope = ("仅限定 %s" % "、".join(cfg["app_whitelist"])) if rule["app_filter"] \
            else ("所有程序" if cfg["apply_to_all_apps"] else "跟随白名单")
        print("规则 %d：[%s] %s → %s  ｜生效范围：%s"
              % (i, "启用" if rule["enabled"] else "停用",
                 vk_to_name(rule["trigger"]["vk"]), combo_text(rule["output"]),
                 scope))
    print("NumLock：%s" % ("开" if get_numlock_on() else "关"))
    print("-" * 66)

    seen: List[str] = []

    def log(msg: str) -> None:
        print("[钩子] %s" % msg)

    engine = ReplacementEngine(cfg, sender=KeySender(log=log), log=log)

    def show(ev: EngineEvent) -> None:
        line = "[%s] %s" % (ev.at, ev.text)
        if ev.injected:
            line += "｜注入：%s" % ev.injected
        if ev.app:
            line += "｜前台：%s" % ev.app
        seen.append(line)
        print(line)

    engine.on_event = show
    hook = HookThread(engine.on_hook, log=log)
    if not hook.install():
        print("\n钩子安装失败：%s" % (hook.error or "未知原因"))
        print("常见原因：安全软件拦截、系统资源不足。"
              "请把本程序加入杀软白名单后重试。")
        return 2

    print("钩子已安装。请在 %d 秒内按几下你要用的键（例如开关热键、小键盘 . ）。"
          % int(seconds))
    print("（诊断模式不真的注入按键，只报告判定结果；每次按键都会打印出来）")
    if engine.mode != "replace":
        print("注意：你现在是「正常输入」，规则不会触发；"
              "按开关热键（%s）切到替代模式再试。"
              % hotkey_text(cfg["toggle_hotkey"]))
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(0.25)
    hook.request_stop()
    hook.join(1.0)

    print("-" * 66)
    print("诊断结论：")
    print("  钩子收到的按键事件数：%d" % engine.hook_calls)
    if engine.hook_calls == 0:
        print("  ✗ 钩子一个按键都没收到 —— 你的按键没有到达本工具的键盘钩子。")
        print("    可能原因：")
        print("      1) 安全软件/键盘驱动/输入法把底层键盘钩子拦掉了；")
        print("      2) 程序不在你的用户桌面会话里（远程桌面/切换用户后的残留进程）；")
        print("      3) 系统里已有别的键盘映射工具（AutoHotkey、游戏键盘驱动）抢占了钩子。")
        print("    处理：加入杀软白名单 → 重启本程序 → 再跑一次本诊断。")
    else:
        print("  ✓ 钩子能收到按键。")
        hit = any("触发" in s for s in seen)
        print("  规则命中：%s" % ("有" if hit else "没有"))
        if not hit:
            print("  ✗ 没看到「触发」记录：可能没进替代模式，或生效范围不匹配"
                  "（上面每次按键都打印了具体原因）。")
        else:
            print("  ✓ 规则有命中。若目标程序仍无反应，多半是注入被拦"
                  "（目标程序以管理员运行），请以管理员身份重启本工具。")
    print("  （本次诊断没有真的注入按键，所以不会影响其它程序。）")
    return 0 if engine.hook_calls else 2


def cmd_watch(path: str, seconds: float = 0.0) -> int:
    """持续监听模式：一直打印每个按键的判定结果，直到 Ctrl+C。

    和 GUI 用的是同一份配置与同一套引擎，用来现场确认「这个键到底有没有被收到」。
    """
    if not IS_WINDOWS:
        print("仅 Windows 可用")
        return 1
    cfg = load_config(path)
    print("=" * 66)
    print("快捷键管理工具 持续监听模式（Ctrl+C 退出）")
    print("当前模式：%s ｜开关热键：%s ｜白名单：%s ｜所有程序生效：%s"
          % ("替代模式" if cfg["mode"] == "replace" else "正常输入",
             hotkey_text(cfg["toggle_hotkey"]),
             ", ".join(cfg["app_whitelist"]) or "（空）",
             cfg["apply_to_all_apps"]))
    print("-" * 66)

    def log(msg: str) -> None:
        print("[钩子] %s" % msg)

    engine = ReplacementEngine(cfg, sender=KeySender(log=log), log=log)

    def show(ev: EngineEvent) -> None:
        line = "[%s] %s" % (ev.at, ev.text)
        if ev.injected:
            line += "｜注入：%s" % ev.injected
        if ev.app:
            line += "｜前台：%s" % ev.app
        print(line)
        sys.stdout.flush()

    engine.on_event = show
    hook = HookThread(engine.on_hook, log=log)
    if not hook.install():
        print("钩子安装失败：%s" % (hook.error or "未知原因"))
        return 2
    print("钩子已安装。现在按你要用的键，每个按键的判定都会打印出来。")
    deadline = time.time() + seconds if seconds > 0 else None
    try:
        while deadline is None or time.time() < deadline:
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在退出…")
    hook.request_stop()
    hook.join(1.0)
    print("共收到 %d 个按键事件。" % engine.hook_calls)
    if engine.hook_calls == 0:
        print("⚠ 一个按键都没收到：安全软件/键盘驱动拦截，或已有别的按键工具抢占了钩子。")
        return 2
    return 0


def acquire_or_warn_single_instance() -> bool:
    """已经有一个实例在跑时提示并返回 False。"""
    mutex, already = acquire_single_instance()
    if not already:
        return True
    others = find_tool_instances()
    detail = ""
    if others:
        detail = ("\n\n检测到这些 Python 进程（本工具就在这个文件夹里运行）：\n"
                  + "\n".join("  %s（PID %d；启动于 %s）" % (name, pid,
                                                        _proc_start_text(pid))
                             for pid, name in others[:6])
                  + "\n\n请确认没有与「%s」同目录的旧进程；有的话先在任务管理器里结束它，"
                    "再重新启动本工具。" % os.path.dirname(os.path.abspath(__file__)))
    message = ("本工具已经在运行了，所以这次不会重复安装键盘钩子。\n"
               "如果你觉得原来那个窗口「没反应、按键无效」，请先在"
               "任务管理器里结束它，再重新启动本工具。"
               + detail)
    log_line("启动被单实例拦截：已有实例在运行；可疑进程 %s" % (others or "无"))
    print(message)
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_NAME, message)
        root.destroy()
    except Exception:
        pass
    return False


def _proc_start_text(pid: int) -> str:
    """粗略给出进程启动时间，帮助辨认残留的旧实例。"""
    if not IS_WINDOWS:
        return "未知"
    try:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return "未知"
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if kernel32.GetProcessTimes(handle, ctypes.byref(created),
                                        ctypes.byref(exited),
                                        ctypes.byref(kernel),
                                        ctypes.byref(user)):
                ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
                # FILETIME → Unix 时间
                seconds = ticks / 10000000.0 - 11644473600.0
                return time.strftime("%H:%M:%S", time.localtime(seconds))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return "未知"


def gui_failure_prompt(exc: BaseException) -> None:
    """启动失败时弹一个能读的错误框，而不是静默消失。"""
    import traceback

    text = "".join(traceback.format_exception(
        type(exc), exc, exc.__traceback__))
    log_line("启动失败：\n%s" % text)
    sys.stderr.write(text)
    try:
        root = tk.Tk()
        root.withdraw()
        box = tk.Text(root, width=100, height=24, wrap="none")
        box.insert("1.0", "%s\n\n详情已写入：\n%s\n\n%s"
                   % (exc, startup_log_path(), text))
        box.configure(state="disabled")
        box.pack()
        ttk.Button(root, text="关闭",
                   command=root.destroy).pack(pady=8)
        root.title("%s 启动失败" % APP_NAME)
        root.mainloop()
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # 必须在任何 print 之前执行：CI / 英文控制台下中文输出会 UnicodeEncodeError
    setup_console_encoding()
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--version", action="version", version=APP_TITLE,
                        help="显示版本号并退出")
    parser.add_argument("--config", default=None,
                        help="指定配置文件路径（默认脚本同目录 config.json）")
    parser.add_argument("--check-config", action="store_true",
                        help="校验配置并打印摘要")
    parser.add_argument("--simulate", action="store_true",
                        help="模拟规则匹配并打印注入序列（不做真实注入）")
    parser.add_argument("--list-keys", action="store_true", help="打印内置键名表")
    parser.add_argument("--probe-app", action="store_true",
                        help="3 秒后打印当前前台进程名")
    parser.add_argument("--diag", action="store_true",
                        help="实机诊断：装钩子 15 秒，打印每次按键的判定结果")
    parser.add_argument("--diag-seconds", type=float, default=15.0,
                        help="--diag 的观察时长（秒）")
    parser.add_argument("--watch", action="store_true",
                        help="持续监听模式：一直打印按键判定，Ctrl+C 退出")
    parser.add_argument("--watch-seconds", type=float, default=0.0,
                        help="--watch 的时长（0 = 一直运行）")
    parser.add_argument("--keys", action="store_true",
                        help="键码实时显示：按下按键打印它的虚拟键码，确认键位")
    parser.add_argument("--keys-seconds", type=float, default=30.0,
                        help="--keys 的时长（0 = 一直运行）")
    args = parser.parse_args(argv)
    path = args.config or default_config_path()
    if args.list_keys:
        return cmd_list_keys()
    if args.check_config:
        return cmd_check_config(path)
    if args.simulate:
        return cmd_simulate(path)
    if args.probe_app:
        return cmd_probe_app()
    if args.diag:
        return cmd_diag(path, args.diag_seconds)
    if args.watch:
        return cmd_watch(path, args.watch_seconds)
    if args.keys:
        return cmd_keys(path, args.keys_seconds)
    if not IS_WINDOWS:
        print("图形界面与键盘钩子仅支持 Windows。")
        return 1
    if not GUI_AVAILABLE:
        print("当前 Python 缺少 tkinter，无法启动图形界面。")
        return 1
    if not acquire_or_warn_single_instance():
        return 0
    _start_log_writer()
    enable_dpi_awareness()
    log_line("启动界面：%s，python %s，配置 %s"
             % (APP_TITLE, sys.version.split()[0], path))
    log_line("运行环境：%s" % describe_runtime())
    app = App(path)
    log_line("界面已就绪，键盘钩子：%s"
             % ("已安装" if app.controller.hook and
                app.controller.hook.is_alive_hook() else "未安装"))
    log_line("提示：若这个窗口收不到按键，请把本文件（startup.log）发给开发者对照")
    app.run()
    log_line("界面已退出，键盘钩子已卸载")
    flush_log()
    return 0


def cli_entry() -> int:
    """带兜底异常处理的入口：失败时写日志 + 弹错误框，不静默退出。"""
    setup_console_encoding()
    try:
        return main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - 这里就是要兜住一切
        if GUI_AVAILABLE and any(a in sys.argv for a in (
                "--check-config", "--simulate", "--list-keys", "--probe-app")):
            sys.stderr.write("%s: %s\n" % (type(exc).__name__, exc))
            return 1
        gui_failure_prompt(exc)
        return 1


if __name__ == "__main__":
    sys.exit(cli_entry())
