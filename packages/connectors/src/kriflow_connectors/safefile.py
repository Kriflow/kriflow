"""Files the core reads from or writes into a folder a sandboxed process can write (its scratch
HOME, a stage's folder), without following a link the process planted there.

Every step opens one path component at a time with O_NOFOLLOW from the folder before it, so a
symlink (to `secrets.json`, to `setup.json`) anywhere on the way is refused, and a file read is
checked to be a regular file under a size limit on the descriptor it was read from, never by a
second lookup that a process could swap between the check and the use.
"""

import contextlib
import os
import stat
from collections.abc import Iterator
from pathlib import Path

DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


class Unsafe(OSError):
    """A link, a non-regular file, a path out of its root or a file over its limit."""


def _parts(rel: str | Path) -> list[str]:
    parts = [p for p in Path(rel).parts if p not in ("", ".")]
    if not parts or any(p in ("..", "/") or "/" in p for p in parts):
        raise Unsafe(f"not a path inside its folder: {rel}")
    return parts


@contextlib.contextmanager
def folder(root: Path, rel: str | Path = "", make: bool = False) -> Iterator[int]:
    """A descriptor of the folder `rel` under `root`, each step opened without following a link;
    with `make`, missing steps are created (mode 700). `root` itself is the core's own path."""
    fd = os.open(root, DIR)
    try:
        for part in _parts(rel) if str(rel) not in ("", ".") else []:
            if make:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o700, dir_fd=fd)
            try:
                nxt = os.open(part, DIR, dir_fd=fd)
            except OSError as exc:
                raise Unsafe(f"{part} is not a plain folder") from exc
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


def read_at(at: int, name: str, limit: int) -> bytes:
    """The bytes of regular file `name` in the folder `at`, at most `limit` of them. Raises
    Unsafe for a link, a non-regular file or one over `limit`; FileNotFoundError."""
    _parts(name)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=at)
    except FileNotFoundError:
        raise
    except OSError as exc:  # ELOOP: a link
        raise Unsafe(f"{name} is a link") from exc
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise Unsafe(f"{name} is not a regular file")
        if info.st_size > limit:
            raise Unsafe(f"{name} is over {limit} bytes")
        data = f.read(limit + 1)
    if len(data) > limit:
        raise Unsafe(f"{name} is over {limit} bytes")
    return data


def read(root: Path, rel: str | Path, limit: int) -> bytes:
    """`read_at` for file `rel` under `root`, each folder on the way opened without a link."""
    *dirs, name = _parts(rel)
    with folder(root, Path(*dirs) if dirs else "") as at:
        return read_at(at, name, limit)


def write(at: int, name: str, data: bytes, mode: int = 0o600) -> None:
    """`data` as file `name` in the folder `at`, written to a fresh temporary file (never through
    a link a process left in its place) and moved over it."""
    _parts(name)
    tmp = f".{name}.{os.getpid()}.tmp"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp, dir_fd=at)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(tmp, flags, mode, dir_fd=at)
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), mode)
        f.write(data)
    os.replace(tmp, name, src_dir_fd=at, dst_dir_fd=at)


def write_in(root: Path, rel: str | Path, data: bytes, mode: int = 0o600) -> None:
    *dirs, name = _parts(rel)
    with folder(root, Path(*dirs) if dirs else "") as at:
        write(at, name, data, mode)


def remove(root: Path, rel: str | Path) -> None:
    """Delete file `rel` under `root` (a link itself, never its target); gone already, or a
    folder in its place, is left be."""
    *dirs, name = _parts(rel)
    with contextlib.suppress(OSError), folder(root, Path(*dirs) if dirs else "") as at:
        os.unlink(name, dir_fd=at)


def files(root: Path) -> Iterator[Path]:
    """Every regular file under `root`, relative to it, never through a linked folder."""
    for top, dirs, names in os.walk(root, followlinks=False):
        here = Path(top)
        dirs[:] = [d for d in dirs if not (here / d).is_symlink()]
        for n in names:
            if not (here / n).is_symlink():
                yield (here / n).relative_to(root)
