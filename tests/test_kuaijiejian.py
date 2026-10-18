# -*- coding: utf-8 -*-
"""快捷键管理工具的自测：只跑纯逻辑，不安装键盘钩子、不真的注入按键。

运行：
    python tests\\test_kuaijiejian.py
    python -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kuaijiejian as K  # noqa: E402


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------

class FakeSender(object):
    def __init__(self):
        self.sent = []
        self.last_error = None

    def send(self, sequence):
        self.sent.append(list(sequence))
        return len(sequence), len(sequence)


class FakeAppQuery(object):
    def __init__(self, name=None):
        self.name = name

    def set_name(self, name):
        self.name = name

    def get(self):
        return self.name, 0, 0

    def force_refresh(self):
        return self.get()


class FakeState(object):
    def __init__(self, vk, extended=False, injected=False, extra=0):
        self.vkCode = vk
        self.flags = 0
        if extended:
            self.flags |= K.LLKHF_EXTENDED
        if injected:
            self.flags |= K.LLKHF_INJECTED
        self.dwExtraInfo = extra


def make_engine(rules=None, whitelist=("demo.exe",), all_apps=False, mode="replace",
                app_name="demo.exe", cooldown_ms=0):
    cfg = K.validate_config({
        "engine_running": True,
        "mode": mode,
        "apply_to_all_apps": all_apps,
        "app_whitelist": list(whitelist),
        "debug_log": True,
        # 测试里默认关掉「切换冷却」，方便验证真实的连按行为
        "hotkey_cooldown_ms": cooldown_ms,
        "rules": list(rules or []),
    })
    sender = FakeSender()
    events = []
    engine = K.ReplacementEngine(cfg, sender=sender,
                                 app_query=FakeAppQuery(app_name))
    engine.mode = mode
    engine.on_event = lambda ev: events.append(ev)
    return engine, sender, events


def simple_rule(trigger, output, app_filter=True, enabled=True, note=""):
    return {
        "enabled": enabled,
        "note": note,
        "trigger": {"vk": trigger, "extended": False},
        "output": [{"vk": v, "extended": False} for v in output],
        "app_filter": app_filter,
    }


# ---------------------------------------------------------------------------
# 键名与虚拟键码
# ---------------------------------------------------------------------------

class TestKeyNames(unittest.TestCase):
    def test_known_names(self):
        self.assertEqual(K.vk_to_name(K.VK_DECIMAL), "小键盘 .")
        self.assertEqual(K.vk_to_name(K.VK_NUMPAD0), "小键盘 0")
        self.assertEqual(K.vk_to_name(K.VK_LCONTROL), "左Ctrl")
        self.assertEqual(K.vk_to_name(ord("A")), "A")
        self.assertEqual(K.vk_to_name(K.VK_F12), "F12")
        self.assertEqual(K.vk_to_name(K.VK_F24), "F24")
        self.assertEqual(K.vk_to_name(K.VK_DIVIDE), "小键盘 /")

    def test_unknown_vk(self):
        self.assertEqual(K.vk_to_name(0xFE), "VK_0xFE")

    def test_name_to_vk_roundtrip(self):
        for vk in (K.VK_DECIMAL, K.VK_NUMPAD0, K.VK_LCONTROL, ord("Z"),
                   K.VK_F1, K.VK_F24, K.VK_LEFT, K.VK_RETURN):
            name = K.vk_to_name(vk)
            self.assertEqual(K.name_to_vk(name), vk, "键名往返失败：%s" % name)

    def test_name_aliases(self):
        self.assertEqual(K.name_to_vk("Esc"), K.VK_ESCAPE)
        self.assertEqual(K.name_to_vk("esc"), K.VK_ESCAPE)
        self.assertEqual(K.name_to_vk("np0"), K.VK_NUMPAD0)
        self.assertEqual(K.name_to_vk("ctrl"), K.VK_LCONTROL)
        self.assertEqual(K.name_to_vk("vk_0x1b"), K.VK_ESCAPE)
        with self.assertRaises(KeyError):
            K.name_to_vk("不存在的键")

    def test_modifier_and_extended(self):
        self.assertTrue(K.is_modifier(K.VK_LMENU))
        self.assertTrue(K.is_modifier(K.VK_RWIN))
        self.assertFalse(K.is_modifier(K.VK_NUMPAD0))
        self.assertTrue(K.requires_extended(K.VK_DIVIDE))
        self.assertTrue(K.requires_extended(K.VK_RMENU))
        self.assertFalse(K.requires_extended(K.VK_NUMPAD0))
        self.assertFalse(K.requires_extended(K.VK_DECIMAL))
        # 中文键名（左Alt / 右Alt）与常量对应
        self.assertEqual(K.name_to_vk("左Alt"), K.VK_LMENU)
        self.assertEqual(K.name_to_vk("右Alt"), K.VK_RMENU)

    def test_injected_signature(self):
        self.assertTrue(K.is_injected(K.INJECT_TAG_INT))
        self.assertTrue(K.is_injected(0x42420000))
        self.assertFalse(K.is_injected(0))
        self.assertFalse(K.is_injected(0x12345678))

    def test_normalize_numpad_when_numlock_off(self):
        # NumLock 关：小键盘 . 实际发 VK_DELETE，应归一化回小键盘 .
        self.assertEqual(K.normalize_trigger_vk(K.VK_DELETE, False, True),
                         K.VK_DECIMAL)
        self.assertEqual(K.normalize_trigger_vk(K.VK_DELETE, True, True),
                         K.VK_DELETE)
        self.assertEqual(K.normalize_trigger_vk(K.VK_DELETE, False, False),
                         K.VK_DELETE)
        self.assertEqual(K.normalize_trigger_vk(K.VK_DECIMAL, None, True),
                         K.VK_DECIMAL)

    def test_resolve_modifier(self):
        self.assertEqual(K.resolve_modifier_vk(K.VK_CONTROL), K.VK_LCONTROL)
        self.assertEqual(K.resolve_modifier_vk(K.VK_MENU), K.VK_LMENU)
        self.assertEqual(K.resolve_modifier_vk(K.VK_LMENU), K.VK_LMENU)
        self.assertEqual(K.resolve_modifier_vk(K.VK_RMENU), K.VK_RMENU)
        self.assertEqual(K.resolve_modifier_vk(K.VK_NUMPAD0), K.VK_NUMPAD0)

    def test_combo_text(self):
        keys = [{"vk": K.VK_LMENU}, {"vk": K.VK_LCONTROL}, {"vk": K.VK_NUMPAD0}]
        self.assertEqual(K.combo_text(keys), "左Alt+左Ctrl+小键盘 0")


# ---------------------------------------------------------------------------
# 注入序列构造
# ---------------------------------------------------------------------------

class TestBuildOutputSequence(unittest.TestCase):
    def _pattern(self, seq):
        return [(s["vk"], bool(s["up"])) for s in seq]

    def test_simple_output(self):
        out = [{"vk": K.VK_LMENU}, {"vk": K.VK_LCONTROL}, {"vk": K.VK_NUMPAD0}]
        seq = K.build_output_sequence(out, set(), 0)
        # 修饰键按 Ctrl → Alt 规范序先按下，再按主键，最后逆序抬起
        self.assertEqual(self._pattern(seq), [
            (K.VK_LCONTROL, False),
            (K.VK_LMENU, False),
            (K.VK_NUMPAD0, False),
            (K.VK_NUMPAD0, True),
            (K.VK_LMENU, True),
            (K.VK_LCONTROL, True),
        ])

    def test_every_key_down_has_matching_up(self):
        cases = [
            [{"vk": K.VK_LCONTROL}, {"vk": K.VK_LMENU}, {"vk": K.VK_NUMPAD0}],
            [{"vk": K.VK_F8}],
            [{"vk": K.VK_F8}, {"vk": K.VK_F9}],
            [{"vk": K.VK_LSHIFT}],  # 只有修饰键
        ]
        for out in cases:
            seq = K.build_output_sequence(out, set(), 0)
            downs = sorted(s["vk"] for s in seq if not s["up"])
            ups = sorted(s["vk"] for s in seq if s["up"])
            self.assertEqual(downs, ups, "按下/抬起不配对：%s" % out)

    def test_extended_flag_is_set(self):
        seq = K.build_output_sequence([{"vk": K.VK_RMENU}], set(), 0)
        for step in seq:
            self.assertTrue(step["extended"], "右Alt 必须带扩展旗标")

    def test_extra_held_modifiers_released_and_restored(self):
        # 用户按住 Shift 时触发：Shift 先抬起，注入完再按下
        out = [{"vk": K.VK_LCONTROL}, {"vk": K.VK_NUMPAD0}]
        seq = K.build_output_sequence(out, {K.VK_LSHIFT}, 0)
        self.assertEqual(self._pattern(seq), [
            (K.VK_LSHIFT, True),
            (K.VK_LCONTROL, False),
            (K.VK_NUMPAD0, False),
            (K.VK_NUMPAD0, True),
            (K.VK_LCONTROL, True),
            (K.VK_LSHIFT, False),
        ])

    def test_held_modifier_inside_output_is_not_released_before_use(self):
        out = [{"vk": K.VK_LMENU}, {"vk": K.VK_NUMPAD0}]
        seq = K.build_output_sequence(out, {K.VK_LMENU}, 0)
        pattern = self._pattern(seq)
        # 用户正按住的 Alt 不能先被抬起
        self.assertNotIn((K.VK_LMENU, True), pattern[:pattern.index((K.VK_NUMPAD0, False))])
        self.assertEqual(pattern[0], (K.VK_LMENU, False))
        self.assertEqual(pattern[-1], (K.VK_LMENU, True))

    def test_modifier_only_output_pairing(self):
        seq = K.build_output_sequence([{"vk": K.VK_LCONTROL}, {"vk": K.VK_LSHIFT}],
                                      set(), 0)
        self.assertEqual(self._pattern(seq), [
            (K.VK_LCONTROL, False),
            (K.VK_LSHIFT, False),
            (K.VK_LSHIFT, True),
            (K.VK_LCONTROL, True),
        ])

    def test_hold_ms_is_applied(self):
        out = [{"vk": K.VK_F8}]
        seq = K.build_output_sequence(out, set(), 25)
        delays = [s["delay_ms"] for s in seq]
        self.assertIn(25, delays)

    def test_empty_output(self):
        self.assertEqual(K.build_output_sequence([], set(), 0), [])


# ---------------------------------------------------------------------------
# 配置读写与校验
# ---------------------------------------------------------------------------

class TestConfig(unittest.TestCase):
    def test_validate_fills_defaults(self):
        cfg = K.validate_config({})
        self.assertEqual(cfg["mode"], "normal")
        self.assertEqual(cfg["rules"], [])
        self.assertTrue(cfg["numlock_off_follows_numpad"])
        self.assertEqual(cfg["toggle_hotkey"]["vk"], K.VK_F12)

    def test_validate_rejects_bad_rule(self):
        with self.assertRaises(K.ConfigError):
            K.validate_config({"rules": [{"trigger": {"vk": 110}, "output": []}]})
        with self.assertRaises(K.ConfigError):
            K.validate_config({"rules": [{"trigger": {"vk": 0},
                                          "output": [{"vk": 8}]}]})
        with self.assertRaises(K.ConfigError):
            K.validate_config({"rules": [{"trigger": {"vk": 110},
                                          "output": [{"vk": v} for v in
                                                     (8, 9, 10, 11, 12)]}]})

    def test_whitelist_normalised_and_deduped(self):
        cfg = K.validate_config({"app_whitelist": [" CloudMusic.exe ", "a.exe", "A.EXE"],
                                 "hold_ms": 999})
        self.assertEqual(cfg["app_whitelist"], ["cloudmusic.exe", "a.exe"])
        self.assertEqual(cfg["hold_ms"], 200)

    def test_roundtrip_file(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "config.json")
        cfg = K.load_config(path)
        self.assertTrue(os.path.exists(path))
        rules = [simple_rule(K.VK_DECIMAL, [K.VK_LMENU, K.VK_LCONTROL, K.VK_NUMPAD0],
                             app_filter=False)]
        cfg["rules"] = rules
        K.write_json_atomic(path, cfg)
        again = K.load_config(path)
        self.assertEqual(K.combo_text(again["rules"][0]["output"]),
                         "左Alt+左Ctrl+小键盘 0")
        self.assertEqual(again["rules"][0]["trigger"]["vk"], K.VK_DECIMAL)

    def test_broken_config_backed_up(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ 这不是合法 JSON")
        cfg = K.load_config(path)
        # 损坏配置回落到内置预置规则（默认全部停用）
        self.assertEqual(len(cfg["rules"]), len(K.DEFAULT_RULES))
        self.assertFalse(any(r["enabled"] for r in cfg["rules"]))
        backups = [n for n in os.listdir(tmp) if ".bad-" in n]
        self.assertEqual(len(backups), 1)

    def test_saved_file_has_no_internal_fields(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "config.json")
        cfg = K.validate_config({"rules": [simple_rule(K.VK_F8, [K.VK_F9])]})
        K.write_json_atomic(path, cfg)
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertNotIn("_index", raw["rules"][0])

    def test_conflict_warnings(self):
        cfg = K.validate_config({
            "toggle_hotkey": {"vk": K.VK_F8},
            "rules": [simple_rule(K.VK_F8, [K.VK_F9])],
        })
        warnings = K.check_hotkey_conflicts(cfg)
        self.assertTrue(any("开关热键" in w for w in warnings))

    def test_same_trigger_overlap_warning(self):
        cfg = K.validate_config({"rules": [
            simple_rule(K.VK_DECIMAL, [K.VK_F1], app_filter=False),
            simple_rule(K.VK_DECIMAL, [K.VK_F2], app_filter=True),
        ]})
        warnings = K.check_hotkey_conflicts(cfg)
        self.assertTrue(any("生效范围重叠" in w for w in warnings))

    def test_default_rules_cover_two_scenarios(self):
        cfg = K.validate_config(dict(K.DEFAULT_CONFIG, rules=K.DEFAULT_RULES))
        self.assertEqual(len(cfg["rules"]), 2)
        # 预置规则默认关闭，避免一启动就改变键位
        self.assertFalse(any(r["enabled"] for r in cfg["rules"]))
        self.assertEqual(cfg["rules"][0]["trigger"]["vk"], K.VK_DECIMAL)
        self.assertEqual(cfg["rules"][1]["trigger"]["vk"], K.VK_F8)
        self.assertTrue(cfg["rules"][1]["app_filter"])


# ---------------------------------------------------------------------------
# 规则匹配与优先级
# ---------------------------------------------------------------------------

class TestRuleMatching(unittest.TestCase):
    def test_scoped_rule_wins_over_global(self):
        global_rule = simple_rule(K.VK_DECIMAL, [K.VK_F1], app_filter=False,
                                  note="全局")
        scoped_rule = simple_rule(K.VK_DECIMAL, [K.VK_F2], app_filter=True,
                                  note="限定")
        engine, _, _ = make_engine([global_rule, scoped_rule])
        hit = engine.find_rule(K.VK_DECIMAL, "demo.exe")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["_index"], 1)  # 限定程序的规则排在后面但优先匹配
        self.assertTrue(hit["app_filter"])

    def test_scoped_rule_skipped_for_other_app(self):
        global_rule = simple_rule(K.VK_DECIMAL, [K.VK_F1], app_filter=False)
        scoped_rule = simple_rule(K.VK_DECIMAL, [K.VK_F2], app_filter=True)
        # 全局规则要生效，需要「所有程序」开关，否则会被白名单拦住
        engine, _, _ = make_engine([global_rule, scoped_rule], all_apps=True)
        hit = engine.find_rule(K.VK_DECIMAL, "other.exe")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["_index"], 0)

    def test_scoped_only_and_other_app_no_match(self):
        scoped_rule = simple_rule(K.VK_DECIMAL, [K.VK_F2], app_filter=True)
        engine, _, _ = make_engine([scoped_rule])
        self.assertIsNone(engine.find_rule(K.VK_DECIMAL, "other.exe"))

    def test_disabled_rule_ignored(self):
        rule = simple_rule(K.VK_DECIMAL, [K.VK_F1], enabled=False)
        engine, _, _ = make_engine([rule])
        self.assertIsNone(engine.find_rule(K.VK_DECIMAL, "demo.exe"))

    def test_app_allowed(self):
        engine, _, _ = make_engine([], whitelist=())
        self.assertFalse(engine.app_allowed())
        engine, _, _ = make_engine([], whitelist=("a.exe",))
        self.assertTrue(engine.app_allowed())
        engine, _, _ = make_engine([], whitelist=(), all_apps=True)
        self.assertTrue(engine.app_allowed())

    def test_match_reason_scoped_rule_other_app(self):
        """只有限定程序的规则时，切到别的程序要给出「为什么没生效」的原因。"""
        rule = simple_rule(K.VK_MULTIPLY, [K.VK_LCONTROL, K.VK_LMENU, K.VK_NUMPAD0])
        engine, _, _ = make_engine([rule])          # 白名单 = demo.exe
        idx, reason = engine._match_rule(K.VK_MULTIPLY, "notepad.exe")
        self.assertEqual(idx, -1)
        self.assertIn("只对白名单程序生效", reason)
        self.assertIn("notepad.exe", reason)
        self.assertIn("demo.exe", reason)
        # 白名单里的程序要能命中
        idx2, reason2 = engine._match_rule(K.VK_MULTIPLY, "demo.exe")
        self.assertEqual(idx2, 0)
        self.assertEqual(reason2, "")

    def test_match_reason_global_rule_blocked_by_whitelist(self):
        rule = simple_rule(K.VK_MULTIPLY, [K.VK_F1], app_filter=False)
        engine, _, _ = make_engine([rule])
        idx, reason = engine._match_rule(K.VK_MULTIPLY, "notepad.exe")
        self.assertEqual(idx, -1)
        self.assertIn("不在白名单", reason)
        # 勾上「所有程序」后就应该命中
        engine.config["apply_to_all_apps"] = True
        idx2, reason2 = engine._match_rule(K.VK_MULTIPLY, "notepad.exe")
        self.assertEqual(idx2, 0)
        self.assertEqual(reason2, "")

    def test_match_reason_empty_whitelist(self):
        rule = simple_rule(K.VK_MULTIPLY, [K.VK_F1], app_filter=False)
        engine, _, _ = make_engine([rule], whitelist=())
        idx, reason = engine._match_rule(K.VK_MULTIPLY, None)
        self.assertEqual(idx, -1)
        self.assertIn("白名单为空", reason)

    def test_no_match_has_no_noisy_log(self):
        """未命中任何规则的普通按键默认不写日志，避免刷屏。"""
        rule = simple_rule(K.VK_MULTIPLY, [K.VK_F1], app_filter=False)
        engine, _, events = make_engine([rule])
        engine.on_hook(FakeState(ord("Q")), True, False)
        self.assertEqual([e for e in events if e.kind == K.EVENT_PASS], [])
        # 打开「记录所有按键」后才有
        engine.config["log_all_keys"] = True
        engine.on_hook(FakeState(ord("W")), True, False)
        self.assertEqual(len([e for e in events if e.kind == K.EVENT_PASS]), 1)


# ---------------------------------------------------------------------------
# 钩子入口行为
# ---------------------------------------------------------------------------

class TestEngineHook(unittest.TestCase):
    def test_normal_mode_passes_through(self):
        rule = simple_rule(K.VK_DECIMAL, [K.VK_F1], app_filter=False)
        engine, sender, _ = make_engine([rule], mode="normal")
        swallow = engine.on_hook(FakeState(K.VK_DECIMAL), True, False)
        self.assertFalse(swallow)
        self.assertEqual(sender.sent, [])

    def _press(self, engine, vk, repeat=False):
        """一次完整按压：按下 + 松开。规则触发键与开关热键都是「松开才动作」。"""
        down = engine.on_hook(FakeState(vk), True, repeat)
        up = engine.on_hook(FakeState(vk), False, False)
        return down, up

    def test_replace_mode_swallows_and_injects(self):
        rule = simple_rule(K.VK_DECIMAL, [K.VK_LMENU, K.VK_LCONTROL, K.VK_NUMPAD0],
                           app_filter=False)
        engine, sender, events = make_engine([rule], all_apps=True)
        swallow = engine.on_hook(FakeState(K.VK_DECIMAL), True, False)
        self.assertTrue(swallow, "按下要吞掉（不能让前台程序收到）")
        self.assertEqual(sender.sent, [], "按下时还不该注入")
        self.assertTrue(engine.on_hook(FakeState(K.VK_DECIMAL), False, False))
        self.assertEqual(len(sender.sent), 1, "松开时才注入")
        seq = sender.sent[0]
        self.assertEqual([s["vk"] for s in seq if not s["up"] and s["vk"] != K.VK_LMENU
                          and s["vk"] != K.VK_LCONTROL][0], K.VK_NUMPAD0)
        self.assertTrue(any(e.kind == K.EVENT_SWALLOW for e in events))

    def test_trigger_long_press_injects_once(self):
        """按住触发键不放：只注入一次（以前会被系统连发反复注入）。"""
        rule = simple_rule(K.VK_DECIMAL, [K.VK_LCONTROL, K.VK_NUMPAD0],
                           app_filter=False)
        engine, sender, events = make_engine([rule], all_apps=True)
        self._press(engine, K.VK_DECIMAL)                  # 第一次完整按压
        self.assertEqual(len(sender.sent), 1)
        engine.on_hook(FakeState(K.VK_DECIMAL), True, False)   # 再次按住
        for _ in range(12):                                    # 长按连发
            self.assertTrue(engine.on_hook(FakeState(K.VK_DECIMAL), True, True))
        for _ in range(12):                                    # 不带标记的连发
            self.assertTrue(engine.on_hook(FakeState(K.VK_DECIMAL), True, False))
        self.assertEqual(len(sender.sent), 1, "按住期间不该重复注入")
        engine.on_hook(FakeState(K.VK_DECIMAL), False, False)  # 松开
        self.assertEqual(len(sender.sent), 2, "松开时才注入第二次")
        self.assertTrue(any("自动重复已忽略" in e.text for e in events))

    def test_trigger_repeat_without_press_still_fires(self):
        """只收到连发（按下丢失）时，松开仍要触发一次。"""
        rule = simple_rule(K.VK_DECIMAL, [K.VK_LCONTROL, K.VK_NUMPAD0],
                           app_filter=False)
        engine, sender, _ = make_engine([rule], all_apps=True)
        self.assertTrue(engine.on_hook(FakeState(K.VK_DECIMAL), True, True))
        self.assertEqual(sender.sent, [])
        engine.on_hook(FakeState(K.VK_DECIMAL), False, False)
        self.assertEqual(len(sender.sent), 1)

    def test_keyup_never_swallowed(self):
        rule = simple_rule(K.VK_DECIMAL, [K.VK_F1], app_filter=False)
        engine, sender, _ = make_engine([rule])
        self.assertFalse(engine.on_hook(FakeState(K.VK_DECIMAL), False, False))
        self.assertEqual(sender.sent, [])

    def test_auto_repeat_without_arm_not_triggered(self):
        """连发若是本次按住的起点，也不该在按下时注入。"""
        rule = simple_rule(K.VK_DECIMAL, [K.VK_F1], app_filter=False)
        engine, sender, _ = make_engine([rule], all_apps=True)
        self.assertTrue(engine.on_hook(FakeState(K.VK_DECIMAL), True, True))
        self.assertEqual(sender.sent, [])
        engine.on_hook(FakeState(K.VK_DECIMAL), False, False)
        self.assertEqual(len(sender.sent), 1)

    def test_injected_event_ignored(self):
        rule = simple_rule(K.VK_DECIMAL, [K.VK_F1], app_filter=False)
        engine, sender, _ = make_engine([rule])
        # 带 LLKHF_INJECTED 旗标
        self.assertFalse(engine.on_hook(FakeState(K.VK_DECIMAL, injected=True),
                                        True, False))
        # 带本工具签名
        self.assertFalse(engine.on_hook(
            FakeState(K.VK_DECIMAL, extra=K.INJECT_TAG_INT), True, False))
        self.assertEqual(sender.sent, [])

    def test_empty_whitelist_blocks_rules(self):
        rule = simple_rule(K.VK_DECIMAL, [K.VK_F1], app_filter=False)
        engine, sender, _ = make_engine([rule], whitelist=())
        engine.app_query = FakeAppQuery(None)
        self.assertFalse(engine.on_hook(FakeState(K.VK_DECIMAL), True, False))
        self.assertEqual(sender.sent, [])

    def test_scoped_rule_only_in_target_app(self):
        rule = simple_rule(K.VK_F8, [K.VK_LCONTROL, K.VK_LMENU, ord("L")])
        engine, sender, _ = make_engine([rule])          # 白名单 = demo.exe
        engine.app_query.set_name("demo.exe")
        self._press(engine, K.VK_F8)
        self.assertEqual(len(sender.sent), 1)
        # 切到白名单外的程序后必须不再替代
        engine.app_query.set_name("notepad.exe")
        self._press(engine, K.VK_F8)
        self.assertEqual(len(sender.sent), 1)

    def test_toggle_hotkey(self):
        engine, sender, events = make_engine([], mode="normal")
        engine.config["toggle_hotkey"] = {"vk": K.VK_F12, "extended": False}
        self._press_f12(engine)
        self.assertEqual(engine.mode, "replace")
        self._press_f12(engine)
        self.assertEqual(engine.mode, "normal")
        self.assertTrue(any(e.kind == K.EVENT_MODE for e in events))

    def test_mode_survives_config_save(self):
        """回归：切换模式后，界面保存配置不能把模式打回旧值。

        历史缺陷：模式只存在引擎的独立属性里，apply_config 用旧配置覆盖它，
        表现为「按了开关热键但模式没切换」。
        """
        engine, _, _ = make_engine([], mode="normal")
        engine.set_mode("replace")
        stale = {"engine_running": True, "mode": "normal", "rules": [],
                 "app_whitelist": ["a.exe"], "debug_log": True}
        engine.apply_config(stale)          # 界面保存配置时会走这一步
        self.assertEqual(engine.mode, "replace")

    def test_toggle_hotkey_writes_into_config(self):
        engine, _, _ = make_engine([], mode="normal")
        self._press_f12(engine)
        self.assertEqual(engine.config["mode"], "replace")
        self._press_f12(engine)
        self.assertEqual(engine.config["mode"], "normal")

    def test_mode_property_normalises(self):
        engine, _, _ = make_engine([], mode="normal")
        engine.mode = "什么鬼"
        self.assertEqual(engine.mode, "normal")
        engine.mode = "replace"
        self.assertEqual(engine.config["mode"], "replace")

    # -- 开关热键：按下武装、松开才切换（方案 A）--------------------------

    def _f12_down(self, engine, repeat=False):
        return engine.on_hook(FakeState(K.VK_F12), True, repeat)

    def _f12_up(self, engine):
        return engine.on_hook(FakeState(K.VK_F12), False, False)

    def _press_f12(self, engine, repeat=False):
        """一次完整按压：按下 + 松开。"""
        self._f12_down(engine, repeat)
        return self._f12_up(engine)

    @staticmethod
    def _toggle_armed_state(engine):
        return engine._toggle_armed

    def test_toggle_happens_on_release(self):
        engine, _, _ = make_engine([], mode="normal")
        self.assertTrue(self._f12_down(engine))
        self.assertEqual(engine.mode, "normal", "按下时还不该切换")
        self.assertTrue(self._f12_up(engine))
        self.assertEqual(engine.mode, "replace", "松开时才切换")

    def test_long_press_toggles_exactly_once(self):
        """长按：按下 + 一堆自动重复 + 松开 → 只切换一次（不依赖 repeat 标记）。"""
        engine, _, events = make_engine([], mode="normal")
        self._f12_down(engine)
        for _ in range(12):                       # 带 repeat 标记的连发
            self.assertTrue(self._f12_down(engine, repeat=True))
        for _ in range(12):                       # 不带标记的连发也要被忽略
            self.assertTrue(self._f12_down(engine))
        self.assertEqual(engine.mode, "normal", "按住期间绝不能切换模式")
        self._f12_up(engine)
        self.assertEqual(engine.mode, "replace")
        # 再长按一次 → 正好切回正常输入
        self._f12_down(engine)
        for _ in range(20):
            self._f12_down(engine, repeat=True)
        self._f12_up(engine)
        self.assertEqual(engine.mode, "normal")
        self.assertTrue(any("仍被按住" in e.text for e in events))

    def test_stray_keyup_is_ignored(self):
        """没按过就来了一个抬起（例如钩子中途重装），不能切换模式。"""
        engine, _, _ = make_engine([], mode="normal")
        self.assertTrue(self._f12_up(engine))
        self.assertEqual(engine.mode, "normal")

    def test_hold_across_config_reload_does_not_switch(self):
        """按下后还没松开就重新加载配置：武装状态要清掉，避免下次按键误切。"""
        engine, _, _ = make_engine([], mode="normal")
        self._f12_down(engine)
        engine.apply_config(dict(engine.config))
        self.assertFalse(engine._toggle_armed)
        self._f12_up(engine)
        self.assertEqual(engine.mode, "normal")

    def test_repeat_down_is_swallowed(self):
        engine, _, _ = make_engine([], mode="normal")
        self.assertTrue(self._f12_down(engine, repeat=True))
        self.assertEqual(engine.mode, "normal")

    def test_repeat_without_press_still_toggles_once(self):
        """真实故障场景：新启动的进程只收到了长按连发，最初那次「按下」丢了。

        此时连发要当作这次按住的起点，松开时仍然恰好切换一次
        （典型触发条件：关掉工具时手指还按着 F12）。
        """
        engine, _, events = make_engine([], mode="normal")
        for _ in range(4):                     # 只收到 repeat，收不到最初的按下
            self.assertTrue(self._f12_down(engine, repeat=True))
        self.assertEqual(engine.mode, "normal", "按住期间不该切换")
        self.assertEqual(self._toggle_armed_state(engine), True, "连发应触发武装")
        self.assertTrue(self._f12_up(engine))
        self.assertEqual(engine.mode, "replace", "松开时必须切换一次")
        # 再重复一次同样的过程：仍然每次恰好一次
        engine.reset_hotkey_state()
        for _ in range(3):
            self._f12_down(engine, repeat=True)
        self._f12_up(engine)
        self.assertEqual(engine.mode, "normal")
        self.assertTrue(any("按下事件丢失" in e.text for e in events))

    def test_stale_arm_is_cleared_on_new_repeat(self):
        """残留的旧武装（抬起丢失）超过 5 秒后，新一次长按要重新武装。"""
        engine, _, _ = make_engine([], mode="normal")
        self._f12_down(engine)                       # 武装
        engine._toggle_armed_at -= 30.0              # 假装抬起事件丢了很久
        self.assertTrue(self._f12_down(engine, repeat=True))
        self.assertEqual(engine.mode, "normal")
        self.assertTrue(self._f12_up(engine))
        self.assertEqual(engine.mode, "replace")     # 仍然只切换一次

    def test_repeat_while_armed_does_not_rearm(self):
        engine, _, events = make_engine([], mode="normal")
        self._f12_down(engine)
        for _ in range(5):
            self.assertTrue(self._f12_down(engine, repeat=True))
        self._f12_up(engine)
        self.assertEqual(engine.mode, "replace")      # 一次按压只切换一次
        self.assertTrue(any("自动重复已忽略" in e.text for e in events))

    def test_rapid_full_presses_toggle_each_time(self):
        """连着点 F12：每次「按下-松开」都算一次（不做时间限制）。"""
        engine, _, _ = make_engine([], mode="normal")
        self._press_f12(engine)
        self.assertEqual(engine.mode, "replace")
        self._press_f12(engine)
        self.assertEqual(engine.mode, "normal")
        self._press_f12(engine)
        self.assertEqual(engine.mode, "replace")

    def test_panic_hotkey_fires_on_press_and_is_swallowed(self):
        engine, _, _ = make_engine([], mode="replace")
        engine.config["panic_hotkey"] = {"vk": K.VK_PAUSE, "extended": False}
        self.assertTrue(engine.on_hook(FakeState(K.VK_PAUSE), True, False))
        self.assertEqual(engine.mode, "normal")
        # 抬起也要吞掉，不能漏给前台程序
        self.assertTrue(engine.on_hook(FakeState(K.VK_PAUSE), False, False))
        # 按住不放的连发不会重复触发，但也仍然是吞掉的
        self.assertTrue(engine.on_hook(FakeState(K.VK_PAUSE), True, True))

    def test_hotkey_config_fields(self):
        cfg = K.validate_config({"hotkey_debounce_ms": 250,
                                 "mode_restore_on_start": True,
                                 "mode": "replace"})
        self.assertEqual(cfg["hotkey_debounce_ms"], 250)
        self.assertTrue(cfg["mode_restore_on_start"])
        self.assertEqual(cfg["mode"], "replace")
        # 默认不恢复模式：启动一律正常输入
        cfg2 = K.validate_config({"mode": "replace"})
        self.assertFalse(cfg2["mode_restore_on_start"])
        self.assertEqual(cfg2["mode"], "normal")
        cfg3 = K.validate_config({"hotkey_debounce_ms": 99999})
        self.assertEqual(cfg3["hotkey_debounce_ms"], 5000)

    def test_panic_hotkey_returns_to_normal(self):
        engine, _, _ = make_engine([], mode="replace")
        engine.config["panic_hotkey"] = {"vk": K.VK_END, "extended": True}
        self.assertTrue(engine.on_hook(FakeState(K.VK_END), True, False))
        self.assertEqual(engine.mode, "normal")

    def test_held_modifier_restored_after_trigger(self):
        rule = simple_rule(K.VK_DECIMAL, [K.VK_LCONTROL, K.VK_NUMPAD0],
                           app_filter=False)
        engine, sender, _ = make_engine([rule], all_apps=True)
        engine.on_hook(FakeState(K.VK_LSHIFT), True, False)   # 用户按住 Shift
        engine.on_hook(FakeState(K.VK_DECIMAL), True, False)  # 按下触发键
        engine.on_hook(FakeState(K.VK_DECIMAL), False, False)  # 松开 → 注入
        seq = sender.sent[0]
        self.assertTrue(seq[0]["up"] and seq[0]["vk"] == K.VK_LSHIFT)
        self.assertEqual((seq[-1]["vk"], seq[-1]["up"]), (K.VK_LSHIFT, False))

    def test_engine_survives_broken_sender(self):
        class BoomSender(object):
            last_error = "boom"
            def send(self, sequence):
                raise RuntimeError("boom")

        rule = simple_rule(K.VK_DECIMAL, [K.VK_F1], app_filter=False)
        engine, _, events = make_engine([rule], all_apps=True)
        logs = []
        engine.log = logs.append
        engine.sender = BoomSender()
        # 不应抛异常（否则会卡键盘），按下与松开都要吞掉
        self.assertTrue(engine.on_hook(FakeState(K.VK_DECIMAL), True, False))
        self.assertTrue(engine.on_hook(FakeState(K.VK_DECIMAL), False, False))
        self.assertEqual(engine.last_error, "boom")
        self.assertTrue(any("注入失败" in m for m in logs))
        # 调试日志里仍能看到这次触发（并标注注入不完整）
        self.assertTrue(any(e.kind == K.EVENT_SWALLOW for e in events))
        self.assertIn("注入不完整", "".join(e.text for e in events))

    def test_input_structure_layout(self):
        """INPUT 必须是 40 字节且 dwExtraInfo 在偏移 16。

        实测 cbSize 传 32/28/24 都会被 SendInput 拒绝（错误码 87），
        所以这个布局断言很关键，防止以后改动结构把注入弄坏。
        """
        self.assertEqual(K.ctypes.sizeof(K.INPUT), 40)
        self.assertEqual(K.ctypes.sizeof(K.KEYBDINPUT), 24)
        self.assertEqual(K.KEYBDINPUT.dwExtraInfo.offset, 16)
        inp = K.KeySender.__new__(K.KeySender)
        K.KeySender.__init__(inp)
        self.assertIsNone(inp.last_error)

    def test_make_input_flags(self):
        sender = K.KeySender()
        down = sender._make_input(K.VK_F8, False, False)
        self.assertEqual(down.type, K.INPUT_KEYBOARD)
        self.assertEqual(down.ki.wVk, K.VK_F8)
        self.assertEqual(down.ki.dwFlags, 0)
        self.assertEqual(down.ki.dwExtraInfo, K.INJECT_TAG_INT)
        up = sender._make_input(K.VK_F8, True, False)
        self.assertEqual(up.ki.dwFlags, K.KEYEVENTF_KEYUP)
        ext = sender._make_input(K.VK_RMENU, False, True)
        self.assertTrue(ext.ki.dwFlags & K.KEYEVENTF_EXTENDEDKEY)
        auto = sender._make_input(K.VK_DIVIDE, False, False)
        self.assertTrue(auto.ki.dwFlags & K.KEYEVENTF_EXTENDEDKEY)


# ---------------------------------------------------------------------------
# 配置默认值 / 命令行辅助
# ---------------------------------------------------------------------------

class TestMisc(unittest.TestCase):
    def test_default_config_json_serialisable(self):
        cfg = K.validate_config(dict(K.DEFAULT_CONFIG, rules=K.DEFAULT_RULES))
        text = json.dumps(cfg, ensure_ascii=False)
        self.assertIn("小键盘", K.combo_text(cfg["rules"][0]["output"]) + "小键盘")

    def test_describe_sequence(self):
        seq = K.build_output_sequence([{"vk": K.VK_F8}], set(), 0)
        text = K.describe_sequence(seq)
        self.assertIn("F8", text)

    def test_default_config_path(self):
        path = K.default_config_path()
        self.assertTrue(path.endswith("config.json"))


# ---------------------------------------------------------------------------
# GUI 心跳（需要图形环境，没有就跳过）
# ---------------------------------------------------------------------------

class TestGuiHeartbeat(unittest.TestCase):
    """GUI 心跳必须自己续命：任何异常都不能让 _poll 停止重排。

    历史缺陷：_poll 抛异常后 after() 不再排下一次，界面还开着但状态不再刷新、
    钩子掉线也不会重装，表现就是「窗口开着，按什么键都没反应」。
    """

    def setUp(self):
        if not K.GUI_AVAILABLE or not K.IS_WINDOWS:
            self.skipTest("需要 Windows + tkinter")
        try:
            import tkinter
            probe = tkinter.Tk()
            probe.destroy()
        except Exception:
            self.skipTest("当前会话没有可用的图形环境")

    def test_poll_reschedules_after_exception(self):
        app = K.App(K.default_config_path())
        original = app._update_status_widgets
        calls = {"n": 0}

        def boom(status):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise RuntimeError("模拟状态刷新崩溃")
            return original(status)

        app._update_status_widgets = boom
        try:
            for _ in range(14):
                app.root.update()
                time.sleep(0.1)
            self.assertGreater(calls["n"], 3, "心跳被异常打断了")
            self.assertIsNotNone(app.controller.refresh_status())
        finally:
            app._update_status_widgets = original
            app.on_quit()

    def test_grab_app_fills_target_not_self(self):
        """「取当前前台进程名」要把目标程序填进输入框，而不是工具自己。"""
        app = K.App(K.default_config_path())
        original_query = app.controller.engine.app_query
        shown = []
        original_info = K.messagebox.showinfo
        K.messagebox.showinfo = lambda *a, **k: shown.append(a)

        class FakeQuery(object):
            def __init__(self, name):
                self.name = name

            def get(self):
                return self.name, 0, 0

            def force_refresh(self):
                return self.get()

        try:
            app.controller.engine.app_query = FakeQuery("cloudmusic.exe")
            app.on_grab_app()
            app._do_grab_app_delayed()          # 跳过 3 秒等待
            self.assertEqual(app.var_new_app.get(), "cloudmusic.exe")

            # 若抓到的是 python/pythonw，应弹提示而不是把工具自己写进白名单
            app.var_new_app.set("")
            shown[:] = []
            app.controller.engine.app_query = FakeQuery("pythonw.exe")
            app._do_grab_app_delayed()
            self.assertEqual(app.var_new_app.get(), "")
            self.assertTrue(shown, "抓到工具自己时应给出提示")
        finally:
            K.messagebox.showinfo = original_info
            app.controller.engine.app_query = original_query
            app.on_quit()

    def test_hook_stall_watchdog_reinstalls(self):
        """钩子被系统静默摘掉（计数不再增长）时，看门狗要重装钩子。

        背景：Windows 判定低层钩子回调超时后会把钩子摘掉且不通知我们，
        程序看着一切正常却收不到任何按键。日志里出现过这种「计数冻结」。
        """
        app = K.App(K.default_config_path())
        original_restart = app.controller.restart_hook
        called = []
        try:
            app.controller.restart_hook = lambda: called.append(True)
            app.controller.engine.hook_calls = 100       # 假装收到过 100 个按键
            app.controller._last_stall_calls = 100
            app.controller._last_stall_at = time.time() - 60.0   # 60 秒没再增长
            app.controller._retry_at = time.time() - 60.0
            app.controller._last_heartbeat = time.time()          # 跳过常规心跳
            app.controller.refresh_status()
            self.assertTrue(called, "计数冻结 25 秒后应触发重装")
        finally:
            app.controller.restart_hook = original_restart
            app.on_quit()

    def test_log_line_is_async(self):
        """日志必须走后台队列，绝不能阻塞调用方（钩子回调会调用它）。"""
        self.assertTrue(hasattr(K, "_LOG_QUEUE"))
        K._start_log_writer()
        path = K.startup_log_path()
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        K.log_line("测试：异步日志写入")
        K.flush_log(2.0)
        self.assertTrue(os.path.exists(path), "后台线程应写出日志文件")
        with open(path, "r", encoding="utf-8") as fh:
            self.assertIn("异步日志写入", fh.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
