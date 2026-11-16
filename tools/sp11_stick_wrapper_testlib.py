# SPDX-License-Identifier: MIT
"""Shared test support for the SP11 stick wrapper and reporter (docs/SP11-STICK-WRAPPER.md): a synthetic bundle made by the real bundle
builder, the C host harness (tools/test-sp11-stick-wrapper.c) compiled with AddressSanitizer and UndefinedBehaviorSanitizer around the
REAL wrapper and reporter sources, a scenario writer for it and a reader of what it prints, and the disassembly checks of the unit
binaries. Used by tools/sp11_stick_wrapper_checks.py (and through it tools/test-sp11-stick-wrapper.py and
tools/test-sp11-stick-wrapper-refusals.py), tools/test-sp11-stick-wrapper-build.py and tools/test-sp11-stick-wrapper-qemu.py. Nothing here
touches hardware; every file is written below build/. EXPERIMENTAL."""
import concurrent.futures
import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_build as bb  # noqa: E402
import sp11_stick_wrapper_build as wb  # noqa: E402

E = sb.E
MIB = sb.MIB
BUILD = ROOT / 'build' / 'sp11-wrapper-test-scratch'
HARNESS_C = TOOLS / 'test-sp11-stick-wrapper.c'
EXAMPLE = TOOLS / 'testdata/sp11-facts.example.json'
GIC = dict(SNAP_GICD_BASE='0x17000000', SNAP_GICR_FIRST='0x17080000', SNAP_GICR_LIMIT='0x17380000', SNAP_GICR_STRIDE='0x40000', SNAP_TIMER_INTID='26')
SANITIZE = ['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined']
HOST_FLAGS = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', *SANITIZE]
WRAPPER_SOURCES = ('probes/sp11-stick-wrapper.c', 'probes/sp11-stick-load.c', 'probes/sp11-stick-report.c')
ATTR = 0x100F
LOW, BANK = 0x98000000, 0x880000000
PAGE = 4096
LINE = 64


def find_tool(name):
    return shutil.which(name) or next((str(Path(d) / name) for d in wb.LLVM_DIRS if (Path(d) / name).exists()), None)


def q1n1_tree():
    """The q1n1 checkout the wrapper builds against, or None (the tests that need it SKIP: a skip is not a pass)."""
    try:
        return wb.q1n1_tree()
    except wb.Refused:
        return None


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def page_up(n):
    return (n + PAGE - 1) & ~(PAGE - 1)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)
    return str(path)


def blob(directory, data):
    """A file named by the digest of its bytes, written once: scenarios refer to their files by path."""
    path = Path(directory) / 'blobs' / sha(data)[:24]
    if not path.exists():
        write(path, data)
    return str(path)


# ---- A synthetic bundle, made by the real builder ----------------------------------------------------------------------
def efi_map(descs):
    return b''.join(struct.pack('<IIQQQQ', t, 0, start, 0, pages, attr) + bytes(8) for t, start, pages, attr in descs)


def build_map(extra=0):
    """The unit's map in outline: the low conventional extent (590 MiB), the bank, a reserved framebuffer, a few allocations."""
    base = [(0, 0x80000000, 0x100, ATTR), (7, LOW, 590 * 256, ATTR), (6, 0xCD000000, 0x40, ATTR), (0, 0xE0000000, 0x1518, ATTR),
            (7, BANK, 30720 * 256, ATTR)]
    return base + [(1, 0xD8000000 + 0x2000 * i, 1, ATTR) for i in range(extra)]


def tables_for(HB, f):
    gtdt = struct.pack('<QIIIIIIIIIQII', (1 << 64) - 1, 0, 29, 4, 30, 4, 27, 4, f['timer_intid'], 4, (1 << 64) - 1, 0, 0)
    cpus = [HB.gicc(i, m, gicr=0) for i, m in enumerate((0x0, 0x100, 0x200, 0x300, 0x10000, 0x10100, 0x10200, 0x10300, 0x20000, 0x20100, 0x20200, 0x20300))]
    entries = [HB.gicd(f['gicd_base'], 4), *cpus, HB.gicr(f['gicr_first'], f['gicr_limit'] - f['gicr_first']), HB.its(f['its_base'])]
    return {'APIC': HB.madt(entries), 'GTDT': HB.sdt(b'GTDT', gtdt), 'MCFG': HB.mcfg([(0, 0x40000000, 0, 255)]), 'FACP': HB.sdt(b'FACP', bytes(240))}


