# SPDX-License-Identifier: MIT
"""The windows that the words of rung 7 name for the nodes that the quiet profile withdraws (K15, R11-v2).

The record tells the coordinator that a MON STOP S2_FAULT with A= inside a window of a withdrawn node means that the profile did not take. Those windows are the reg of the 43 nodes
that the profile withdraws, through the ranges of /device-tree/arm-io, less the windows that a retained node also has (the CPU-visible address is the same, so a stop there is not
evidence about the withdrawal). They were a hand list in four places of the record, and the independent review found two retained windows in it (the DARTs of the video decoder and
of the scaler, which the quiet tree keeps) and one omitted window (0X290000000 of the scaler); now the four places render this module's one table, and `check` derives the table
again from the two trees of the build, so that the words cannot list a window the guest does not withdraw or omit one it does (GUEST_PROFILE_READBACK in the builder, which reads the
guest's published tree back: tools/sp11_stick_bundle_profile.py).

Evidence: OBSERVED on the host (a run of the loader on the J700 26A428 kernelcache; tools/test-sp11-m1-windows.py); INFERRED that no other window of the unit's address space is
reached through the withdrawn nodes (a regs entry outside the arm-io ranges, such as the entries of the audio codecs, is not an address)."""
ARM_IO = '/device-tree/arm-io'
NODES = 43                                  # the nodes of the base board that the quiet profile withdraws (395 less 352)

# (first byte, one past the last), in the order that the words list them
VIDEO = (0x380000000, 0x381404000)          # avd
VIDEO_DART = ('dart-avd', 0x381020000, 0x381040000)     # a retained node inside the video decoder's window
SCALER = ((0x291000000, 0x291038000), (0x291204000, 0x291208000), (0x291210000, 0x291214000), (0x290000000, 0x290008000))      # scaler0
SCALER_DART = ('dart-scaler', 0x291260000, 0x291280000)  # retained, outside the scaler's windows
PRORES = ((0x294000000, 0x294008000), (0x295000000, 0x295004000))    # apr
AUDIO = ((0x313200000, 0x313234000), (0x313300000, 0x313334000), (0x313800000, 0x313834000))       # admac-leap-s, admac-leap-ns, admac-base-ns
SHARED = ((0x308078000, 0x30807C000), (0x3082A8000, 0x3082A8008))    # windows of avd and of the three admac roots that a retained node covers
BY_NODE = {'avd': (VIDEO,), 'scaler0': SCALER, 'apr': PRORES, 'admac-leap-s': (AUDIO[0],), 'admac-leap-ns': (AUDIO[1],), 'admac-base-ns': (AUDIO[2],)}
HOLES = {'avd': (VIDEO_DART[1:],)}


def upper(window):
    return f'0X{window[0]:X}..0X{window[1]:X}'


def lower(window):
    return f'0x{window[0]:x}..0x{window[1]:x}'


def _list(windows):
    parts = [upper(w) for w in windows]
    return parts[0] if len(parts) == 1 else ', '.join(parts[:-1]) + ' and ' + parts[-1]


def for_falsifiers():
    """Section 5d's list of the falsifiers (the profile did not take), short."""
    return f'{upper(VIDEO)} for the video decoder but for its DART\'s {upper(VIDEO_DART[1:])}, {_list(SCALER)} for the scaler'


def for_new_stop():
    """Section 5d's class NEW STOP: every window of every withdrawn node, with what stays."""
    return (f'a window of a node that the profile withdrew (the video decoder\'s {upper(VIDEO)} but not its DART\'s {upper(VIDEO_DART[1:])}, which stays; the scaler\'s {_list(SCALER)}, '
            f'but not its DART\'s {upper(SCALER_DART[1:])}, which stays; the ProRes engine\'s {_list(PRORES)}; the audio roots\' {_list(AUDIO)}; the coordinator reads these as the reg of '
            f'the {NODES} nodes in the base board\'s DeviceTree.j700ap through the ranges of arm-io, and takes no other window for this reading: the video decoder\'s 0X{SHARED[0][0]:X} and '
            f'the audio roots\' 0X{SHARED[1][0]:X} are shared with nodes that stay)')


def for_result():
    """Section 3c's RESULT sentence: the video decoder's window, lower case as the sentence is."""
    return f'{lower(VIDEO)} but for its DART\'s {lower(VIDEO_DART[1:])}'


