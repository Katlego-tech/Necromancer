"""Safe extraction of the CMU Enron tar.gz.

Guards: no absolute paths, no '..' escapes, no symlinks/hardlinks/devices, a total
size cap (decompression-bomb guard). Files are written by hand, so archive-supplied
ownership and permission bits are ignored.

Usage:
    python3 -m spirits.enron.extract data/enron/enron_mail_20150507.tar.gz data/enron
"""
from __future__ import annotations

import os
import shutil
import sys
import tarfile
from dataclasses import dataclass, field
from typing import List

DEFAULT_MAX_BYTES = 8 * 1024**3


class UnsafeArchiveError(Exception):
    pass


@dataclass
class ExtractStats:
    files: int = 0
    dirs: int = 0
    bytes: int = 0
    skipped_existing: int = 0
    skipped_special: List[str] = field(default_factory=list)


def _target(dest_real: str, name: str) -> str:
    if os.path.isabs(name) or name.startswith(("/", "\\")):
        raise UnsafeArchiveError(f"absolute path in archive: {name!r}")
    if "\x00" in name:
        raise UnsafeArchiveError(f"NUL byte in archive path: {name!r}")
    target = os.path.realpath(os.path.join(dest_real, name))
    if target != dest_real and not target.startswith(dest_real + os.sep):
        raise UnsafeArchiveError(f"path escapes destination: {name!r}")
    return target


def safe_extract(archive: str, dest: str, max_bytes: int = DEFAULT_MAX_BYTES) -> ExtractStats:
    os.makedirs(dest, exist_ok=True)
    dest_real = os.path.realpath(dest)
    stats = ExtractStats()
    with tarfile.open(archive, mode="r|gz") as tar:
        for member in tar:
            target = _target(dest_real, member.name)
            if member.isdir():
                os.makedirs(target, mode=0o755, exist_ok=True)
                stats.dirs += 1
                continue
            if not member.isfile():
                stats.skipped_special.append(member.name)
                continue
            stats.bytes += member.size
            if stats.bytes > max_bytes:
                raise UnsafeArchiveError(f"archive exceeds {max_bytes} bytes when extracted")
            if os.path.isfile(target) and os.path.getsize(target) == member.size:
                stats.skipped_existing += 1
                continue
            os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                stats.skipped_special.append(member.name)
                continue
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(target, 0o644)
            stats.files += 1
    return stats


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    s = safe_extract(sys.argv[1], sys.argv[2])
    print(f"extracted {s.files} files, {s.dirs} dirs, {s.bytes} bytes; "
          f"already present {s.skipped_existing}; skipped special {len(s.skipped_special)}")