class Fixtures:
    """The inputs of the bundle builder: three synthetic firmware dumps and the facts that agree with them. Written once, below `root`."""

    def __init__(self, root):
        self.root = Path(root)
        HB = load('hamoa_bootcontract_tests', TOOLS / 'test-hamoa-bootcontract.py')
        f = bb.load_facts(EXAMPLE)
        self.dumps, tables = [], tables_for(HB, f)
        for name, descs in (('d1', build_map()), ('d2', build_map(3)), ('d3', build_map(1))):
            d = self.root / 'dumps' / name
            for sig, data in tables.items():
                write(d / f'{sig}.bin', data)
            write(d / 'memmap.raw', efi_map(descs))
            gop = [f'gop_width={f["fb_width"]}', f'gop_height={f["fb_height"]}', f'gop_stride_pixels={f["fb_stride"]}', f'gop_format={f["fb_format"]:#x}',
                   f'gop_base={f["fb_base"]:#018x}', f'gop_size={f["fb_bytes"]:#018x}']
            write(d / 'summary.txt', ('# synthetic dump\n' + '\n'.join([f'memmap_count={len(descs)}', 'memmap_descriptor_size=48', *gop]) + '\n').encode())
            self.dumps.append(str(d))
        acpi = [dict(sig=s, length=len(t), sha256=sha(t)) for s, t in sorted(tables.items())]
        self.facts = write(self.root / 'facts.json', json.dumps({**json.loads(EXAMPLE.read_text()), 'acpi': acpi}).encode())
        self.monitor = write(self.root / 'in/monitor.bin', b'PLACEHOLDER SNAPMON.BIN\n' * 4)
        self.reporter = write(self.root / 'in/reporter.bin', b'PLACEHOLDER SNAPREP.BIN\n' * 4)

    def bundle(self, name, base, guest_mib=256, sizes=(8, 1, 4), monitor=None, reporter=None, stores='ram,el2_sysreg', extra=()):
        """A bundle below root/out/<name>: files PIN.DAT, GUEST.DAT, REPORT.DAT (and the placeholders or the given payloads). `sizes` are
        the image, tables and SPTM data in MiB; None leaves the builder's defaults (a GUEST.DAT of 144 MB). `stores` and `extra` (more
        builder options, such as --gic-stop) are for the GIC and stop tests; the defaults are the dry run's."""
        out = self.root / 'out' / name
        if not (out / 'build-report.json').exists():
            opts = ['--facts', self.facts, '--base', hex(base), '--guest-mib', str(guest_mib), '--monitor', monitor or self.monitor,
                    '--reporter', reporter or self.reporter, '--stores', stores, '--time-box-s', '5', *extra]
            if sizes:
                opts += ['--image-mib', str(sizes[0]), '--tables-mib', str(sizes[1]), '--sptm-data-mib', str(sizes[2])]
            opts += [x for d in self.dumps for x in ('--dump', d)]
            code, text, err = run_builder(['authorisation-template', 'synthetic', *opts])
            assert code == 0, (text, err)
            record = write(self.root / 'records' / f'{name}.txt', text.encode())
            code, text, err = run_builder(['synthetic', *opts, '--no-manifest', '--out', str(out), '--authorisation', record])
            assert code == 0, (text, err)
        return Bundle(out / 'bundle' / 'SP11')


