# SPDX-License-Identifier: MIT
"""A staged data file of more than 256 MiB, which tools/stage-sp11-boot-media.py must never read whole (docs/SP11-STICK-ROOT.md): the root-carry pair's GUEST.DAT is 1.93 GB. It exists behind the gate of
tools/sp11_autorun.py (`bigfile`, or SP11_ROOT_BUILD=1 in the environment, which raises the stager's data limit from 256 MiB to 2 GiB): with the gate off no file is over 256 MiB, none is ever a BigData, and every
file the stager reads is read as it always was.

`validate` is the stager's `read_validated_input` for such a file: it opens it without following a link, holds the descriptor to the file that the path checks saw (device, inode and size) and hashes it in
blocks, so that the resident set is one block. `BigData.copy_to` is the stager's copy: it reads the file again in blocks, hashes what it copies and refuses (removing the partial copy) unless the digest is the one
that `validate` saw, so a file replaced between the check and the copy is never staged, which is the rule that the in-memory path keeps by reading once. Evidence: EXPERIMENTAL, host only."""
import hashlib
import os
import stat

BIG_MIN = 256 << 20         # the stager's own data limit without the gate: a file up to it is read whole, as before
BLOCK = 1 << 20


class BigData:
    """The identity of a big data file (its path, size, device and inode and the SHA-256 that `validate` read); `len()` is its size."""

    def __init__(self, path, size, sha256, ident, refusal):
        self.path, self.size, self.sha256, self.ident, self.refusal = path, size, sha256, ident, refusal

    def __len__(self):
        return self.size

    def copy_to(self, dest):
        """Copy the file to `dest` (a new or an overwritten file) in blocks; the copy's SHA-256 must be the validated one, else the partial file is removed and the stager's own
        `input_changed_while_reading` is raised."""
        source = _open(self.path, self.refusal)
        digest, total = hashlib.sha256(), 0
        try:
            info = os.fstat(source)
            if (info.st_dev, info.st_ino, info.st_size) != (*self.ident, self.size):
                raise self.refusal('input_changed_while_reading', str(self.path))
            with open(dest, 'wb') as out:
                while block := os.read(source, BLOCK):
                    digest.update(block)
                    total += len(block)
                    out.write(block)
        finally:
            os.close(source)
        if total != self.size or digest.hexdigest() != self.sha256:
            os.unlink(dest)
            raise self.refusal('input_changed_while_reading', str(self.path))


def _open(path, refusal):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_CLOEXEC', 0)
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise refusal('input_unreadable', f'{path}: {exc.strerror}') from exc


def validate(path, expected, refusal):
    """(facts, BigData) of a regular file of more than BIG_MIN bytes: `expected` is the lstat() taken when the path was validated, `refusal` the stager's Refusal class. The facts are those of a
    data file ({'path', 'size', 'sha256'})."""
    fd = _open(path, refusal)
    digest, total = hashlib.sha256(), 0
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino) or opened.st_size != expected.st_size:
            raise refusal('input_changed_while_reading', str(path))
        while block := os.read(fd, BLOCK):
            digest.update(block)
            total += len(block)
            if total > expected.st_size:
                raise refusal('input_changed_while_reading', str(path))
    finally:
        os.close(fd)
    if total != expected.st_size:
        raise refusal('input_changed_while_reading', str(path))
    return ({'path': str(path), 'size': total, 'sha256': digest.hexdigest()},
            BigData(path, total, digest.hexdigest(), (opened.st_dev, opened.st_ino), refusal))
