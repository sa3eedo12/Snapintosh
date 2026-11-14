# SPDX-License-Identifier: MIT
"""Emulator support for tools/test-sp11-autorun-qemu.py: stand-in images, a FAT16 stick with long names, a boot.

The autorun script names files such as SLBOUNCE-DEVHE-MARK.EFI, which are not 8.3 names, so the 8.3-only writer of
probes/uefi/host/fatimg.py cannot hold them. LongNameFat16Image reuses that module's geometry and boot sector and
adds the long-name directory entries; its output is read back with fatimg.read_fat16, an independent reader, in the
test, and the firmware's own FAT driver is the third judge when the stick is booted.

Only regular files are ever handed to the emulator (the firmware, a copy of its variable store, the stick image),
and the emulator's monitor is a pipe, as in probes/uefi/host/qemu_probe.py, whose asset search and command line this
module reuses. Nothing under /Volumes or /dev is read or written, and no host path is shared with the guest.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'probes' / 'uefi' / 'host'))
import fatimg        # noqa: E402
import qemu_probe    # noqa: E402

TOOL_DIRS = ('/opt/homebrew/opt/llvm/bin', '/opt/homebrew/opt/lld/bin', '/opt/homebrew/bin')
DUMMY_SOURCE = ROOT / 'tools' / 'sp11-autorun-dummy.c'
# The flags of tools/build-uefi-probe.py: the same compiler pairing builds the real UEFI images of this tree.
CFLAGS = ('--target=aarch64-pc-windows-msvc', '-std=c11', '-O2', '-ffreestanding', '-fno-builtin',
          '-fno-stack-protector', '-fshort-wchar', '-mgeneral-regs-only', '-mno-outline-atomics',
          '-mno-incremental-linker-compatible', '-Wall', '-Wextra', '-Werror')
LDFLAGS = ('/entry:efi_main', '/nodefaultlib', '/machine:arm64', '/dynamicbase', '/Brepro')
SUBSYSTEMS = {'DRIVER': 'efi_runtime_driver', 'APP': 'efi_application'}
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
PROMPT_AT_END = re.compile(r'(?:Shell|FS\d+:\\[^>\n]*)> $')


class Unavailable(Exception):
    """A tool or firmware the emulator tests need is not installed; the caller SKIPs."""


def find_tool(name: str) -> str:
    path = shutil.which(name, path=os.pathsep.join(TOOL_DIRS + (os.environ.get('PATH', ''),)))
    if not path:
        raise Unavailable(f'{name} is not installed')
    return path


def build_dummy(tag: str, kind: str, workdir: Path, hang: bool = False, status: int = 0) -> bytes:
    """One stand-in image: `kind` is DRIVER (what the Shell's `load` accepts) or APP; it prints a line naming `tag`.

    `hang` makes it never return; `status` is the EFI_STATUS its entry point returns (nonzero rehearses a failure).
    """
    clang, lld = find_tool('clang'), find_tool('lld-link')
    workdir.mkdir(parents=True, exist_ok=True)
    stem = f'{tag}-{kind}{"-HANG" if hang else ""}{f"-S{status:X}" if status else ""}'
    obj, image = workdir / f'{stem}.obj', workdir / f'{stem}.efi'
    defines = [f'-DDUMMY_TAG="{tag}"', f'-DDUMMY_KIND="{kind}"'] + (['-DDUMMY_HANG=1'] if hang else [])
    if status:
        defines.append(f'-DDUMMY_STATUS={status:#x}ull')
    for command in ([clang, *CFLAGS, *defines, '-c', str(DUMMY_SOURCE), '-o', str(obj)],
                    [lld, f'/subsystem:{SUBSYSTEMS[kind]}', *LDFLAGS, str(obj), f'/out:{image}']):
        done = subprocess.run(command, capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(f'{command[0]} failed:\n{done.stdout}{done.stderr}')
    return image.read_bytes()


# ---------------------------------------------------------------------------------------- FAT16 with long names

_SHORT_CHARS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&\'()-@^_`{}~')


def _is_short_name(name: str) -> bool:
    base, _, ext = name.partition('.')
    return (bool(base) and len(base) <= 8 and len(ext) <= 3 and '.' not in ext
            and all(c in _SHORT_CHARS for c in base + ext))


def _short_alias(name: str, taken: set) -> bytes:
    upper = name.upper()
    if _is_short_name(upper) and upper not in taken:
        alias = upper
    else:
        base, _, ext = upper.rpartition('.') if '.' in upper else (upper, '', '')
        base = ''.join(c for c in base if c in _SHORT_CHARS)
        ext = ''.join(c for c in ext if c in _SHORT_CHARS)[:3]
        for number in range(1, 1000):
            tail = f'~{number}'
            alias = base[:8 - len(tail)] + tail + (f'.{ext}' if ext else '')
            if alias not in taken:
                break
    taken.add(alias)
    stem, _, ext = alias.partition('.')
    return stem.ljust(8).encode('ascii') + ext.ljust(3).encode('ascii')


def _checksum(short11: bytes) -> int:
    total = 0
    for byte in short11:
        total = (((total & 1) << 7) + (total >> 1) + byte) & 0xFF
    return total


def _long_name_entries(name: str, short11: bytes) -> list:
    """The directory entries that carry `name`, highest sequence number first (FAT specification, long names)."""
    units = [ord(c) for c in name]
    if len(units) % 13:
        units.append(0)
        units += [0xFFFF] * (-len(units) % 13)
    count = len(units) // 13
    out = []
    for seq in range(count, 0, -1):
        part = units[(seq - 1) * 13:seq * 13]
        raw = bytearray(32)
        raw[0] = seq | (0x40 if seq == count else 0)
        raw[1:11] = struct.pack('<5H', *part[0:5])
        raw[11] = fatimg.ATTR_LFN
        raw[13] = _checksum(short11)
        raw[14:26] = struct.pack('<6H', *part[5:11])
        raw[28:32] = struct.pack('<2H', *part[11:13])
        out.append(bytes(raw))
    return out


class LongNameFat16Image(fatimg.Fat16Image):
    """fatimg.Fat16Image with any ASCII file name: a name that is not an exact 8.3 name gets long-name entries."""

    def mkdir(self, path: str):
        node = self.root
        for part in filter(None, path.split('/')):
            node = node.children.setdefault(part, fatimg._Dir())
            if not isinstance(node, fatimg._Dir):
                raise fatimg.FatError(f'{part!r} is a file')
        return node

    def add_file(self, path: str, data: bytes) -> None:
        parent, _, name = path.rpartition('/')
        if not name or not name.isascii() or '/' in name or '\\' in name:
            raise fatimg.FatError(f'cannot store {path!r}')
        self.mkdir(parent).children[name] = bytes(data)

    def to_bytes(self) -> bytes:
        cluster_bytes = self.spc * fatimg.SECTOR
        data_start = (self.reserved + self.nfats * self.fat_sectors + self.root_sectors) * fatimg.SECTOR
        img = bytearray(self.total_sectors * fatimg.SECTOR)
        fat = [0] * (self.clusters + 2)
        fat[0], fat[1] = 0xFFF8, 0xFFFF
        next_cluster = [2]

        def alloc(nbytes: int) -> list:
            count = max(1, -(-nbytes // cluster_bytes))
            first = next_cluster[0]
            if first + count > self.clusters + 2:
                raise fatimg.FatError('image is full')
            next_cluster[0] += count
            chain = list(range(first, first + count))
            for a, b in zip(chain, chain[1:]):
                fat[a] = b
            fat[chain[-1]] = 0xFFFF
            return chain

        def write_chain(chain, payload):
            for i, cluster in enumerate(chain):
                chunk = payload[i * cluster_bytes:(i + 1) * cluster_bytes]
                off = data_start + (cluster - 2) * cluster_bytes
                img[off:off + len(chunk)] = chunk

        def entry(name11, attr, first, size):
            return struct.pack('<11sBBBHHHHHHHI', name11, attr, 0, 0, 0, fatimg.FAT_DATE, fatimg.FAT_DATE, 0, 0,
                               fatimg.FAT_DATE, first, size)

        def emit(node, parent_first: int, is_root: bool) -> int:
            taken: set = set()
            names = {}
            for name in sorted(node.children):
                short11 = _short_alias(name, taken)
                exact_short = name == name.upper() and _is_short_name(name)
                names[name] = (short11, [] if exact_short else _long_name_entries(name, short11))
            n_entries = sum(1 + len(longs) for _, longs in names.values()) + (1 if is_root else 2)
            chain = None if is_root else alloc(n_entries * 32)
            first = 0 if is_root else chain[0]
            entries = []
            if is_root:
                entries.append(entry(self.label.upper().ljust(11).encode('ascii')[:11], fatimg.ATTR_VOLUME_ID, 0, 0))
            else:
                entries.append(entry(b'.          ', fatimg.ATTR_DIRECTORY, first, 0))
                entries.append(entry(b'..         ', fatimg.ATTR_DIRECTORY, parent_first, 0))
            for name in sorted(node.children):
                child = node.children[name]
                short11, longs = names[name]
                entries.extend(longs)
                if isinstance(child, fatimg._Dir):
                    entries.append(entry(short11, fatimg.ATTR_DIRECTORY, emit(child, first, False), 0))
                else:
                    first_cluster = 0
                    if child:
                        cchain = alloc(len(child))
                        write_chain(cchain, child)
                        first_cluster = cchain[0]
                    entries.append(entry(short11, 0x20, first_cluster, len(child)))
            blob = b''.join(entries)
            if is_root:
                if len(blob) > self.root_entries * 32:
                    raise fatimg.FatError('root directory is full')
                root_off = (self.reserved + self.nfats * self.fat_sectors) * fatimg.SECTOR
                img[root_off:root_off + len(blob)] = blob
            else:
                write_chain(chain, blob)
            return first

        emit(self.root, 0, True)
        boot = bytearray(fatimg.SECTOR)
        boot[0:3] = b'\xEB\x3C\x90'
        boot[3:11] = b'PROBEFAT'
        struct.pack_into('<HBHBHHBHHHII', boot, 11, fatimg.SECTOR, self.spc, self.reserved, self.nfats,
                         self.root_entries, self.total_sectors if self.total_sectors < 0x10000 else 0, 0xF8,
                         self.fat_sectors, 32, 2, 0, 0 if self.total_sectors < 0x10000 else self.total_sectors)
        struct.pack_into('<BBBI11s8s', boot, 36, 0x80, 0, 0x29, 0x50524F42,
                         self.label.upper().ljust(11).encode('ascii')[:11], b'FAT16   ')
        boot[510:512] = b'\x55\xAA'
        img[0:fatimg.SECTOR] = boot
        fat_bytes = b''.join(struct.pack('<H', v) for v in fat)
        for i in range(self.nfats):
            off = (self.reserved + i * self.fat_sectors) * fatimg.SECTOR
            img[off:off + len(fat_bytes)] = fat_bytes
        return bytes(img)


# ---------------------------------------------------------------------------------------------------- the boot

@dataclass
class Boot:
    serial: str          # the console log with escape sequences and carriage returns removed
    stick: object        # fatimg.FatTree read back from the image after the guest stopped
    seconds: float
    ended: str           # 'idle' (the Shell sat at a prompt), 'until' (the caller's condition held) or 'timeout'
    sent: tuple          # the (when, key) pairs actually sent through the monitor


_TYPED_KEYS = {' ': 'spc', ':': 'shift-semicolon', '\\': 'backslash', '\n': 'ret', '.': 'dot', '-': 'minus'}


def typed(text: str, prompt: str = 'Shell>') -> tuple:
    """`keys` for boot_stick that type `text` (lower-case letters, digits and the few characters of _TYPED_KEYS) at the
    Shell prompt. Each key is sent once the console has echoed the one before it: the monitor's sendkey holds a key for
    100 ms, so two quick presses of one key (the "11" of sp11) would otherwise overlap and the second would be lost."""
    keys = []
    for index, char in enumerate(text):
        name = _TYPED_KEYS.get(char, char if char.isascii() and (char.isdigit() or char.islower()) else None)
        if name is None:
            raise ValueError(f'no monitor key for {char!r}')
        keys.append((f'{prompt} {text[:index]}' if index else prompt, name))
    return tuple(keys)


def boot_stick(image: Path, shell: Path, workdir: Path, *, keys=(), until=None, timeout: float = 150.0,
               settle: float = 5.0, decoy=None, read_only: bool = False) -> Boot:
    """Boots the stick image in `image` once and returns what the console showed and what the stick holds after.

    `keys` is a sequence of (text, monitor key name): each key is sent once, the first time `text` shows in the
    console log (for the countdown line this is how ESC reaches the Shell while it still counts). The run ends when
    `until(serial)` is true, or when the Shell has sat at a prompt with no new output for `settle` seconds, or at
    `timeout`. The image is modified in place by the guest, so two boots of the same file are two boots of one stick.
    `decoy` is another image, presented on a PCI slot ahead of the stick's USB controller so that it, not the stick,
    is the first file system the Shell maps (the stick is then FS1:). `read_only` presents the stick write-protected.
    """
    assets = qemu_probe.find_assets()
    workdir.mkdir(parents=True, exist_ok=True)
    serial_path, vars_copy = workdir / 'serial.log', workdir / 'vars.fd'
    serial_path.write_bytes(b'')
    shutil.copyfile(assets.vars_fd, vars_copy)
    for path in (image, serial_path, vars_copy, Path(assets.code_fd), *([decoy] if decoy else [])):
        if not path.is_file():
            raise RuntimeError(f'{path} is not a regular file')
    cfg = qemu_probe.RunConfig(name='autorun', efi=shell, workdir=workdir, el2=False)
    command = qemu_probe.qemu_command(cfg, assets, vars_copy, image, serial_path) + ['-device', 'usb-kbd']
    if read_only:
        at = next(i for i, word in enumerate(command) if word.startswith('if=none,id=stick,'))
        command[at] += ',readonly=on'
    if decoy:
        at = command.index('qemu-xhci') - 1
        command[at:at] = ['-drive', f'if=none,id=decoy,format=raw,file={decoy}', '-device', 'virtio-blk-pci,drive=decoy']
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sink = bytearray()

    def drain(pipe):
        try:
            for chunk in iter(lambda: pipe.read1(4096), b''):
                sink.extend(chunk)
        except (OSError, ValueError):
            pass

    readers = [threading.Thread(target=drain, args=(pipe,), daemon=True) for pipe in (proc.stdout, proc.stderr)]
    for reader in readers:
        reader.start()

    def monitor(text: str) -> None:
        try:
            proc.stdin.write(text.encode() + b'\n')
            proc.stdin.flush()
        except (OSError, ValueError):
            pass

    start = time.monotonic()
    pending, sent = list(keys), []
    serial, ended, last_change, last_text = '', 'timeout', start, ''
    try:
        while time.monotonic() - start < timeout and proc.poll() is None:
            serial = ANSI.sub('', serial_path.read_bytes().decode('utf-8', 'replace')).replace('\r', '')
            if serial != last_text:
                last_text, last_change = serial, time.monotonic()
            for item in list(pending):
                if item[0] in serial:
                    monitor('sendkey ' + item[1])
                    sent.append(item)
                    pending.remove(item)
            if until is not None and until(serial):
                ended = 'until'
                break
            if PROMPT_AT_END.search(serial) and time.monotonic() - last_change >= settle:
                ended = 'idle'
                break
            time.sleep(0.25)
    finally:
        monitor('quit')
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        for reader in readers:
            reader.join(timeout=5)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            with contextlib.suppress(OSError, ValueError):
                pipe.close()
    serial = ANSI.sub('', serial_path.read_bytes().decode('utf-8', 'replace')).replace('\r', '')
    seconds = time.monotonic() - start
    return Boot(serial, fatimg.read_fat16(image.read_bytes()), seconds, ended, tuple(sent))