def run_builder(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = bb.main([str(a) for a in argv])
        except SystemExit as error:         # argparse refusing the arguments
            code = error.code
    return code, out.getvalue(), err.getvalue()


class Bundle:
    """What the builder wrote: the five files and their parsed records."""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.monitor, self.reporter = [(self.dir / n).read_bytes() for n in ('SNAPMON.BIN', 'SNAPREP.BIN')]
        self.guest, self.pin, self.report = [(self.dir / n).read_bytes() for n in ('GUEST.DAT', 'PIN.DAT', 'REPORT.DAT')]
        self.fields = sb.unpack(self.pin, sb.PIN_LAYOUT)
        self.head, self.chunks = sb.unpack_guest(self.guest)

    def midr(self):
        return struct.unpack('<2I', self.fields['midr'][:8])[1]

    def chunk_data(self, i):
        c = self.chunks[i]
        return None if c['flags'] & sb.CHUNK_ZERO else self.guest[c['file_offset']:c['file_offset'] + c['size']]

    def data_index(self):
        return next(i for i, c in enumerate(self.chunks) if not c['flags'] & sb.CHUNK_ZERO and c['kind'] == sb.KIND_KERNEL)

    def zero_index(self):
        return next(i for i, c in enumerate(self.chunks) if c['flags'] & sb.CHUNK_ZERO)

    def repacked_pin(self, **changes):
        """PIN.DAT with fields changed and the record digest recomputed, so the case reaches the check it is about."""
        pin = bytearray(self.pin)
        for name, value in changes.items():
            sb.put(pin, sb.PIN_LAYOUT, name, value)
        return bytes(sb.pin_digest(pin))

    def repacked_guest(self, mutate):
        """(GUEST.DAT, PIN.DAT) from the chunk list after mutate(list of dicts), with the binding fields of PIN.DAT following."""
        chunks = [dict(kind=c['kind'], dest=c['dest'], **(dict(size=c['size']) if c['flags'] & sb.CHUNK_ZERO else dict(data=self.chunk_data(i))))
                  for i, c in enumerate(self.chunks)]
        mutate(chunks)
        guest = sb.pack_guest(self.head['load_base'], self.head['load_size'], chunks)
        return guest, self.repacked_pin(**sb.guest_pin_fields(guest))


# ---- The host harness --------------------------------------------------------------------------------------------------
_objects = {}
_lock = threading.Lock()


def use_scratch(name):
    """Give this test process a scratch directory of its own below build/ and forget the harness executables compiled into an earlier one,
    so that the test files can run side by side, or one after the other in one process, without sharing or deleting each other's files."""
    global BUILD
    BUILD = ROOT / 'build' / f'sp11-wrapper-test-scratch-{name}'
    _objects.clear()
    return BUILD


def clang(args, what):
    done = subprocess.run([find_tool('clang'), *args], capture_output=True, text=True, timeout=600)
    if done.returncode:
        raise AssertionError(f'{what} failed:\n{done.stderr[-3000:]}')


def include_flags(tree):
    return [f'-I{tree / "platform/uefi"}', f'-I{tree / "payloads"}']


def common_flags(tree, gic=None):
    macros = {**GIC, **(gic or {})}
    return [*HOST_FLAGS, '-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=1', '-DSP11_STICK_HOST_TEST=1', *[f'-D{k}={v}' for k, v in macros.items()],
            *include_flags(tree)]


def shared_objects(tree):
    """The harness, the reporter, q1n1's sha256.c and its text layer: the objects every variant of the wrapper is linked with."""
    with _lock:
        if 'shared' not in _objects:
            out = BUILD / 'harness'
            out.mkdir(parents=True, exist_ok=True)
            flags = common_flags(tree)
            jobs = {'harness': (['-DQ1N1_SP11_PAYLOAD=1'], HARNESS_C), 'text': (['-DQ1N1_SP11_PAYLOAD=1'], tree / 'platform/uefi/sp11-payload-text.c'),
                    'sha256': ([], tree / 'platform/uefi/sha256.c'),
                    'reporter': (['-Dpayload_entry=snaprep_entry'], ROOT / 'probes/sp11-stick-reporter.c')}
            with concurrent.futures.ThreadPoolExecutor(4) as pool:
                futures = [pool.submit(clang, [*flags, *extra, '-c', str(src), '-o', str(out / f'{name}.o')], f'compiling {src.name}')
                           for name, (extra, src) in jobs.items()]
                for future in futures:
                    future.result()
            _objects['shared'] = [str(out / f'{name}.o') for name in jobs]
        return _objects['shared']


def harness(tree, name='base', defines=(), gic=None):
    """The executable of one variant of the wrapper: `defines` are -D flags (a seeded fault, a hash policy), `gic` replaces macro values."""
    key = (name, tuple(defines), tuple(sorted((gic or {}).items())))
    shared = shared_objects(tree)
    with _lock:
        if key in _objects:
            return _objects[key]
    out = BUILD / 'harness' / name
    out.mkdir(parents=True, exist_ok=True)
    flags = [*common_flags(tree, gic), *defines, '-Dpayload_entry=snapmon_entry']
    objs = []
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        futures = []
        for src in WRAPPER_SOURCES:
            obj = out / (Path(src).stem + '.o')
            objs.append(str(obj))
            futures.append(pool.submit(clang, [*flags, '-c', str(ROOT / src), '-o', str(obj)], f'compiling {src} ({name})'))
        for future in futures:
            future.result()
    exe = out / 'harness'
    clang([*SANITIZE, *objs, *shared, '-o', str(exe)], f'linking the harness ({name})')
    with _lock:
        _objects[key] = exe
    return exe


@dataclasses.dataclass
class Line:
    index: int
    length: int
    clean: bool
    text: str


@dataclasses.dataclass
class Event:
    index: int
    kind: str
    a: int
    b: int
    stage: int
    extra: int


@dataclasses.dataclass
class Result:
    name: str
    run: str
    ret: int
    faulted: bool
    fault: str = ''
    lines: list = dataclasses.field(default_factory=list)
    events: list = dataclasses.field(default_factory=list)
    report: bytes = None
    report_tail: int = 0
    dirty: dict = dataclasses.field(default_factory=dict)
    digests: list = dataclasses.field(default_factory=list)
    counts: dict = dataclasses.field(default_factory=dict)

    def texts(self):
        return [line.text for line in self.lines]

    def head(self):
        return sb.unpack(self.report, sb.REPORT_LAYOUT)

    def of(self, kind):
        return [e for e in self.events if e.kind == kind]


def parse_output(text):
    results, current = {}, None
    for raw in text.splitlines():
        word, _, rest = raw.partition(' ')
        if word == 'RESULT':
            name, run, ret, faulted = rest.split()
            current = Result(name, run, int(ret[4:], 16), faulted == 'faulted=1')
            results.setdefault(name, []).append(current)
        elif word == 'FAULT':
            current.fault = rest
        elif word == 'LINE':
            index, length, clean, line = rest.split(' ', 3)
            current.lines.append(Line(int(index), int(length), clean == '1', line))
        elif word == 'EV':
            index, kind, a, b, stage, extra = rest.split()
            current.events.append(Event(int(index), kind, int(a, 16), int(b, 16), int(stage, 16), int(extra)))
        elif word == 'REPORT':
            current.report = bytes.fromhex(rest)
        elif word == 'REPORTTAIL':
            current.report_tail = int(rest)
        elif word == 'DIRTY':
            region, count = rest.split()
            current.dirty[region] = int(count)
        elif word == 'DIGEST':
            fields = rest.split()
            current.digests.append((int(fields[0], 16), int(fields[1], 16), *(fields[2:] if len(fields) > 2 and fields[2] != '-' else (None, None))))
        elif word == 'COUNTS':
            current.counts = {k: int(v) for k, v in (item.split('=') for item in rest.split())}
    return results


def run_scenarios(exe, text, timeout=1800):
    env = {**os.environ, 'ASAN_OPTIONS': 'detect_stack_use_after_return=0:detect_leaks=0', 'UBSAN_OPTIONS': 'print_stacktrace=1'}
    done = subprocess.run([str(exe)], input=text, capture_output=True, text=True, env=env, timeout=timeout)
    if done.returncode:
        raise AssertionError(f'the harness exited {done.returncode}:\n{done.stderr[-4000:]}\n{done.stdout[-1000:]}')
    return parse_output(done.stdout)


def prebuild(tree, variants):
    """Compile every variant {name: dict(defines=..., gic=...)} concurrently; the shared objects are made first."""
    shared_objects(tree)
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        for future in [pool.submit(harness, tree, name, **spec) for name, spec in variants.items()]:
            future.result()


def probe_addresses(base, size):
    """The pages an AT probe of [base, base + size) must visit, as the interface states them: the first, the last and every 2 MiB boundary
    between (the rule written independently of sw_probe_range)."""
    first, last = base & ~(PAGE - 1), (base + size - 1) & ~(PAGE - 1)
    return sorted({first, last} | set(range(((first >> 21) + 1) << 21, last, 1 << 21)))


# ---- Scenarios ---------------------------------------------------------------------------------------------------------
class World:
    """One fake machine around a bundle: where the runner put each file, the final memory map, the registers and the framebuffer, all
    consistent with the bundle's PIN.DAT, with the places a case may change. `text()` writes the scenario for the harness."""
    TABLE, MON, REP, PIN, REPORT, MAP, STACK, GUEST = 0xCE000000, 0xCE010000, 0xCE020000, 0xCE030000, 0xCE040000, 0xCE0D0000, 0xCE0E0000, 0xCE200000

    def __init__(self, bundle, directory, pin=None, guest=None, report=None, **over):
        self.bundle, self.dir = bundle, directory
        f = bundle.fields
        self.pin = bundle.pin if pin is None else pin
        self.guest = bundle.guest if guest is None else guest
        self.report = bundle.report if report is None else report
        self.regs = dict(currentel=8, midr=bundle.midr(), mpidr=f['boot_mpidr'], cntfrq=f['cntfrq_hz'], hcr=0x88000010, sctlr=0x100030C5183D,
                         tcr=0x80843514, ctr=0x8444C004)
        self.fb = (f['fb_base'], f['fb_bytes'], f['fb_width'], f['fb_height'], f['fb_stride'], f['fb_format'])
        self.place = (f['place_base'], f['place_size'])
        self.sizes = {0: 0x9000, 1: 0x6000}
        self.bases = {0: self.MON, 1: self.REP, 2: self.GUEST, 3: self.PIN, 4: self.REPORT}
        self.paths = {}
        self.table_count, self.table_magic, self.self_slot = 5, None, None
        self.map_descs, self.map_stride, self.map_size = None, 48, None
        self.stack, self.table = (self.STACK, 0x10000), self.TABLE
        self.args, self.ops, self.with_placement = None, None, True
        self.extra, self.tick, self.images, self.size_over = [], (1000, 1000), {}, {}
        self.prefill, self.digest_chunks = False, False
        self.__dict__.update(over)

    def size_of(self, slot):
        default = {2: len(self.guest), 3: len(self.pin), 4: len(self.report)}.get(slot, self.sizes.get(slot))
        return self.size_over.get(slot, default)

    def descriptors(self):
        descs = [d for d in build_map() if not (d[0] == 0 and d[1] == 0xE0000000)]
        descs.append((0, self.fb[0], page_up(self.fb[1]) // PAGE, ATTR))
        for slot, base in self.bases.items():
            descs.append((1 if slot < 2 else 2, base, page_up(self.size_of(slot)) // PAGE, ATTR))
        descs += [(2, self.table, 2, ATTR), (2, self.MAP, 1, ATTR), (2, self.stack[0], self.stack[1] // PAGE, ATTR)]
        return sorted(descs, key=lambda d: d[1])

    def chunk_ranges(self):
        return [(c['dest'], c['size']) for c in self.bundle.chunks]

    def text(self, name, runs=('snapmon',)):
        d = self.dir
        descs = self.map_descs if self.map_descs is not None else self.descriptors()
        lines = [f'BEGIN {name}', f'TABLE {self.table:#x} {self.table_count}']
        if self.table_magic is not None:
            lines.append(f'TABLEMAGIC {self.table_magic:#x}')
        for slot in range(5):
            image = {2: self.guest, 3: self.pin, 4: self.report}.get(slot)
            image = self.images.get(slot, image)
            size = self.size_of(slot)
            lines.append(f'FILE {slot} {blob(d, image) if image is not None else "-"} {self.bases[slot]:#x} {size:#x}')
            if slot in self.paths:
                lines.append(f'PATHHEX {slot} {self.paths[slot].hex()}')
        lines.append(f'MAP {self.MAP:#x} {self.map_stride} {blob(d, efi_map(descs))}' + (f' {self.map_size:#x}' if self.map_size is not None else ''))
        lines.append(f'STACK {self.stack[0]:#x} {self.stack[1]:#x}')
        lines.append('FB ' + ' '.join(hex(v) for v in self.fb))
        if self.with_placement:
            lines.append(f'PLACEMENT {self.place[0]:#x} {self.place[1]:#x}')
        lines += [f'REG {k} {v:#x}' for k, v in self.regs.items()]
        if self.self_slot is not None:
            lines.append(f'SELF {self.self_slot}')
        if self.args:
            lines.append('ARGS ' + ' '.join(hex(v) for v in self.args))
        if self.ops:
            lines.append('OPS ' + ' '.join(str(v) for v in self.ops))
        lines.append(f'TICK {self.tick[0]} {self.tick[1]}')
        lines += self.extra
        if self.with_placement and self.prefill:
            lines += [f'PREFILL {a:#x} {n:#x} 0xa5' for a, n in self.chunk_ranges()]
        if self.with_placement and self.digest_chunks:
            lines += [f'DIGEST {a:#x} {n:#x}' for a, n in self.chunk_ranges()]
        lines += [f'RUN {r}' for r in runs]
        lines.append('END')
        return '\n'.join(lines) + '\n'


def expected_chunk_image(bundle, i):
    c = bundle.chunks[i]
    return bytes(c['size']) if c['flags'] & sb.CHUNK_ZERO else bundle.chunk_data(i)


# ---- Reading the unit binaries -----------------------------------------------------------------------------------------
def disassemble(elf):
    done = subprocess.run([find_tool('llvm-objdump'), '-d', '--no-show-raw-insn', str(elf)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def sections(elf):
    """{name: (address, size)} of the allocated sections, from llvm-readelf."""
    done = subprocess.run([find_tool('llvm-readelf'), '--wide', '--sections', str(elf)], capture_output=True, text=True)
    found = {}
    for line in done.stdout.splitlines():
        m = re.match(r'^\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+([0-9a-f]+)\s+[0-9a-f]+\s+([0-9a-f]+)', line)
        if m:
            found[m.group(1)] = (int(m.group(2), 16), int(m.group(3), 16))
    return found


def parse_functions(text):
    """{name: [(address, mnemonic, operands)]} in listing order (one entry per label; a duplicated local name keeps both copies)."""
    funcs, current, serial = {}, None, {}
    for line in text.splitlines():
        label = re.match(r'^([0-9a-f]+) <(.+)>:$', line)
        if label:
            name = label.group(2)
            serial[name] = serial.get(name, 0) + 1
            current = name if serial[name] == 1 else f'{name}#{serial[name]}'
            funcs[current] = []
            continue
        ins = re.match(r'^\s+([0-9a-f]+):\s+(\S+)\s*(.*)$', line)
        if ins and current:
            funcs[current].append((int(ins.group(1), 16), ins.group(2), ins.group(3).split('//')[0].strip()))
    return funcs


OPS_OFFSETS = {0x78, 0x80, 0x88, 0x90}      # puts, hex, load32, counter in struct q1n1_payload_args (docs/SP11-PAYLOAD.md)
CALLER_SAVED = {f'x{i}' for i in range(19)} | {'x30'}
ARGUMENT_REGISTERS = {f'x{i}' for i in range(8)}
BRANCHES = {'b', 'bl', 'br', 'blr', 'ret', 'cbz', 'cbnz', 'tbz', 'tbnz'}
NO_DESTINATION = {'cmp', 'cmn', 'tst', 'ccmp', 'ccmn', 'nop', 'hint', 'bti', 'isb', 'dsb', 'dmb', 'at', 'dc', 'ic', 'tlbi', 'sys', 'msr', 'prfm',
                  'prfum'} | BRANCHES


def reg_of(text):
    text = text.strip()
    if text in ('sp', 'wsp'):
        return 'sp'
    m = re.fullmatch(r'[xw](\d+)', text)
    return f'x{m.group(1)}' if m and int(m.group(1)) < 31 else None


def split_ops(text):
    out, depth, cur = [], 0, ''
    for ch in text:
        depth += ch == '['
        depth -= ch == ']'
        if ch == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += ch
    return out + ([cur.strip()] if cur.strip() else [])


def memory_base(operands):
    for op in operands:
        if op.startswith('['):
            return reg_of(op[1:].split(',')[0].split(']')[0])
    return None


def branch_target(operands):
    m = re.search(r'0x([0-9a-f]+)', operands[-1]) if operands else None
    return int(m.group(1), 16) if m else None


def dataflow(instructions):
    """Per instruction, (stack-derived registers, registers holding an op loaded from the arguments' table, registers derived from an
    incoming argument) before it runs: a forward must analysis over the function's basic blocks (join = intersection). A register is
    stack-derived (argument-derived) when written by add/sub/mov from sp or a stack-derived register (from x0..x7 at entry or an
    argument-derived register); an op register when written by a load at an ops-table offset. A call forgets the caller-saved registers.
    This proves the base of an access, never its offset, as the memory check of q1n1's el2mem payload does; it may refuse a safe binary,
    and must not pass an unsafe one."""
    index = {addr: i for i, (addr, _, _) in enumerate(instructions)}
    leaders = {0}
    for i, (addr, mnemonic, operands) in enumerate(instructions):
        if mnemonic in BRANCHES or mnemonic.startswith('b.'):
            target = branch_target(split_ops(operands)) if mnemonic not in ('ret', 'br', 'blr') else None
            if target in index:
                leaders.add(index[target])
            if i + 1 < len(instructions):
                leaders.add(i + 1)
    starts = sorted(leaders)
    ends = {s: (starts[k + 1] if k + 1 < len(starts) else len(instructions)) for k, s in enumerate(starts)}

    def step(state, ins):
        stack, ops, args, slots = set(state[0]), set(state[1]), set(state[2]), set(state[3])
        _, mnemonic, text = ins
        operands = split_ops(text)
        if mnemonic in ('bl', 'blr'):
            return (frozenset(stack - CALLER_SAVED), frozenset(ops - CALLER_SAVED), frozenset(args - CALLER_SAVED), frozenset(slots))
        on_stack = memory_base(operands) == 'sp'
        offset = re.search(r'\[sp(?:, #(-?0x[0-9a-f]+|-?\d+))?\]', text)
        slot = int(offset.group(1), 0) if offset and offset.group(1) else 0
        if re.search(r'\]!|\], #', text):
            slots.clear()                       # a write-back moves sp: the offsets no longer name the same slots
            on_stack = False
        pair = mnemonic in ('stp', 'ldp')
        if mnemonic.startswith('st') and on_stack:
            for k, reg in enumerate(operands[:2] if pair else operands[:1]):
                if reg.startswith('x') and reg_of(reg) in args:
                    slots.add(slot + 8 * k)         # a pointer argument spilled to the frame
                else:
                    slots.discard(slot + 8 * k)
        if mnemonic.startswith(('ld',)):
            n = 2 if mnemonic.startswith('ldp') else 1
            displacement = re.search(r'#(-?0x[0-9a-f]+|-?\d+)\]', text)
            from_ops = n == 1 and displacement and int(displacement.group(1), 0) in OPS_OFFSETS and mnemonic == 'ldr'
            for k, op in enumerate(operands[:n]):
                reg = reg_of(op)
                if reg:
                    stack.discard(reg)
                    ops.discard(reg)
                    args.discard(reg)
                    if from_ops:
                        ops.add(reg)
                    if on_stack and op.startswith('x') and slot + 8 * k in slots:
                        args.add(reg)               # the spilled pointer, reloaded
        elif mnemonic not in NO_DESTINATION and not mnemonic.startswith(('st', 'b.')) and operands:
            dest = reg_of(operands[0])
            if dest:
                sources = [reg_of(o) for o in operands[1:3 if mnemonic == 'add' else 2]]
                if mnemonic in ('madd', 'umaddl', 'smaddl') and len(operands) > 3:
                    sources = [reg_of(operands[3])]     # base + index * size: the base is the addend, the index only moves within the object
                derived = mnemonic in ('add', 'sub', 'mov', 'madd', 'umaddl', 'smaddl') and any(s in (stack | {'sp'}) for s in sources)
                from_argument = mnemonic in ('add', 'sub', 'mov', 'madd', 'umaddl', 'smaddl') and any(s in args for s in sources)
                carried = mnemonic == 'mov' and sources[:1] and sources[0] in ops
                stack.discard(dest)
                ops.discard(dest)
                args.discard(dest)
                if dest == 'sp':
                    slots.clear()
                if derived:
                    stack.add(dest)
                if from_argument:
                    args.add(dest)
                if carried:
                    ops.add(dest)
        return (frozenset(stack), frozenset(ops), frozenset(args), frozenset(slots))

    states = {0: (frozenset({'sp'}), frozenset(), frozenset(ARGUMENT_REGISTERS), frozenset())}
    work = [0]
    while work:
        s = work.pop()
        state = states[s]
        for i in range(s, ends[s]):
            state = step(state, instructions[i])
        last = instructions[ends[s] - 1]
        succ = []
        mnemonic, operands = last[1], split_ops(last[2])
        if mnemonic not in ('b', 'ret', 'br'):
            succ.append(ends[s])
        if (mnemonic in BRANCHES or mnemonic.startswith('b.')) and mnemonic not in ('ret', 'br', 'blr', 'bl'):
            target = branch_target(operands)
            if target in index:
                succ.append(index[target])
        for t in succ:
            if t >= len(instructions):
                continue
            merged = state if t not in states else tuple(a & b for a, b in zip(states[t], state))
            if states.get(t) != merged:
                states[t] = merged
                work.append(t)
    per = {}
    for s in starts:
        if s not in states:
            continue
        state = states[s]
        for i in range(s, ends[s]):
            per[i] = state
            state = step(state, instructions[i])
    return per


def store_violations(funcs, allowed=(), through_arguments=()):
    """Stores whose base register is not provably the stack, in functions not named in `allowed`; in a function named in
    `through_arguments` a base derived from an incoming argument is accepted too (the callers are inside the image)."""
    found = []
    for name, instructions in funcs.items():
        if name.split('#')[0] in allowed:
            continue
        per = dataflow(instructions)
        for i, (addr, mnemonic, text) in enumerate(instructions):
            if mnemonic.startswith('st') and i in per:
                base = memory_base(split_ops(text))
                if base not in per[i][0] and not (name.split('#')[0] in through_arguments and base in per[i][2]):
                    found.append(f'{name}+{addr:#x}: {mnemonic} {text} (base {base} is not provably the stack)')
    return found


def callers_of(funcs, callee):
    """{function: number of direct calls} to `callee` (a bl to its label, whatever the offset)."""
    found = {}
    for name, instructions in funcs.items():
        n = sum(1 for _, mnemonic, text in instructions if mnemonic == 'bl' and re.search(rf'<{re.escape(callee)}(#\d+)?(\+0x[0-9a-f]+)?>', text))
        if n:
            found[name.split('#')[0]] = found.get(name.split('#')[0], 0) + n
    return found


def indirect_branch_violations(funcs):
    """br anywhere, and blr through anything but a register loaded from the runner's ops table."""
    found = []
    for name, instructions in funcs.items():
        per = dataflow(instructions)
        for i, (addr, mnemonic, text) in enumerate(instructions):
            if mnemonic == 'br' or mnemonic.startswith('braa') or mnemonic.startswith('brab'):
                found.append(f'{name}+{addr:#x}: {mnemonic} {text}')
            if mnemonic == 'blr' and (i not in per or reg_of(text) not in per[i][1]):
                found.append(f'{name}+{addr:#x}: blr {text} is not through the ops table')
    return found


def stack_depth(funcs, root='payload_entry'):
    """The deepest stack use along the call graph, in bytes, from the frame allocations of the prologues; raises on recursion."""
    frames, calls = {}, {}
    for name, instructions in funcs.items():
        total, targets = 0, set()
        for _, mnemonic, text in instructions:
            m = re.match(r'sp, sp, #(0x[0-9a-f]+|\d+)(?:, lsl #12)?$', text)
            if mnemonic == 'sub' and m:
                total += int(m.group(1), 0) << (12 if 'lsl #12' in text else 0)
            pre = re.search(r'\[sp, #-(0x[0-9a-f]+|\d+)\]!', text)
            if mnemonic.startswith('st') and pre:
                total += int(pre.group(1), 0)
            if mnemonic == 'bl':
                called = re.search(r'<([^>+]+)(\+0x[0-9a-f]+)?>', text)
                if called:
                    targets.add(called.group(1))
        frames[name.split('#')[0]], calls[name.split('#')[0]] = total, targets

    def depth(name, seen):
        if name in seen:
            raise AssertionError(f'recursion through {name}')
        return frames.get(name, 0) + max([depth(t, seen | {name}) for t in calls.get(name, ()) if t in frames] or [0])
    return depth(root, frozenset())


def code_words(flat, text_section):
    start, size = text_section
    return [struct.unpack_from('<I', flat, off)[0] for off in range(start, start + size, 4)]


# Encodings (AArch64) of what a payload of this bundle may execute among the system instructions; everything else of that class is refused.
SYS_MASK = 0xFFFFFFE0
ALLOWED_SYSTEM = {0xD503245F: 'bti c (hint #34), the entry head', 0xD5033F9F: 'dsb sy', 0xD5033FDF: 'isb', 0xD50B7A20: 'dc cvac', 0xD50B7E20: 'dc civac',
                  0xD50C7820: 'at s1e2w', 0xD50C7800: 'at s1e2r'}
MRS_ALLOWED = {0xD5384240: 'CurrentEL', 0xD5380000: 'MIDR_EL1', 0xD53800A0: 'MPIDR_EL1', 0xD53BE000: 'CNTFRQ_EL0', 0xD53BE020: 'CNTPCT_EL0',
               0xD53B0020: 'CTR_EL0', 0xD53C1100: 'HCR_EL2', 0xD53C1000: 'SCTLR_EL2', 0xD53C2040: 'TCR_EL2', 0xD5387400: 'PAR_EL1'}


def encoding_violations(words, allowed_system, allowed_mrs):
    """Every word of the code that is a system instruction, an exception, a return from one or an MSR, other than the named encodings.
    This is independent of the disassembler: the class of a word is read from its bits."""
    found = []
    for i, w in enumerate(words):
        if (w & 0xFFE0001F) in (0xD4000001, 0xD4000002, 0xD4000003) or (w & 0xFFE0001F) == 0xD4200000:
            found.append(f'word {i * 4:#x} {w:#010x} is SVC, HVC, SMC or BRK')
        elif w in (0xD69F03E0, 0xD69F0BFF, 0xD6BF03E0, 0xD6BF0BFF) or (w & 0xFFFFFC1F) == 0xD69F0000 or (w & 0xFFFFFC1F) == 0xD61F0000:
            found.append(f'word {i * 4:#x} {w:#010x} is ERET or an indirect branch BR')
        elif (w & 0xFFF00000) == 0xD5100000 or (w & 0xFFF8F01F) == 0xD500401F:
            found.append(f'word {i * 4:#x} {w:#010x} is an MSR')
        elif (w & 0xFFF80000) == 0xD5280000:
            found.append(f'word {i * 4:#x} {w:#010x} is SYSL')
        elif (w & 0xFFF80000) == 0xD5080000 and (w & SYS_MASK) not in allowed_system:
            found.append(f'word {i * 4:#x} {w:#010x} is a SYS instruction (DC, IC, AT, TLBI) that is not allowed')
        elif (w & 0xFFF00000) == 0xD5300000 and (w & SYS_MASK) not in allowed_mrs:
            found.append(f'word {i * 4:#x} {w:#010x} is an MRS of a register that is not allowed')
        elif (w & 0xFFFFF01F) == 0xD503301F and (w & 0xFFFFFFFF) not in (0xD5033F9F, 0xD5033FDF) and (w & SYS_MASK) not in allowed_system:
            found.append(f'word {i * 4:#x} {w:#010x} is a barrier that is not allowed')
        elif (w & 0xFFFFF01F) == 0xD503201F and w not in (0xD503201F, 0xD503245F) and (w & SYS_MASK) not in allowed_system:
            found.append(f'word {i * 4:#x} {w:#010x} is a hint that is not allowed')
    return found
