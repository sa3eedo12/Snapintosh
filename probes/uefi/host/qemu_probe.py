# SPDX-License-Identifier: MIT
"""Runs a probe image under QEMU with EDK2 firmware and records what it does.

The probe is meant for a physical machine, so this runner is only a model of
it: QEMU's firmware, GOP, USB stack and PSCI differ from the real device, and
nothing observed here is evidence about that device. What it can do is run the
real UEFI image through ExitBootServices and the display loop, and compare the
dump on the virtual stick and the pixels on the virtual display against what
independent host code expects.

Safety: the emulator is only ever given regular files (pflash images, a FAT
image built here) and its monitor is a pipe on stdin/stdout, not a socket,
so nothing on the host other than those files is reachable from the guest.
No device node, USB device or serial port is opened by this module.

Typical use (see tools/test-uefi-probe.py for the full scenarios):

    assets = qemu_probe.find_assets()          # raises Unavailable if missing
    cfg = qemu_probe.RunConfig(name="el2", efi=probe_efi, workdir=tmp / "el2")
    result = qemu_probe.run(cfg, assets, font)
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fatimg  # noqa: E402
import screen  # noqa: E402

MARKER_PREFIX = "SP11PROBE "


class Unavailable(Exception):
    """The emulator or its firmware is not installed; the caller should SKIP."""


@dataclass(frozen=True)
class Assets:
    qemu: str
    code_fd: str
    vars_fd: str


def _share_dirs(qemu: str):
    prefixes = [Path(qemu).parent.parent, Path(os.path.realpath(qemu)).parent.parent]
    dirs = [str(p / sub) for p in prefixes
            for sub in ("share/qemu", "share/AAVMF", "share/edk2/aarch64")]
    dirs += ["/opt/homebrew/share/qemu", "/usr/local/share/qemu", "/usr/share/qemu",
             "/usr/share/AAVMF", "/usr/share/edk2/aarch64"]
    return list(dict.fromkeys(dirs))


_FIRMWARE_PAIRS = (
    ("edk2-aarch64-code.fd", "edk2-arm-vars.fd"),
    ("AAVMF_CODE.fd", "AAVMF_VARS.fd"),
    ("QEMU_EFI-pflash.raw", "vars-template-pflash.raw"),
)


def find_assets(environ=None) -> Assets:
    """Locates qemu-system-aarch64 and an EDK2 firmware pair.

    UEFI_PROBE_QEMU, UEFI_PROBE_EDK2_CODE and UEFI_PROBE_EDK2_VARS override
    the search. Raises Unavailable with the reason if anything is missing.
    """
    env = os.environ if environ is None else environ
    qemu = env.get("UEFI_PROBE_QEMU") or shutil.which("qemu-system-aarch64")
    if not qemu or not os.access(qemu, os.X_OK):
        raise Unavailable("qemu-system-aarch64 not found")
    code, varsfd = env.get("UEFI_PROBE_EDK2_CODE"), env.get("UEFI_PROBE_EDK2_VARS")
    if not (code and varsfd):
        for share in _share_dirs(qemu):
            for code_name, vars_name in _FIRMWARE_PAIRS:
                if os.path.isfile(os.path.join(share, code_name)) and \
                        os.path.isfile(os.path.join(share, vars_name)):
                    code, varsfd = os.path.join(share, code_name), os.path.join(share, vars_name)
                    break
            if code and varsfd:
                break
    if not (code and varsfd and os.path.isfile(code) and os.path.isfile(varsfd)):
        raise Unavailable("no EDK2 aarch64 firmware pair (code + vars) found")
    return Assets(qemu, code, varsfd)


@dataclass
class RunConfig:
    name: str
    efi: Path
    workdir: Path
    el2: bool = True              # virt,virtualization=on starts the guest at EL2
    smp: int = 1
    disk: str = "usb"             # "usb" (usb-storage behind xHCI) or "virtio" (virtio-blk)
    gop: bool = True              # attach ramfb
    extra_files: dict = field(default_factory=dict)  # stick path -> bytes
    memory: str = "1G"
    boot_timeout: float = 150.0   # QEMU start to the probe's first console line
    pre_ebs_timeout: float = 90.0
    capture_seconds: float = 45.0  # after "EBS STATE=CALLING"
    capture_interval: float = 0.5
    stop_when: Optional[Callable[["RunResult"], bool]] = None
    # For an image that is meant never to reach ExitBootServices: start capturing the
    # screen as soon as this predicate holds on the serial log (the wait for an EBS line
    # is replaced by it). None keeps the probe's own protocol.
    capture_when: Optional[Callable[["RunResult"], bool]] = None
    keep_workdir: bool = False


@dataclass
class Marker:
    tag: str
    fields: dict
    raw: str


@dataclass
class Sample:
    t: float
    digest: str
    screen: screen.Screen
    ppm: Optional[bytes] = None


@dataclass
class RunResult:
    name: str
    failure: str = ""             # harness-level problem; empty when the run went as planned
    serial: str = ""
    markers: list = field(default_factory=list)
    samples: list = field(default_factory=list)
    stick: Optional[fatimg.FatTree] = None
    stick_error: str = ""
    exit_code: Optional[int] = None
    ended_by_guest: bool = False
    t_begin: Optional[float] = None
    t_ebs: Optional[float] = None
    t_end: float = 0.0
    stderr: str = ""
    shots_failed: int = 0
    last_ppm: Optional[bytes] = None

    def marker(self, tag: str, **where):
        for m in self.markers:
            if m.tag == tag and all(m.fields.get(k) == v for k, v in where.items()):
                return m
        return None

    def screens(self):
        return [s.screen for s in self.samples if s.screen.ok]


_FIELD = re.compile(r"(\w+)=(.*?)(?=\s+\w+=|$)")


def parse_markers(text: str) -> list:
    out = []
    for raw in text.replace("\r", "").split("\n"):
        i = raw.find(MARKER_PREFIX)
        if i < 0:
            continue
        rest = raw[i + len(MARKER_PREFIX):].rstrip()
        tag, _, tail = rest.partition(" ")
        out.append(Marker(tag, {k: v.strip() for k, v in _FIELD.findall(tail)}, rest))
    return out


def _require_regular_file(path) -> None:
    mode = os.stat(path).st_mode
    if not stat.S_ISREG(mode):
        raise ValueError(f"refusing to hand a non-regular file to the emulator: {path}")


def _drain(pipe, sink: bytearray, limit: int = 1 << 20) -> None:
    try:
        for chunk in iter(lambda: pipe.read1(4096), b""):
            if len(sink) < limit:
                sink += chunk
    except (OSError, ValueError):
        pass


def build_stick(cfg: RunConfig) -> bytes:
    stick = fatimg.Fat16Image()
    stick.add_file("EFI/BOOT/BOOTAA64.EFI", Path(cfg.efi).read_bytes())
    for path, data in cfg.extra_files.items():
        stick.add_file(path, data)
    return stick.to_bytes()


def qemu_command(cfg: RunConfig, assets: Assets, vars_copy: Path, stick: Path, serial: Path):
    cmd = [assets.qemu, "-accel", "tcg",
           "-machine", "virt,virtualization=on" if cfg.el2 else "virt",
           "-cpu", "max", "-smp", str(cfg.smp), "-m", cfg.memory, "-nic", "none",
           "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={assets.code_fd}",
           "-drive", f"if=pflash,format=raw,unit=1,file={vars_copy}",
           "-display", "none", "-serial", f"file:{serial}", "-monitor", "stdio",
           "-no-reboot"]
    if cfg.gop:
        cmd += ["-device", "ramfb"]
    if cfg.disk == "usb":
        cmd += ["-device", "qemu-xhci", "-drive", f"if=none,id=stick,format=raw,file={stick}",
                "-device", "usb-storage,drive=stick"]
    elif cfg.disk == "virtio":
        cmd += ["-drive", f"if=none,id=stick,format=raw,file={stick}",
                "-device", "virtio-blk-pci,drive=stick"]
    else:
        raise ValueError(f"unknown disk kind {cfg.disk!r}")
    return cmd


def run(cfg: RunConfig, assets: Assets, font: dict) -> RunResult:
    workdir = Path(cfg.workdir)
    if re.search(r"[\s\"']", str(workdir)):
        raise ValueError("workdir must not contain whitespace or quotes (monitor syntax)")
    workdir.mkdir(parents=True, exist_ok=True)
    vars_copy, stick, serial = workdir / "vars.fd", workdir / "stick.img", workdir / "serial.log"
    shot = workdir / "shot.ppm"
    shutil.copyfile(assets.vars_fd, vars_copy)
    stick.write_bytes(build_stick(cfg))
    serial.write_bytes(b"")
    for path in (vars_copy, stick, serial, assets.code_fd):
        _require_regular_file(path)

    result = RunResult(cfg.name)
    out_buf, err_buf = bytearray(), bytearray()
    start = time.monotonic()
    proc = subprocess.Popen(qemu_command(cfg, assets, vars_copy, stick, serial),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    for pipe, sink in ((proc.stdout, out_buf), (proc.stderr, err_buf)):
        threading.Thread(target=_drain, args=(pipe, sink), daemon=True).start()
    sent_quit = False

    def now():
        return time.monotonic() - start

    def monitor(command: str) -> None:
        nonlocal sent_quit
        try:
            proc.stdin.write(command.encode() + b"\n")
            proc.stdin.flush()
        except (OSError, ValueError):
            pass
        if command == "quit":
            sent_quit = True

    def refresh() -> None:
        result.serial = serial.read_bytes().decode("utf-8", "replace")
        result.markers = parse_markers(result.serial)

    def wait_for(predicate, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            refresh()
            if predicate():
                return True
            if proc.poll() is not None:
                refresh()
                return predicate()
            time.sleep(0.2)
        return False

    def ebs_state():
        for m in result.markers:
            if m.tag == "EBS" and "STATE" in m.fields:
                return m.fields["STATE"]
        return None

    def take_shot() -> Optional[bytes]:
        try:
            os.unlink(shot)
        except FileNotFoundError:
            pass
        monitor(f"screendump {shot}")
        end = time.monotonic() + 8.0
        while time.monotonic() < end and proc.poll() is None:
            try:
                data = shot.read_bytes()
                screen.parse_ppm(data)
                return data
            except (FileNotFoundError, screen.ScreenError):
                time.sleep(0.05)
        return None

    try:
        if not wait_for(lambda: result.marker("BEGIN") is not None, cfg.boot_timeout):
            result.failure = ("the probe never printed its BEGIN line "
                              "(firmware did not boot the stick, or QEMU exited)")
            return result
        result.t_begin = now()

        def ready() -> bool:
            if cfg.capture_when is not None:
                return cfg.capture_when(result)
            return ebs_state() is not None

        if not wait_for(ready, cfg.pre_ebs_timeout):
            result.failure = ("the capture condition did not hold before the pre-ExitBootServices "
                              "timeout" if cfg.capture_when is not None else
                              "no EBS state line before the pre-ExitBootServices timeout")
            return result

        if cfg.capture_when is not None or ebs_state() == "CALLING":
            result.t_ebs = now()
            previous = None
            end = time.monotonic() + cfg.capture_seconds
            while time.monotonic() < end and proc.poll() is None:
                data = take_shot()
                if data is None:
                    result.shots_failed += 1
                elif hashlib.blake2b(data, digest_size=16).hexdigest() != previous:
                    previous = hashlib.blake2b(data, digest_size=16).hexdigest()
                    sample = Sample(now(), previous,
                                    screen.decode(screen.parse_ppm(data), font))
                    result.samples.append(sample)
                    result.last_ppm = data
                    if cfg.stop_when and cfg.stop_when(result):
                        break
                time.sleep(cfg.capture_interval)
        else:
            time.sleep(1.0)
    finally:
        result.ended_by_guest = proc.poll() is not None and not sent_quit
        if proc.poll() is None:
            monitor("quit")
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        result.exit_code = proc.returncode
        result.t_end = now()
        refresh()
        result.stderr = bytes(err_buf).decode("utf-8", "replace")
        try:
            result.stick = fatimg.read_fat16(stick.read_bytes())
        except (fatimg.FatError, OSError) as exc:
            result.stick_error = str(exc)
        for big in (vars_copy,):
            try:
                os.unlink(big)
            except FileNotFoundError:
                pass
        if not cfg.keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
    return result