def for_boot26():
    """Section 0a's account of boot 26: the scaler's windows other than the first and its DART."""
    first, second, third = SCALER[1:]
    assert second[1] - second[0] == first[1] - first[0]
    return (f'other windows 0X{first[0]:X} and 0X{second[0]:X} (0X{first[1] - first[0]:X} each) and 0X{third[0]:X} (0X{third[1] - third[0]:X}), and its DART, the node '
            f'{SCALER_DART[0]} that stays in the quiet tree, at 0X{SCALER_DART[1]:X}')


def expected():
    """The table above in the shape that `derive` returns."""
    return dict(by_node={node: tuple(sorted(w)) for node, w in BY_NODE.items()}, holes={node: tuple(sorted(h)) for node, h in HOLES.items()}, shared=tuple(sorted(SHARED)),
                darts={SCALER_DART[0]: SCALER_DART[1:], VIDEO_DART[0]: VIDEO_DART[1:]})


def _translate(tree, path, spans):
    """The CPU windows of the reg of `path`, a direct child of arm-io: each entry (address, size) whose address lies in a range of arm-io; an entry outside every range is not an address."""
    props = tree.nodes[path]
    reg = bytes(props['reg'].value) if 'reg' in props else b''
    out = []
    for off in range(0, len(reg) - 15, 16):
        address, size = int.from_bytes(reg[off:off + 8], 'little'), int.from_bytes(reg[off + 8:off + 16], 'little')
        for child, parent, length in spans:
            if child <= address < child + length:
                out.append((parent + address - child, parent + address - child + size))
                break
    return out


def derive(before, published, withdrawn, nodes=NODES):
    """What the two trees say (`before` has the withdrawn nodes, `published` is the guest's tree; `withdrawn` the paths that the profile takes out): the windows of each withdrawn arm-io
    child (a window that a retained node covers wholly is SHARED and not listed; a retained node inside a window is a HOLE of it; a retained window that covers a window in part is an
    error), the holes, the shared windows and the windows of the DARTs that stay. ValueError when a count or a shape is not the one the words are written for (`nodes` is the count)."""
    if len(withdrawn) != nodes:
        raise ValueError(f'{len(withdrawn)} nodes are withdrawn; the words are written for {nodes}')
    raw = bytes(before.nodes[ARM_IO]['ranges'].value)
    spans = [tuple(int.from_bytes(raw[o + 8 * i:o + 8 * i + 8], 'little') for i in range(3)) for o in range(0, len(raw) - 23, 24)]
    retained = {path: _translate(published, path, spans) for path in published.nodes if path.rsplit('/', 1)[0] == ARM_IO}
    by_node, holes, shared = {}, {}, set()
    for path in sorted(withdrawn):
        if path.rsplit('/', 1)[0] != ARM_IO:
            continue
        node = path.rsplit('/', 1)[1]
        for window in _translate(before, path, spans):
            overlapping = [(name, w) for name, ws in retained.items() for w in ws if w[0] < window[1] and window[0] < w[1]]
            if any(w[0] <= window[0] and window[1] <= w[1] for _, w in overlapping):
                shared.add(window)
                continue
            if any(not (window[0] <= w[0] and w[1] <= window[1]) for _, w in overlapping):
                raise ValueError(f'{path}: the window {upper(window)} is covered in part by a retained node')
            by_node.setdefault(node, []).append(window)
            if overlapping:
                holes.setdefault(node, []).extend(w for _, w in overlapping)
    darts = {name: retained[f'{ARM_IO}/{name}'][0] for name in (SCALER_DART[0], VIDEO_DART[0]) if retained.get(f'{ARM_IO}/{name}')}
    return dict(by_node={n: tuple(sorted(w)) for n, w in by_node.items()}, holes={n: tuple(sorted(h)) for n, h in holes.items()}, shared=tuple(sorted(shared)), darts=darts)


def check(before, published, withdrawn):
    """ValueError, naming the first difference, unless the trees give the table that the words render."""
    got, want = derive(before, published, withdrawn), expected()
    for key in want:
        if got[key] != want[key]:
            raise ValueError(f'the windows of the withdrawn nodes are not the ones that the record names ({key}: the trees say {got[key]}, the words say {want[key]})')
