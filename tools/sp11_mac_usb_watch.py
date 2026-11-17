#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read-only Mac-side watcher for the Surface Pro 11 xHCI S1 test (docs/SP11-XHCI-S1.md, section 8).

Once per interval it reads the USB device tree and the network interface list the way a user can: `ioreg`, `ifconfig -l` and
`networksetup -listallhardwareports`. No sudo, no device opened, no socket, no write outside its log under build/. It reduces what it
reads to a digest of vendor id, product id and product name (serial numbers, location IDs, registry IDs and MAC addresses are never
read into the digest or the log), records one line per tick, and prints a line on every change. A parser says whether a USB device, a
USB network device, a new network interface or an Apple gadget appeared (the Mac's NCM function 05ac:1905 as the A16 sees it, or a
device named like q1n1's). It decides nothing about the test: a quiet log is a result.

    python3 tools/sp11_mac_usb_watch.py [--interval 1.0] [--duration SECONDS] [--log-dir DIR] [--allow-empty-usb]

The baseline is the first tick at which all three sources answered; a source that exits 0 with nothing printed has failed (never "everything was
removed"), and so has one that prints nothing it can read. `--allow-empty-usb` takes an empty `ioreg` as "no USB device attached", for a Mac that has none.
Without it a Mac with no USB device takes no baseline until one appears, and that first device is then the baseline and not a finding: the warning after
three empty ticks names the option, and a baseline that is not the first tick's (`BASELINE ... LATE after <n> pending ticks`) prints its devices.

Stop with Ctrl-C; the summary line is printed and logged. Evidence: EXPERIMENTAL; the parser is tested on synthetic text in the format
`ioreg -r -c IOUSBHostDevice -l -w0` printed on the author's Mac. What the Mac shows when it is the gadget behind a host-only SP11 is UNKNOWN.
The log may hold device names of the Mac's own accessories: sanitise it before it goes anywhere.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = ROOT / 'build' / 'sp11-mac-usb-watch'
# The only commands this tool runs, each user level and read only, without a shell.
COMMANDS = (
    ('usb', ('ioreg', '-r', '-c', 'IOUSBHostDevice', '-l', '-w0')),
    ('ifconfig', ('ifconfig', '-l')),
    ('ports', ('networksetup', '-listallhardwareports')),
)
COMMAND_TIMEOUT_S = 5.0
WARN_AFTER_FAILURES = 3
EMPTY_TEXT = 'printed nothing'
# Said with the warning for an `ioreg` that keeps printing nothing: on a Mac with no USB device that is the normal answer, and without the option the first
# device that appears becomes the baseline, which is never a finding.
EMPTY_USB_HINT = ' (a Mac with no USB device attached needs --allow-empty-usb: without it the first device that appears is the baseline and is not flagged)'
APPLE_GADGET = (0x05ac, 0x1905)
NAME_PREFIXES = ('q1n1', 'a16-q1n1', 'sp11-q1n1')
# (interface class, subclass) of a USB network function: CDC ECM, CDC NCM, CDC MBIM, wireless controller RNDIS.
NETWORK_CLASSES = {(0x02, 0x06): 'ECM', (0x02, 0x0d): 'NCM', (0x02, 0x0e): 'MBIM', (0xe0, 0x01): 'RNDIS'}
NETWORK_NODE_CLASSES = ('IOEthernetInterface', 'IONetworkInterface')
USB_LABEL = re.compile(r'usb|ncm|cdc|ethernet adapter', re.I)
RULES = ('USB_ADDED', 'USB_REMOVED', 'NET_ADDED', 'NET_REMOVED', 'APPLE_GADGET', 'USB_NET_DEVICE', 'USB_NET_IFACE')

NODE = re.compile(r'^(?P<prefix>[ |]*)\+-o (?P<name>.*?)  <class (?P<cls>[A-Za-z0-9_]+)(?P<rest>[^>]*)>')
PROP = re.compile(r'^[ |]*"(?P<key>[^"]+)" = (?P<value>.*)$')
ENTRY_ID = re.compile(r'\bid (0x[0-9a-fA-F]+)')


class CommandError(Exception):
    pass


@dataclass(frozen=True)
class UsbDevice:
    vendor: int
    product: int
    name: str
    classes: tuple = ()     # sorted (interface class, subclass) pairs of the device's own interfaces
    ifaces: tuple = ()      # sorted BSD names of the network interfaces below the device

    @property
    def vidpid(self):
        return f'{self.vendor:04x}:{self.product:04x}'

    @property
    def key(self):
        return (self.vendor, self.product, self.name)


@dataclass(frozen=True)
class Snapshot:
    devices: tuple = ()     # UsbDevice, sorted by key
    interfaces: tuple = ()  # names from `ifconfig -l`, sorted
    labels: tuple = ()      # (device name, hardware port label), sorted


@dataclass(frozen=True)
class Finding:
    rule: str
    detail: str


@dataclass
class Node:
    depth: int
    name: str
    cls: str
    entry: str
    props: dict
    children: list


def ascii_safe(text):
    """Printable ASCII only: the log is a plain text file that may be pasted anywhere."""
    return re.sub(r'[^\x20-\x7e]', '?', str(text))


def parse_value(text):
    text = text.strip()
    if re.fullmatch(r'-?\d+', text):
        return int(text)
    if re.fullmatch(r'0x[0-9a-fA-F]+', text):
        return int(text, 16)
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text


def parse_nodes(text):
    """The registry nodes of `ioreg -l` text as a forest, by the depth of each `+-o` marker. Properties belong to the node printed above them."""
    roots, stack, current = [], [], None
    for line in text.splitlines():
        match = NODE.match(line)
        if match:
            depth = len(match['prefix'])
            entry = ENTRY_ID.search(match['rest'])
            current = Node(depth, match['name'].strip(), match['cls'], entry.group(1) if entry else '', {}, [])
            while stack and stack[-1].depth >= depth:
                stack.pop()
            (stack[-1].children if stack else roots).append(current)
            stack.append(current)
            continue
        prop = PROP.match(line)
        if prop and current is not None and prop['key'] not in current.props:
            current.props[prop['key']] = parse_value(prop['value'])
    return roots


def below(node):
    """The nodes under `node` that belong to it: the walk stops at a nested USB device, which has its own interfaces."""
    for child in node.children:
        if child.cls == 'IOUSBHostDevice':
            continue
        yield child
        yield from below(child)


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.children)


def parse_usb(text):
    """The USB devices of `ioreg -r -c IOUSBHostDevice -l -w0` text: vendor id, product id, name, interface classes and the network interfaces below
    each. A device that is printed twice (as a root and under its hub) is one device, by its registry ID."""
    found = {}
    for node in walk(parse_nodes(text)):
        vendor, product = node.props.get('idVendor'), node.props.get('idProduct')
        if node.cls != 'IOUSBHostDevice' or not isinstance(vendor, int) or not isinstance(product, int):
            continue
        name = node.props.get('USB Product Name') or node.props.get('kUSBProductString') or node.name.split('@')[0]
        classes, ifaces = set(), set()
        for inner in below(node):
            if inner.cls == 'IOUSBHostInterface' and isinstance(inner.props.get('bInterfaceClass'), int):
                classes.add((inner.props['bInterfaceClass'], inner.props.get('bInterfaceSubClass')))
            if inner.cls in NETWORK_NODE_CLASSES and isinstance(inner.props.get('BSD Name'), str):
                ifaces.add(inner.props['BSD Name'])
        device = UsbDevice(vendor, product, ' '.join(str(name).split()), tuple(sorted(classes, key=lambda c: (c[0], -1 if c[1] is None else c[1]))), tuple(sorted(ifaces)))
        found.setdefault(node.entry or f'{device.key}', device)
    return tuple(sorted(found.values(), key=lambda d: (d.key, d.classes, d.ifaces)))


def parse_ifconfig_list(text):
    return tuple(sorted(set(text.split())))


def parse_hardware_ports(text):
    """((device, hardware port label), ...) of `networksetup -listallhardwareports`; the Ethernet Address lines are dropped here, unread."""
    pairs, label = [], None
    for line in text.splitlines():
        if line.startswith('Hardware Port: '):
            label = line[len('Hardware Port: '):].strip()
        elif line.startswith('Device: ') and label is not None:
            pairs.append((line[len('Device: '):].strip(), label))
            label = None
    return tuple(sorted(pairs))


def usb_digest(snapshot):
    """12 hex digits over the sorted `vid:pid name` lines: stable for an unchanged tree, blind to serials, locations and registry IDs."""
    body = ''.join(f'{d.vidpid} {d.name}\n' for d in snapshot.devices)
    return hashlib.sha256(body.encode('utf-8', 'replace')).hexdigest()[:12]


def net_digest(snapshot):
    return hashlib.sha256(''.join(f'{name}\n' for name in snapshot.interfaces).encode('ascii', 'replace')).hexdigest()[:12]


def surplus(devices, other):
    """The devices of `devices` beyond as many of the same key as `other` holds (a second identical device is one addition, not two)."""
    spare, extra = Counter(d.key for d in other), []
    for device in devices:
        if spare[device.key] > 0:
            spare[device.key] -= 1
        else:
            extra.append(device)
    return extra


def compare(previous, current):
    """The findings between two snapshots, in a fixed order. Everything is a set difference of what the snapshots hold; the first snapshot of a
    run is the baseline and is never compared with anything."""
    findings = []
    added, removed = surplus(current.devices, previous.devices), surplus(previous.devices, current.devices)
    findings += [Finding('USB_ADDED', f'{d.vidpid} {d.name}') for d in added]
    findings += [Finding('USB_REMOVED', f'{d.vidpid} {d.name}') for d in removed]
    new_ifaces = sorted(set(current.interfaces) - set(previous.interfaces))
    gone_ifaces = sorted(set(previous.interfaces) - set(current.interfaces))
    findings += [Finding('NET_ADDED', name) for name in new_ifaces]
    findings += [Finding('NET_REMOVED', name) for name in gone_ifaces]
    for device in added:
        if (device.vendor, device.product) == APPLE_GADGET or device.name.lower().startswith(NAME_PREFIXES):
            findings.append(Finding('APPLE_GADGET', f'{device.vidpid} {device.name}'))
        kinds = sorted({NETWORK_CLASSES[c] for c in device.classes if c in NETWORK_CLASSES})
        if kinds:
            findings.append(Finding('USB_NET_DEVICE', f'{device.vidpid} {"/".join(kinds)} {device.name}'))
    labels = dict(current.labels)
    for name in new_ifaces:
        owner = next((d for d in current.devices if name in d.ifaces), None)
        if owner is not None:
            findings.append(Finding('USB_NET_IFACE', f'{name} below {owner.vidpid} {owner.name}'))
        elif USB_LABEL.search(labels.get(name, '')):
            findings.append(Finding('USB_NET_IFACE', f'{name} labelled {labels[name]}'))
    return findings


def run_command(name, timeout=COMMAND_TIMEOUT_S):
    """The text of one of the COMMANDS. CommandError for a missing tool, a failure or a timeout: never fatal for the watcher."""
    argv = dict(COMMANDS)[name]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise CommandError(f'{name} timed out') from None
    except OSError as error:
        raise CommandError(f'{name} could not run: {error.__class__.__name__}') from None
    if done.returncode != 0:
        raise CommandError(f'{name} exited {done.returncode}')
    return done.stdout


def gather(runner, previous=None, allow_empty_usb=False, reasons=None):
    """(Snapshot or None, [names of the sources that failed]).

    A source fails if its command fails, times out, or exits 0 with nothing printed (an empty text), or prints text from which nothing can be read: a Mac that
    is up lists at least an interface and a hardware port, and an empty answer is never taken to mean that everything was removed. `ioreg` alone may be
    empty because no USB device is attached; `allow_empty_usb` takes a text with nothing but white space as an empty tree (a text that is not empty and
    shows no device is still a failure). `reasons`, if given, is filled with the message of each failure, by source name.

    Until a baseline exists (`previous` is None) the result is None unless all three sources answered in this call: the baseline is the first tick at which
    all of them did, because a source that failed would otherwise read as an empty tree and the next tick would report everything attached as an addition.
    After the baseline a failed source keeps its previous value, so that an error is never a removal."""
    kept = previous or Snapshot()
    parsed, errors = {'usb': kept.devices, 'ifconfig': kept.interfaces, 'ports': kept.labels}, []
    parsers = {'usb': parse_usb, 'ifconfig': parse_ifconfig_list, 'ports': parse_hardware_ports}
    for name, _ in COMMANDS:
        try:
            text = runner(name)
            if not text.strip():
                if name != 'usb' or not allow_empty_usb:
                    raise CommandError(f'{name} {EMPTY_TEXT}')
                value = ()
            else:
                value = parsers[name](text)
                if not value:
                    raise CommandError(f'{name} printed nothing it could read')
            parsed[name] = value
        except CommandError as error:
            errors.append(name)
            if reasons is not None:
                reasons[name] = str(error)
    if previous is None and errors:
        return None, errors
    return Snapshot(parsed['usb'], parsed['ifconfig'], parsed['ports']), errors


def utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def watch(runner, interval, duration, log, out, clock=time.monotonic, sleep=time.sleep, now=utc_now, allow_empty_usb=False):
    """Tick until `duration` seconds have passed (0: until Ctrl-C). Prints and logs the baseline (the first tick at which all three sources answered; ticks
    before it are logged as `baseline=pending` with their errors, and nothing in them is a finding), every finding, the first failures of a source and the
    summary; logs every tick. A baseline that is not the first tick's (LATE) is the one at which whatever was attached already counts as no addition: its
    devices are printed too, so that the screen shows what became the baseline. Returns the Counter of the summary."""
    counts, streak, previous, ticks, start = Counter(), Counter(), None, 0, clock()

    def say(line, to_out=True):
        line = ascii_safe(line)
        log.write(line + '\n')
        if to_out:
            out.write(line + '\n')
            out.flush()

    try:
        while True:
            stamp, reasons = now(), {}
            snapshot, errors = gather(runner, previous, allow_empty_usb, reasons)
            if snapshot is None:
                tick = f'TICK {stamp} baseline=pending'
            else:
                tick = f'TICK {stamp} usb={usb_digest(snapshot)} net={net_digest(snapshot)} usb_n={len(snapshot.devices)} net_n={len(snapshot.interfaces)}'
            say(tick + (' errors=' + ','.join(errors) if errors else ''), to_out=False)
            for name, _ in COMMANDS:
                streak[name] = streak[name] + 1 if name in errors else 0
                if name in errors:
                    counts['ERRORS'] += 1
                if streak[name] == WARN_AFTER_FAILURES:
                    hint = EMPTY_USB_HINT if name == 'usb' and reasons.get(name) == f'usb {EMPTY_TEXT}' else ''
                    say(f'WARN {stamp} {name} failed {WARN_AFTER_FAILURES} times in a row{hint}')
            if snapshot is not None and previous is None:
                late = ticks > 0
                say(f'BASELINE {stamp}' + (f' LATE after {ticks} pending ticks' if late else '') +
                    f' usb={usb_digest(snapshot)} net={net_digest(snapshot)} usb_n={len(snapshot.devices)} net_n={len(snapshot.interfaces)}')
                for device in snapshot.devices:
                    say(f'BASELINE {stamp} DEVICE {device.vidpid} {device.name}', to_out=late)
            elif snapshot is not None:
                for finding in compare(previous, snapshot):
                    counts[finding.rule] += 1
                    say(f'CHANGE {stamp} {finding.rule} {finding.detail}')
            previous = snapshot if snapshot is not None else previous
            ticks += 1
            if duration and clock() - start >= duration:
                break
            sleep(interval)
    except KeyboardInterrupt:
        pass
    say(f'SUMMARY ticks={ticks} ' + ' '.join(f'{rule}={counts[rule]}' for rule in RULES) + f' ERRORS={counts["ERRORS"]}' + (' NO_BASELINE' if previous is None else ''))
    return counts


def main(argv=None, runner=run_command, out=None, clock=time.monotonic, sleep=time.sleep):
    parser = argparse.ArgumentParser(description='Read-only Mac-side USB and network watcher for the SP11 xHCI S1 test (docs/SP11-XHCI-S1.md section 8).')
    parser.add_argument('--interval', type=float, default=1.0, help='seconds between two reads, at least 0.2 (default 1)')
    parser.add_argument('--duration', type=float, default=0.0, help='stop after this many seconds (default: until Ctrl-C)')
    parser.add_argument('--log-dir', type=Path, default=DEFAULT_LOG_DIR, help='where the log goes (default: build/sp11-mac-usb-watch)')
    parser.add_argument('--allow-empty-usb', action='store_true',
                        help='take an empty ioreg text as "no USB device attached" (for a Mac with none: otherwise an empty text is an error and no baseline is taken)')
    args = parser.parse_args(argv)
    if args.interval < 0.2 or args.duration < 0:
        parser.error('--interval is at least 0.2 and --duration is not negative')
    out = out if out is not None else sys.stdout
    args.log_dir.mkdir(parents=True, exist_ok=True)
    path = args.log_dir / f'watch-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.log'
    with open(path, 'a', encoding='ascii', errors='replace', buffering=1) as log:
        log.write(f'# sp11_mac_usb_watch interval={args.interval} commands: ' + ' | '.join(' '.join(command) for _, command in COMMANDS) + '\n')
        out.write(f'LOG {path}\n')
        watch(runner, args.interval, args.duration, log, out, clock=clock, sleep=sleep, allow_empty_usb=args.allow_empty_usb)
    return 0


if __name__ == '__main__':
    sys.exit(main())
