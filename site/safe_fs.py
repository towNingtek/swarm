"""File access inside a customer-controlled directory, without following links.

A site's ``dsh-home`` and ``workspace`` are writable by code the customer runs
in their container (as the same uid as the platform). The platform still reads
and writes a few files there. Checking a path with ``lstat`` and then opening
it by name leaves a window in which a directory can be swapped for a symlink,
so every access here walks the path one component at a time with
``O_NOFOLLOW`` directory file descriptors (``openat``). A symlink anywhere
under the root makes the call fail; it is never followed.

The root itself (the bind-mount point) is trusted: the container cannot
replace its own mount point.
"""
from __future__ import annotations

import errno
import os
import secrets
import stat
from pathlib import Path

MAX_READ = 1_000_000


class UnsafePath(OSError):
    pass


def _parts(rel) -> list[str]:
    parts = [p for p in Path(rel).parts]
    if not parts or Path(rel).is_absolute() or any(p in ('', '.', '..') for p in parts):
        raise UnsafePath(errno.EINVAL, f'unsafe relative path {rel!s}')
    return parts


def _open_root(root) -> int:
    root = Path(root)
    info = os.lstat(root)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise UnsafePath(errno.ENOTDIR, 'root must be a real directory')
    return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _walk(root, dirs, *, create=False, mode=0o755) -> int:
    """fd of root/dirs...; each component must be a real directory."""
    fd = _open_root(root)
    try:
        for name in dirs:
            try:
                nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, mode, dir_fd=fd)
                except FileExistsError:
                    pass
                nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UnsafePath(exc.errno, f'{name}: not a real directory') from None
                raise
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _lstat_at(fd, name):
    try:
        return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def kind(root, rel) -> str | None:
    """'file', 'dir', 'link', 'other' or None (absent, or a parent is unsafe)."""
    parts = _parts(rel)
    try:
        fd = _walk(root, parts[:-1])
    except (FileNotFoundError, UnsafePath, NotADirectoryError):
        return None
    try:
        info = _lstat_at(fd, parts[-1])
    finally:
        os.close(fd)
    if info is None:
        return None
    if stat.S_ISLNK(info.st_mode):
        return 'link'
    if stat.S_ISREG(info.st_mode):
        return 'file'
    if stat.S_ISDIR(info.st_mode):
        return 'dir'
    return 'other'


def read_bytes(root, rel, limit=MAX_READ) -> bytes:
    """Contents of a regular file. FileNotFoundError when absent; UnsafePath
    for links/specials/oversize."""
    parts = _parts(rel)
    fd = _walk(root, parts[:-1])
    try:
        try:
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise UnsafePath(exc.errno, f'{rel}: is a symlink') from None
            raise
    finally:
        os.close(fd)
    with os.fdopen(file_fd, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise UnsafePath(errno.EINVAL, f'{rel}: not a regular file')
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise UnsafePath(errno.EFBIG, f'{rel}: too large')
    return data


def read_text(root, rel, limit=MAX_READ) -> str:
    return read_bytes(root, rel, limit).decode('utf-8')


def write_atomic(root, rel, data, mode=0o644, *, make_dirs=False, keep_mode=False) -> None:
    """Replace root/rel with data. The target, if present, must be a regular
    file; a symlink there is refused, never followed or overwritten.
    keep_mode: reuse the existing file's permission bits when there is one."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    parts = _parts(rel)
    fd = _walk(root, parts[:-1], create=make_dirs)
    try:
        info = _lstat_at(fd, parts[-1])
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise UnsafePath(errno.EEXIST, f'{rel}: exists and is not a regular file')
        if keep_mode and info is not None:
            mode = stat.S_IMODE(info.st_mode)
        temporary = f'.{parts[-1]}.{secrets.token_hex(6)}.tmp'
        file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          mode, dir_fd=fd)
        try:
            with os.fdopen(file_fd, 'wb') as handle:
                handle.write(data)
                os.fchmod(handle.fileno(), mode)
            os.replace(temporary, parts[-1], src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=fd)
            except OSError:
                pass
            raise
    finally:
        os.close(fd)


def create_new(root, rel, data, mode=0o644) -> None:
    """Create root/rel (parents too); FileExistsError if anything is there."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    parts = _parts(rel)
    fd = _walk(root, parts[:-1], create=True)
    try:
        file_fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          mode, dir_fd=fd)
        with os.fdopen(file_fd, 'wb') as handle:
            handle.write(data)
    finally:
        os.close(fd)


def make_dirs(root, rel, mode=0o755) -> None:
    os.close(_walk(root, _parts(rel), create=True, mode=mode))


def list_dir(root, rel) -> list[str]:
    fd = _walk(root, _parts(rel))
    try:
        return os.listdir(fd)
    finally:
        os.close(fd)


def unlink(root, rel) -> None:
    parts = _parts(rel)
    fd = _walk(root, parts[:-1])
    try:
        os.unlink(parts[-1], dir_fd=fd)
    finally:
        os.close(fd)
