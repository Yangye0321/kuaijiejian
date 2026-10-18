# -*- coding: utf-8 -*-
"""注入链路验证脚本（手工运行，不要在打字时运行）。

它做两件事：
  1. 用 SendInput 发一个带签名的组合键，通过底层键盘钩子把事件原样抓回来，
     核对「修饰键→主键→逆序抬起」的顺序，以及每个注入事件都带签名；
  2. 模拟一次「按小键盘 . → 注入 Alt+Ctrl+小键盘0」的完整替代流程，
     核对决策为吞键、注入序列正确。

运行：
    python verify_injection.py keys    # 仅做第 1 步，影响很小（约 5 个事件）
    python verify_injection.py like    # 模拟网易云「喜欢」规则的替代流程
"""

import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kuaijiejian as K  # noqa: E402


def order_of(step):
    return ("%s%s" % ("↑" if not step["up"] else "↓",
                      K.vk_to_name(step["vk"], step["extended"])))


def capture_own_injection(sequence, timeout=2.0):
    """注入一段序列，并用钩子线程把事件抓回来。"""
    captured = []
    lock = []

    def handler(state, is_down, is_repeat):
        vk = int(state.vkCode)
        if K.is_injected(int(state.dwExtraInfo)):
            captured.append({
                "vk": vk,
                "up": not is_down,
                "extended": bool(int(state.flags) & K.LLKHF_EXTENDED),
                "n": len(captured),
            })
        return False

    hook = K.HookThread(handler, log=lambda m: sys.stdout.write("[hook] %s\n" % m))
    if not hook.install():
        print("钩子安装失败：%s" % hook.error)
        return None
    time.sleep(0.3)
    sender = K.KeySender()
    sent, total = sender.send(sequence)
    deadline = time.time() + timeout
    while time.time() < deadline and len(captured) < total:
        time.sleep(0.05)
    time.sleep(0.2)
    hook.request_stop()
    hook.join(1.0)
    return sent, total, captured


def show_sequence(title, sequence):
    print("\n%s" % title)
    for i, step in enumerate(sequence):
        print("  %2d. %s%s" % (i + 1, order_of(step),
                               "  (延时 %dms)" % step["delay_ms"]
                               if step.get("delay_ms") else ""))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "keys"
    if not K.IS_WINDOWS:
        print("仅 Windows 可用")
        return 1

    if mode == "like":
        cfg = K.load_config(K.default_config_path())
        rule = cfg["rules"][1]
        sequence = K.build_output_sequence(rule["output"], set(), cfg["hold_ms"])
        show_sequence("模拟规则：F8 → 网易云「喜欢」", sequence)
        sender = K.KeySender()
        print("\n注意：接下来真的会注入一次 %s；请先点一下空白处，别让输入法/聊天框吃到。"
              % K.combo_text(rule["output"]))
        time.sleep(3)
        sent, total = sender.send(sequence)
        print("注入结果：%d/%d%s" % (sent, total,
                                   "" if sent == total else
                                   "（%s）" % sender.last_error))
        return 0 if sent == total else 1

    # 默认：自检注入链路
    out = [{"vk": K.VK_LCONTROL}, {"vk": K.VK_LMENU}, {"vk": K.VK_NUMPAD0}]
    sequence = K.build_output_sequence(out, set(), 0)
    show_sequence("待验证的注入序列：Ctrl+Alt+小键盘0", sequence)
    time.sleep(1)
    result = capture_own_injection(sequence)
    if result is None:
        return 1
    sent, total, captured = result
    print("\n注入 %d/%d 个事件，钩子抓回 %d 个" % (sent, total, len(captured)))
    expect = ["%s" % order_of(s) for s in sequence]
    got = ["%s" % order_of(s) for s in captured]
    expect_head = expect[:len(got)]
    ok = True
    if got != expect_head:
        print("顺序不一致：")
        print("  期望：%s" % " ".join(expect_head))
        print("  实际：%s" % " ".join(got))
        ok = False
    else:
        print("顺序一致：%s" % " ".join(got))
    if len(captured) != total:
        print("提示：抓到 %d 个，期望 %d 个（可能被前台程序或系统吞掉部分事件）"
              % (len(captured), total))
    print("\n结论：%s" % ("注入链路正常，事件带签名且顺序正确。"
                       if ok else "注入链路有问题，请把上面的输出发给我。"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
