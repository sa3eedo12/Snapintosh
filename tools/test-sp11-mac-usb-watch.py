#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the Mac-side watcher tools/sp11_mac_usb_watch.py (docs/SP11-XHCI-S1.md section 8) on SYNTHETIC command outputs: nothing here runs ioreg,
ifconfig or networksetup, opens a device or a socket, or writes outside build/.

    python3 tools/test-sp11-mac-usb-watch.py
    SP11_MAC_WATCH_REAL=1 python3 tools/test-sp11-mac-usb-watch.py RealCommands   # opt in: parse this Mac's real output, structure only (SKIP otherwise)

* the parser reads the shape `ioreg -r -c IOUSBHostDevice -l -w0` prints (decimal ids, nested nodes, a device printed twice) and keeps only vendor id, product
  id, name, interface classes and the network interface names below a device;
* each finding rule fires on its positive case and stays silent on a faulty one: USB_ADDED, USB_REMOVED, NET_ADDED, NET_REMOVED, APPLE_GADGET,
  USB_NET_DEVICE, USB_NET_IFACE; the baseline is never a finding; a failing command is never a removal;
* the digest is blind to serial numbers, location IDs and registry IDs, and the log and the output never hold them or a MAC address;
* the tool runs three fixed read-only commands without a shell, and a source checker rejects sudo, a shell, a socket, an unknown import or a device path.
"""
from pathlib import Path
import ast
import contextlib
import io
import itertools
import os
import shutil
import sys
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLS))
import sp11_mac_usb_watch as watcher  # noqa: E402

SOURCE = TOOLS / 'sp11_mac_usb_watch.py'
BUILD = TOOLS.parent / 'build' / 'sp11-mac-usb-watch-test'
SERIAL, MAC, LOCATION = 'SERIAL-SENTINEL-7Q', 'aa:bb:cc:dd:ee:ff', 0x14100000
IDS = itertools.count(0x100000100)


def dev(vid, pid, name, classes=(), ifaces=()):
    return watcher.UsbDevice(vid, pid, name, tuple(classes), tuple(ifaces))


def snap(devices=(), interfaces=(), labels=()):
    return watcher.Snapshot(tuple(devices), tuple(interfaces), tuple(labels))


def prop_lines(prefix, props):
    return [f'{prefix}{{'] + [f'{prefix}  "{key}" = {value}' for key, value in props.items()] + [f'{prefix}}}', prefix]


def device_lines(d, base=''):
    """One device, its driver, its interfaces, the network interfaces below them and the devices behind it, in the shape the real tool prints."""
    node = lambda prefix, name, cls: f'{prefix}+-o {name}  <class {cls}, id 0x{next(IDS):x}, registered, matched, active, busy 0 (1 ms), retain 12>'  # noqa: E731
    entry = d.get('id', next(IDS))
    lines = [f'{base}+-o {d["name"]}@{d.get("loc", LOCATION):08x}  <class IOUSBHostDevice, id 0x{entry:x}, registered, matched, active, busy 0 (1 ms), retain 12>']
    props = {'idVendor': d['vid'], 'idProduct': d['pid'], 'bDeviceClass': 0, 'locationID': d.get('loc', LOCATION), 'USBSpeed': 3}
    if d.get('name'):
        props['USB Product Name'] = f'"{d["name"]}"'
    props['USB Serial Number'] = f'"{d.get("serial", SERIAL)}"'
    lines += prop_lines(base + '  | ', props)
    lines.append(node(base + '  ', 'AppleUSBHostCompositeDevice', 'AppleUSBHostCompositeDevice'))
    lines += prop_lines(base + '  |   ', {'bDeviceClass': 0})
    for index, (cls, sub) in enumerate(d.get('ifaces', ())):
        lines.append(node(base + '  ', f'{d["name"]}@{index}', 'IOUSBHostInterface'))
        lines += prop_lines(base + '  | | ', {'bInterfaceClass': cls, 'bInterfaceSubClass': sub, 'idVendor': d['vid'], 'idProduct': d['pid']})
        if index == 0 and d.get('bsd'):
            lines.append(node(base + '  | ', 'AppleUSBNCMData', 'AppleUSBNCMData'))
            for bsd in d['bsd']:
                lines.append(node(base + '  | | ', bsd, 'IOEthernetInterface'))
                lines += prop_lines(base + '  | | | ', {'BSD Name': f'"{bsd}"', 'IOMACAddress': f'<{MAC.replace(":", "")}>'})
    for child in d.get('children', ()):
        lines += device_lines(child, base + '  | | ')
    return lines


def ioreg_text(devices, duplicate_nested=False):
    lines = []
    for d in devices:
        lines += device_lines(d)
    if duplicate_nested:
        for d in devices:
            for child in d.get('children', ()):
                lines += device_lines(child)
    return '\n'.join(lines) + '\n'


def ports_text(pairs):
    return ''.join(f'\nHardware Port: {label}\nDevice: {device}\nEthernet Address: {MAC}\n' for device, label in pairs)


GADGET = {'vid': 0x05ac, 'pid': 0x1905, 'name': 'Mac', 'ifaces': [(2, 13), (10, 0)], 'bsd': ['en7']}
HUB = {'vid': 0x05e3, 'pid': 0x0610, 'name': 'USB2.0 Hub', 'ifaces': [(9, 0)], 'id': 0x100000800,
       'children': [{'vid': 0x090c, 'pid': 0x1000, 'name': 'Flash Drive', 'ifaces': [(8, 6)], 'id': 0x100000900}]}


class Frames:
    """A runner that answers the three commands of tick n from frames[n] (the last frame repeats); an Exception in a frame is raised."""

    def __init__(self, frames):
        self.frames, self.calls = frames, 0

    def __call__(self, name):
        frame = self.frames[min((self.calls) // len(watcher.COMMANDS), len(self.frames) - 1)]
        self.calls += 1
        value = frame[name]
        if isinstance(value, Exception):
            raise value
        return value


def frame(devices=(), interfaces=('lo0', 'en0'), pairs=(('en0', 'Ethernet'),), **extra):
    return {'usb': ioreg_text(devices), 'ifconfig': ' '.join(interfaces) + '\n', 'ports': ports_text(pairs), **extra}


def run_watch(frames, duration=5.0, allow_empty_usb=False):
    clock = [0.0]
    out, log = io.StringIO(), io.StringIO()
    counts = watcher.watch(Frames(frames), 1.0, duration, log, out, clock=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s),
                           now=lambda: f'2026-11-17T02:00:{int(clock[0]):02d}Z', allow_empty_usb=allow_empty_usb)
    return counts, out.getvalue(), log.getvalue()


def baseline_heads(text):
    """The BASELINE lines of a screen or a log that are not a device line."""
    return [line for line in text.splitlines() if line.startswith('BASELINE ') and ' DEVICE ' not in line]


def baseline_problems(out, devices, late):
    """What the screen must show of the baseline: one line, marked LATE when ticks came before it, and its devices only then (a baseline of the first tick
    is the whole tree as it was, and its devices are in the log)."""
    problems, heads = [], baseline_heads(out)
    shown = [line.split(' DEVICE ', 1)[1] for line in out.splitlines() if line.startswith('BASELINE ') and ' DEVICE ' in line]
    if len(heads) != 1:
        return [f'{len(heads)} baseline lines']
    if (' LATE after ' in heads[0]) != late:
        problems.append('the LATE marker is ' + ('missing' if late else 'there with no late baseline'))
    if sorted(shown) != (sorted(devices) if late else []):
        problems.append(f'the devices on the screen are {shown}')
    return problems


def source_problems(source):
    """What the tool must never do: an import outside the small allowed set, a shell, a process other than subprocess.run, sudo or a device path in a
    string that is not a docstring, a socket. Read from the syntax tree, so docstrings and comments may talk about them."""
    tree, problems = ast.parse(source), []
    allowed = {'collections', 'dataclasses', 'datetime', 'pathlib', 'argparse', 'hashlib', 're', 'subprocess', 'sys', 'time'}
    docstrings = {id(body[0].value) for node in ast.walk(tree) if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and (body := node.body)
                  and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems += [f'import {a.name}' for a in node.names if a.name.split('.')[0] not in allowed]
        elif isinstance(node, ast.ImportFrom) and (node.module or '').split('.')[0] not in allowed:
            problems.append(f'from {node.module} import')
        elif isinstance(node, ast.Call):
            func = ast.unparse(node.func)
            if func in ('os.system', 'os.popen', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_output', 'os.open', 'eval', 'exec'):
                problems.append(f'call {func}')
            problems += [f'keyword {k.arg}' for k in node.keywords if k.arg in ('shell', 'stdin')]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            problems += [f'string {token}' for token in ('sudo', '/dev/', 'socket') if token in node.value.lower()]
    return problems


def command_problems(commands):
    """The commands must be exactly user level read-only tools, as tuples (no shell string), none of them sudo."""
    problems = []
    for name, argv in commands:
        if not isinstance(argv, tuple) or argv[0] not in ('ioreg', 'ifconfig', 'networksetup'):
            problems.append(f'{name}: {argv!r}')
        elif any(part in ('sudo', '-w', 'sh', 'bash') or ';' in part or '|' in part or '>' in part for part in argv[1:]) and argv[:1] != ('ioreg',):
            problems.append(f'{name}: {argv!r}')
        elif argv[0] == 'networksetup' and argv[1:] != ('-listallhardwareports',):
            problems.append(f'{name}: {argv!r} is not the read-only listing')
    return problems


class Parser(unittest.TestCase):
    def test_a_device_is_its_ids_name_interface_classes_and_the_network_interfaces_below_it(self):
        (found,) = watcher.parse_usb(ioreg_text([GADGET]))
        self.assertEqual((found.vendor, found.product, found.name), (0x05ac, 0x1905, 'Mac'))
        self.assertEqual((found.vidpid, found.classes, found.ifaces), ('05ac:1905', ((2, 13), (10, 0)), ('en7',)))

    def test_a_device_behind_a_hub_is_its_own_and_the_hub_does_not_take_its_interfaces(self):
        found = {d.name: d for d in watcher.parse_usb(ioreg_text([HUB]))}
        self.assertEqual(sorted(found), ['Flash Drive', 'USB2.0 Hub'])
        self.assertEqual(found['USB2.0 Hub'].classes, ((9, 0),))
        self.assertEqual(found['Flash Drive'].classes, ((8, 6),))

    def test_a_device_printed_twice_is_one_device(self):
        once, twice = watcher.parse_usb(ioreg_text([HUB])), watcher.parse_usb(ioreg_text([HUB], duplicate_nested=True))
        self.assertEqual(once, twice)
        self.assertEqual(len(twice), 2)

    def test_a_missing_name_falls_back_to_the_node_name_and_a_node_without_ids_is_not_a_device(self):
        nameless = dict(GADGET, name='')
        text = ioreg_text([dict(GADGET, name='Fallback Name')]).replace('"USB Product Name" = "Fallback Name"', '"x" = 1')
        self.assertEqual([d.name for d in watcher.parse_usb(text)], ['Fallback Name'])
        self.assertEqual(watcher.parse_usb(ioreg_text([nameless]).replace('"idVendor" = 1452', '"idVendor" = "none"')), ())
        self.assertEqual(watcher.parse_usb(''), ())
        self.assertEqual(watcher.parse_usb('junk\n+-o x\n"idVendor" = 1\n'), ())

    def test_the_ifconfig_list_and_the_hardware_ports(self):
        self.assertEqual(watcher.parse_ifconfig_list('lo0 en1 en0 en1\n'), ('en0', 'en1', 'lo0'))
        pairs = watcher.parse_hardware_ports(ports_text([('en7', 'USB 10/100/1000 LAN'), ('en0', 'Wi-Fi')]))
        self.assertEqual(pairs, (('en0', 'Wi-Fi'), ('en7', 'USB 10/100/1000 LAN')))
        self.assertNotIn(MAC, repr(pairs))
        self.assertEqual(watcher.parse_hardware_ports('Device: en3\n'), (), 'a device with no port label is dropped')


class Digest(unittest.TestCase):
    def test_the_digest_ignores_serials_locations_registry_ids_and_order(self):
        a = watcher.Snapshot(watcher.parse_usb(ioreg_text([GADGET, HUB])))
        other = [dict(HUB, id=0x1, loc=0x1000, serial='another'), dict(GADGET, id=0x2, loc=0x2000, serial='SERIAL-ELSE')]
        b = watcher.Snapshot(watcher.parse_usb(ioreg_text(other)))
        self.assertEqual(watcher.usb_digest(a), watcher.usb_digest(b))

    def test_the_digest_changes_with_a_name_a_product_id_or_a_device(self):
        base = watcher.usb_digest(snap([dev(1, 2, 'A')]))
        for label, changed in (('name', snap([dev(1, 2, 'B')])), ('product', snap([dev(1, 3, 'A')])), ('vendor', snap([dev(2, 2, 'A')])),
                               ('one more', snap([dev(1, 2, 'A'), dev(1, 2, 'A')])), ('none', snap())):
            with self.subTest(change=label):
                self.assertNotEqual(watcher.usb_digest(changed), base)
        self.assertEqual(len(base), 12)
        self.assertNotEqual(watcher.net_digest(snap(interfaces=('en0',))), watcher.net_digest(snap(interfaces=('en0', 'en1'))))


A, B = dev(0x1234, 0x5678, 'Widget'), dev(0x1234, 0x5679, 'Widget Two')
APPLE = dev(0x05ac, 0x1905, 'Mac', classes=((2, 13),), ifaces=('en7',))


def rules(previous, current):
    return [f.rule for f in watcher.compare(previous, current)]


class Rules(unittest.TestCase):
    """Each rule on a case that must fire it and on a faulty case that must not."""

    def test_usb_added_and_removed(self):
        self.assertEqual(rules(snap(), snap([A])), ['USB_ADDED'])
        self.assertEqual(rules(snap([A]), snap()), ['USB_REMOVED'])
        self.assertEqual(rules(snap([A]), snap([A])), [], 'an unchanged tree has no finding')
        self.assertEqual(rules(snap([A, A]), snap([A])), ['USB_REMOVED'], 'one of two identical devices left')
        self.assertEqual(rules(snap([A]), snap([A, A])), ['USB_ADDED'], 'a second identical device is one addition')
        self.assertEqual(rules(snap([A]), snap([dev(0x1234, 0x5678, 'Renamed')])), ['USB_ADDED', 'USB_REMOVED'])

    def test_net_added_and_removed(self):
        self.assertEqual(rules(snap(interfaces=('en0',)), snap(interfaces=('en0', 'en9'))), ['NET_ADDED'])
        self.assertEqual(rules(snap(interfaces=('en0', 'en9')), snap(interfaces=('en0',))), ['NET_REMOVED'])
        self.assertEqual(rules(snap(interfaces=('en0',)), snap(interfaces=('en0',))), [])

    def test_apple_gadget_is_the_nobody_else_05ac_1905_or_a_q1n1_name_and_only_when_added(self):
        self.assertIn('APPLE_GADGET', rules(snap(), snap([APPLE])))
        self.assertIn('APPLE_GADGET', rules(snap(), snap([dev(0x1d6b, 0x0002, 'A16-Q1N1-EL2B')])))
        self.assertIn('APPLE_GADGET', rules(snap(), snap([dev(0x1d6b, 0x0002, 'q1n1 proxy')])))
        for label, case in (('another Apple product', dev(0x05ac, 0x1904, 'Mac')), ('the product id of another vendor', dev(0x1234, 0x1905, 'Mac')),
                            ('an unrelated name', dev(0x1d6b, 0x0002, 'Keyboard'))):
            with self.subTest(case=label):
                self.assertNotIn('APPLE_GADGET', rules(snap(), snap([case])))
        self.assertNotIn('APPLE_GADGET', rules(snap([APPLE]), snap([APPLE])), 'present at the baseline is not a finding')
        self.assertNotIn('APPLE_GADGET', rules(snap([APPLE]), snap()), 'a removal is not a gadget appearing')

    def test_usb_net_device_needs_a_network_class_interface(self):
        for label, classes in (('ECM', ((2, 6),)), ('NCM', ((2, 13),)), ('MBIM', ((2, 14),)), ('RNDIS', ((0xe0, 1),))):
            with self.subTest(kind=label):
                found = watcher.compare(snap(), snap([dev(1, 2, 'Net', classes=classes)]))
                self.assertIn(label, [f.detail for f in found if f.rule == 'USB_NET_DEVICE'][0])
        for label, classes in (('CDC ACM serial', ((2, 2),)), ('mass storage', ((8, 6),)), ('HID', ((3, 1),)), ('none', ()), ('CDC data alone', ((10, 0),))):
            with self.subTest(case=label):
                self.assertNotIn('USB_NET_DEVICE', rules(snap(), snap([dev(1, 2, 'Other', classes=classes)])))
        self.assertNotIn('USB_NET_DEVICE', rules(snap([dev(1, 2, 'Net', classes=((2, 13),))]), snap([dev(1, 2, 'Net', classes=((2, 13),))])))

    def test_usb_net_iface_is_a_new_interface_below_a_usb_device_or_labelled_usb(self):
        owner = dev(1, 2, 'Adapter', ifaces=('en7',))
        self.assertIn('USB_NET_IFACE', rules(snap([owner]), snap([owner], interfaces=('en7',))))
        self.assertIn('USB_NET_IFACE', rules(snap(), snap(interfaces=('en8',), labels=(('en8', 'USB 10/100/1000 LAN'),))))
        self.assertIn('USB_NET_IFACE', rules(snap(), snap(interfaces=('en8',), labels=(('en8', 'Ethernet Adapter (en8)'),))))
        for label, current in (('a Wi-Fi label', snap(interfaces=('en9',), labels=(('en9', 'Wi-Fi'),))), ('no label', snap(interfaces=('en9',))),
                               ('a loopback name', snap(interfaces=('lo1',))), ('an interface already there', snap([owner], interfaces=('en7',)))):
            with self.subTest(case=label):
                previous = snap([owner], interfaces=('en7',)) if label == 'an interface already there' else snap()
                self.assertNotIn('USB_NET_IFACE', rules(previous, current))

    def test_every_digest_change_has_a_finding(self):
        pool = [A, B, APPLE, dev(7, 7, 'Seven')]
        for mask in range(16):
            for other in range(16):
                before, after = snap([d for i, d in enumerate(pool) if mask >> i & 1]), snap([d for i, d in enumerate(pool) if other >> i & 1])
                if watcher.usb_digest(before) != watcher.usb_digest(after):
                    self.assertTrue(rules(before, after), (mask, other))


class Loop(unittest.TestCase):
    def test_the_baseline_is_never_a_finding(self):
        counts, out, log = run_watch([frame([GADGET], interfaces=('lo0', 'en0', 'en7'))], duration=3)
        self.assertEqual(sum(counts[rule] for rule in watcher.RULES), 0)
        self.assertEqual(baseline_problems(out, [], late=False), [])
        self.assertNotIn('CHANGE', out)

    def test_a_gadget_appearing_is_printed_once_with_its_findings_and_logged(self):
        quiet, plugged = frame([HUB]), frame([HUB, GADGET], interfaces=('lo0', 'en0', 'en7'), pairs=(('en0', 'Ethernet'), ('en7', 'USB 10/100/1000 LAN')))
        counts, out, log = run_watch([quiet, quiet, quiet, plugged], duration=5)
        changes = [line for line in out.splitlines() if line.startswith('CHANGE')]
        self.assertEqual(sorted(line.split()[2] for line in changes), ['APPLE_GADGET', 'NET_ADDED', 'USB_ADDED', 'USB_NET_DEVICE', 'USB_NET_IFACE'])
        self.assertTrue(all(line.split()[1] == '2026-11-17T02:00:03Z' for line in changes), 'all at the tick the gadget came')
        self.assertEqual(counts['APPLE_GADGET'], 1)
        self.assertEqual(log.count('TICK '), 6)
        self.assertIn('SUMMARY ticks=6 USB_ADDED=1 USB_REMOVED=0 NET_ADDED=1 NET_REMOVED=0 APPLE_GADGET=1 USB_NET_DEVICE=1 USB_NET_IFACE=1 ERRORS=0', out)

    def test_an_unchanged_tree_prints_nothing_but_the_baseline_and_the_summary(self):
        _, out, log = run_watch([frame([HUB])], duration=4)
        self.assertEqual([line.split()[0] for line in out.splitlines()], ['BASELINE', 'SUMMARY'])
        self.assertEqual(log.count('TICK '), 5)
        self.assertEqual(len({line.split()[2] for line in log.splitlines() if line.startswith('TICK')}), 1, 'one digest for an unchanged tree')

    def test_a_failing_command_is_logged_and_never_a_removal(self):
        good, broken = frame([HUB, GADGET]), frame(usb=watcher.CommandError('usb timed out'))
        counts, out, log = run_watch([good, broken, broken, broken, broken, good], duration=5)
        self.assertEqual(counts['USB_REMOVED'], 0)
        self.assertEqual(counts['ERRORS'], 4)
        self.assertEqual(out.count('WARN'), 1, 'one warning for a streak of failures, at the third')
        self.assertIn('errors=usb', log)
        self.assertNotIn('CHANGE', out)

    def test_ctrl_c_ends_with_the_summary(self):
        def interrupted(name):
            raise KeyboardInterrupt
        out, log = io.StringIO(), io.StringIO()
        counts = watcher.watch(interrupted, 1.0, 0, log, out, clock=lambda: 0.0, sleep=lambda s: None, now=lambda: 'T')
        self.assertIn('SUMMARY ticks=0', out.getvalue())
        self.assertEqual(sum(counts.values()), 0)

    def test_a_source_that_fails_at_the_first_tick_gives_no_baseline_and_nothing_attached_is_an_addition(self):
        plugged = frame([HUB, GADGET], interfaces=('lo0', 'en0', 'en7'), pairs=(('en0', 'Ethernet'), ('en7', 'USB 10/100/1000 LAN')))
        for source in ('usb', 'ifconfig', 'ports'):
            with self.subTest(source=source):
                counts, out, log = run_watch([dict(plugged, **{source: watcher.CommandError(f'{source} failed')}), plugged], duration=3)
                self.assertEqual(sum(counts[rule] for rule in watcher.RULES), 0, 'what was attached at the first good tick is the baseline')
                self.assertEqual(baseline_problems(out, [f'{d.vidpid} {d.name}' for d in watcher.parse_usb(plugged['usb'])], late=True), [])
                self.assertNotIn('CHANGE', out)
                first = log.splitlines()[0]
                self.assertTrue(first.startswith('TICK ') and 'baseline=pending' in first and f'errors={source}' in first, first)
                self.assertNotIn('NO_BASELINE', out)
        for source in ('usb', 'ifconfig', 'ports'):
            with self.subTest(source=source, never=True):
                counts, out, _ = run_watch([dict(plugged, **{source: watcher.CommandError('x')})], duration=3)
                self.assertNotIn('BASELINE ', out.replace('NO_BASELINE', ''))
                self.assertIn('NO_BASELINE', out)
                self.assertEqual(counts['ERRORS'], 4)

    def test_an_exit_0_with_nothing_printed_is_an_error_and_never_a_removal(self):
        good = frame([HUB, GADGET], interfaces=('lo0', 'en0', 'en7'), pairs=(('en0', 'Ethernet'), ('en7', 'USB 10/100/1000 LAN')))
        empties = [dict(good, usb=''), dict(good, usb='\n  \n'), dict(good, usb='junk\n'), dict(good, ifconfig=''), dict(good, ports=' \n')]
        counts, out, log = run_watch([good] + empties + [good], duration=6)
        self.assertEqual((counts['USB_REMOVED'], counts['NET_REMOVED'], counts['ERRORS']), (0, 0, 5))
        self.assertNotIn('CHANGE', out)
        self.assertEqual([line.split('errors=')[1] for line in log.splitlines() if 'errors=' in line], ['usb', 'usb', 'usb', 'ifconfig', 'ports'])

    def test_an_empty_first_read_is_no_baseline_either(self):
        counts, out, log = run_watch([frame(), frame([HUB, GADGET])], duration=2)
        self.assertEqual(sum(counts[rule] for rule in watcher.RULES), 0)
        self.assertEqual(baseline_problems(out, [f'{d.vidpid} {d.name}' for d in watcher.parse_usb(ioreg_text([HUB, GADGET]))], late=True), [])
        self.assertIn('baseline=pending errors=usb', log)

    def test_a_mac_with_no_usb_device_needs_the_option_and_then_the_first_gadget_is_found(self):
        plugged = frame([GADGET], interfaces=('lo0', 'en0', 'en7'), pairs=(('en0', 'Ethernet'), ('en7', 'USB 10/100/1000 LAN')))
        counts, out, _ = run_watch([frame(), frame(), plugged], duration=3, allow_empty_usb=True)
        self.assertEqual((counts['USB_ADDED'], counts['APPLE_GADGET'], counts['ERRORS']), (1, 1, 0))
        self.assertEqual(baseline_problems(out, [], late=False), [])
        self.assertTrue(out.splitlines()[0].startswith('BASELINE ') and 'usb_n=0' in out.splitlines()[0])
        counts, out, _ = run_watch([frame(), frame(), plugged], duration=3)
        self.assertEqual((counts['USB_ADDED'], counts['APPLE_GADGET']), (0, 0), 'without the option the first read with a device is the baseline, as documented')
        self.assertEqual(counts['ERRORS'], 2)
        self.assertEqual(baseline_problems(out, ['05ac:1905 Mac'], late=True), [], 'and the screen says so, with the device that became the baseline')
        counts, _, _ = run_watch([frame(usb='junk\n'), plugged], duration=2, allow_empty_usb=True)
        self.assertEqual(counts['ERRORS'], 1, 'text that is not empty and shows no device is still a failure')

    def test_the_warning_for_an_ioreg_that_keeps_printing_nothing_names_the_option(self):
        counts, out, log = run_watch([frame()] * 4, duration=3)
        warnings = [line for line in out.splitlines() if line.startswith('WARN')]
        self.assertEqual(len(warnings), 1, 'one warning for a streak, at the third tick')
        self.assertTrue(warnings[0].startswith('WARN 2026-11-17T02:00:02Z usb failed 3 times in a row'), warnings[0])
        self.assertIn('--allow-empty-usb', warnings[0])
        self.assertTrue(warnings[0].endswith('and is not flagged)'), 'a late baseline lists the device (BASELINE ... DEVICE), so it is not "not reported": it is never flagged')
        self.assertNotIn('not reported', warnings[0])
        self.assertIn('NO_BASELINE', out)
        self.assertEqual(warnings, [line for line in log.splitlines() if line.startswith('WARN')])
        _, out, _ = run_watch([frame()] * 4, duration=3, allow_empty_usb=True)
        self.assertNotIn('WARN', out, 'with the option an empty ioreg is an empty tree, not a failure')
        for failure in (watcher.CommandError('usb timed out'), watcher.CommandError('usb exited 1'), 'junk\n'):
            with self.subTest(failure=str(failure)):
                _, out, _ = run_watch([frame(usb=failure)] * 4, duration=3)
                self.assertEqual(len([line for line in out.splitlines() if line.startswith('WARN')]), 1)
                self.assertNotIn('--allow-empty-usb', out, 'a failure that is not an empty text is not helped by the option')
        _, out, _ = run_watch([frame([HUB], ifconfig='')] * 4, duration=3)
        self.assertIn('WARN', out)
        self.assertNotIn('--allow-empty-usb', out, 'the option is for ioreg alone')

    def test_each_rule_of_the_baseline_check_has_a_faulty_screen_it_rejects(self):
        devices = ['05ac:1905 Mac']
        good = 'BASELINE 2026-11-17T02:00:01Z LATE after 1 pending ticks usb=abc net=def usb_n=1 net_n=2\nBASELINE 2026-11-17T02:00:01Z DEVICE 05ac:1905 Mac\n'
        self.assertEqual(baseline_problems(good, devices, late=True), [])
        for label, text, late in (('no devices on a late baseline', good.splitlines()[0] + '\n', True), ('no LATE marker', good.replace(' LATE after 1 pending ticks', ''), True),
                                  ('a marker on a baseline of the first tick', good, False), ('two baselines', good + good, True),
                                  ('no baseline', 'SUMMARY ticks=1\n', True), ('a wrong device', good.replace('Mac', 'Other'), True)):
            with self.subTest(case=label):
                self.assertTrue(baseline_problems(text, devices, late), label)

    def test_serials_locations_and_macs_never_reach_the_log_or_the_output(self):
        plugged = frame([HUB, dict(GADGET, serial=SERIAL, loc=LOCATION)], interfaces=('en7',), pairs=(('en7', 'USB 10/100/1000 LAN'),))
        _, out, log = run_watch([frame([HUB]), plugged], duration=2)
        for text in (out, log):
            for secret in (SERIAL, MAC, MAC.replace(':', ''), str(LOCATION), f'{LOCATION:08x}'):
                self.assertNotIn(secret, text)

    def test_the_log_is_ascii_and_a_name_with_other_characters_is_replaced(self):
        self.assertEqual(watcher.ascii_safe('Caf\u00e9 \u2013 USB'), 'Caf? ? USB')
        _, out, log = run_watch([frame(), frame([dev_dict_unicode()])], duration=1)
        self.assertTrue(out.isascii() and log.isascii())


def dev_dict_unicode():
    return {'vid': 0x1234, 'pid': 0x1, 'name': 'Caf\u00e9 \u2013 Dock'}


class Commands(unittest.TestCase):
    def test_the_three_commands_are_fixed_read_only_tuples(self):
        self.assertEqual([name for name, _ in watcher.COMMANDS], ['usb', 'ifconfig', 'ports'])
        self.assertEqual(command_problems(watcher.COMMANDS), [])
        self.assertEqual(dict(watcher.COMMANDS)['ports'], ('networksetup', '-listallhardwareports'))
        self.assertEqual(dict(watcher.COMMANDS)['ifconfig'], ('ifconfig', '-l'))

    def test_a_faulty_command_list_is_rejected(self):
        for label, commands in (('sudo', (('usb', ('sudo', 'ioreg')),)), ('a shell string', (('usb', 'ioreg -l'),)), ('a write verb', (('ports', ('networksetup', '-setairportpower', 'en0', 'off')),)),
                                ('a redirect', (('ifconfig', ('ifconfig', '-l', '>', 'x')),)), ('another program', (('usb', ('kextstat',)),))):
            with self.subTest(case=label):
                self.assertTrue(command_problems(commands))

    def test_commands_run_without_a_shell_and_a_failure_is_a_command_error(self):
        seen = []

        def fake_run(argv, **kwargs):
            seen.append((argv, kwargs))
            return mock.Mock(returncode=0, stdout='ok')

        with mock.patch.object(watcher.subprocess, 'run', fake_run):
            for name, argv in watcher.COMMANDS:
                self.assertEqual(watcher.run_command(name), 'ok')
        self.assertEqual([a for a, _ in seen], [argv for _, argv in watcher.COMMANDS])
        self.assertTrue(all(not k.get('shell') and k.get('timeout') and k.get('capture_output') for _, k in seen))
        for label, effect in (('a timeout', watcher.subprocess.TimeoutExpired('x', 1)), ('a missing tool', FileNotFoundError('x'))):
            with self.subTest(case=label), mock.patch.object(watcher.subprocess, 'run', side_effect=effect), self.assertRaises(watcher.CommandError):
                watcher.run_command('usb')
        with mock.patch.object(watcher.subprocess, 'run', return_value=mock.Mock(returncode=1, stdout='')), self.assertRaises(watcher.CommandError):
            watcher.run_command('usb')

    def test_the_tool_source_passes_its_checker(self):
        self.assertEqual(source_problems(SOURCE.read_text()), [])

    def test_the_source_checker_rejects_each_forbidden_thing(self):
        for label, code in (('sudo in a string', 'x = ("sudo", "ioreg")'), ('a shell', 'import subprocess\nsubprocess.run("ls", shell=True)'),
                            ('os.system', 'import os\nos.system("ls")'), ('an unknown import', 'import json'), ('a socket', 'import socket'),
                            ('a device path', 'p = "/dev/tty.x"'), ('Popen', 'import subprocess\nsubprocess.Popen(["ls"])'), ('stdin', 'import subprocess\nsubprocess.run(["ls"], stdin=1)'),
                            ('eval', 'eval("1")')):
            with self.subTest(case=label):
                self.assertTrue(source_problems(code), label)
        self.assertEqual(source_problems('"""sudo and /dev/ in a docstring are fine"""\nx = 1\n'), [])


class Main(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(BUILD, ignore_errors=True)
        self.addCleanup(shutil.rmtree, BUILD, True)

    def test_main_writes_one_log_under_the_directory_given_and_prints_its_name(self):
        clock = [0.0]
        out = io.StringIO()
        status = watcher.main(['--interval', '0.5', '--duration', '1', '--log-dir', str(BUILD)], runner=Frames([frame([HUB]), frame([HUB, GADGET])]), out=out,
                              clock=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        self.assertEqual(status, 0)
        (log,) = BUILD.glob('watch-*.log')
        text = log.read_text()
        self.assertTrue(text.startswith('# sp11_mac_usb_watch interval=0.5 commands: ioreg -r -c IOUSBHostDevice -l -w0 | ifconfig -l | networksetup -listallhardwareports'))
        self.assertIn('APPLE_GADGET', text)
        self.assertTrue(out.getvalue().startswith('LOG '))

    def test_a_bad_interval_or_duration_is_refused(self):
        for argv in (['--interval', '0.1'], ['--interval', '0'], ['--duration', '-1']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                watcher.main(argv + ['--log-dir', str(BUILD)], runner=Frames([frame()]), out=io.StringIO())
        self.assertFalse(BUILD.exists() and list(BUILD.glob('*.log')), 'a refused run leaves no log')

    def test_the_option_for_a_mac_with_no_usb_device_reaches_the_watcher(self):
        clock = [0.0]
        out = io.StringIO()
        status = watcher.main(['--interval', '0.5', '--duration', '1', '--log-dir', str(BUILD), '--allow-empty-usb'], runner=Frames([frame(), frame([GADGET])]), out=out,
                              clock=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        self.assertEqual(status, 0)
        (log,) = BUILD.glob('watch-*.log')
        self.assertIn('APPLE_GADGET', log.read_text())


@unittest.skipUnless(os.environ.get('SP11_MAC_WATCH_REAL') == '1', 'SP11_MAC_WATCH_REAL is not 1: this Mac\'s real ioreg output is not parsed (opt in; structure only)')
class RealCommands(unittest.TestCase):
    def test_the_real_output_parses_to_integers_and_names_and_one_digest(self):
        snapshot, errors = watcher.gather(watcher.run_command)
        self.assertEqual(errors, [])
        self.assertTrue(all(isinstance(d.vendor, int) and isinstance(d.product, int) and d.name for d in snapshot.devices))
        self.assertEqual(watcher.usb_digest(snapshot), watcher.usb_digest(watcher.gather(watcher.run_command)[0]))


if __name__ == '__main__':
    sys.exit(0 if unittest.main(verbosity=1, exit=False).result.wasSuccessful() else 1)
