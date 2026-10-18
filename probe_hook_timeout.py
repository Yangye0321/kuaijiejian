# -*- coding: utf-8 -*-
"""验证「低层钩子回调过慢会被 Windows 摘掉」这个机制，并测量各步骤耗时。

实验：
  1. 装一个 WH_KEYBOARD_LL 钩子，回调里计数；
  2. 用 SendInput 注入若干按键，看钩子收到几次；
  3. 再装一个钩子，回调里故意 Sleep(400ms)（超过 LowLevelHooksTimeout 默认 300ms），
     再注入按键，看钩子是否只收到第 1 次、之后再也不被调用。

运行：python probe_hook_timeout.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kuaijiejian as K  # noqa: E402


def inject_f13(times=5, gap=0.15):
    """注入若干次 F13（F13 在普通键盘上不存在，不会影响别的程序）。"""
    sender = K.KeySender()
    seq = [{"vk": K.VK_F13, "extended": False, "up": False, "delay_ms": 0},
           {"vk": K.VK_F13, "extended": False, "up": True, "delay_ms": 0}]
    for _ in range(times):
        sender.send(seq)
        time.sleep(gap)


def run_case(name, extra_delay):
    counts = {"n": 0}

    def handler(state, is_down, is_repeat):
        counts["n"] += 1
        if extra_delay:
            time.sleep(extra_delay)
        return False

    hook = K.HookThread(handler, log=lambda m: None)
    if not hook.install():
        print("  [%s] 钩子安装失败：%s" % (name, hook.error))
        return None
    time.sleep(0.3)
    inject_f13(5)
    time.sleep(0.5)
    hook.request_stop()
    hook.join(1.0)
    print("  [%s] 注入 10 个按键事件，钩子回调被调用 %d 次%s"
          % (name, counts["n"],
             "（<=2 说明超时后被摘掉了）" if extra_delay else ""))
    return counts["n"]


def measure_engine_cost():
    """测量我们真实引擎回调一次按键要多久（不注入，直接喂假事件）。"""
    cfg = K.load_config(K.default_config_path())
    engine = K.ReplacementEngine(cfg, sender=K.KeySender(),
                                 app_query=K.ForegroundApp(ttl=0))
    state = K.KBDLLHOOKSTRUCT()
    state.vkCode = K.VK_F13
    state.flags = 0
    state.dwExtraInfo = 0
    samples = []
    for _ in range(200):
        t0 = time.perf_counter()
        engine.on_hook(state, True, False)
        engine.on_hook(state, False, False)
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    print("  引擎回调耗时（ms）：中位 %.2f，90%% %.2f，最大 %.2f"
          % (samples[len(samples) // 2],
             samples[int(len(samples) * 0.9)],
             samples[-1]))
    return samples[-1]


def main():
    if not K.IS_WINDOWS:
        print("仅 Windows 可用")
        return 1
    print("=" * 66)
    print("实验 1：正常回调（应该每次都收到）")
    run_case("快速回调", 0.0)
    print("实验 2：回调内 Sleep(400ms)（超过默认 300ms 超时）")
    run_case("慢回调", 0.4)
    print("实验 3：我们真实引擎的回调耗时")
    measure_engine_cost()
    print("=" * 66)
    print("说明：若实验 2 的调用次数明显少于实验 1，就证明「回调超时会被系统摘钩子」；")
    print("那时长里包含的任何同步磁盘写入都必须移出钩子回调。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
