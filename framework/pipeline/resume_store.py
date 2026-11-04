"""Atomic, checksummed checkpoints for the opt-in audited resume runner."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep sibling temp names short enough for ordinary Windows path limits.
    temporary = path.with_name(uuid.uuid4().hex[:16] + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def checkpoint(path, value):
    atomic_json(path, {'checkpoint_version': 1, 'sha256': digest(value), 'payload': value})


def read_checkpoint(path):
    value = read_json(path)
    if value.get('checkpoint_version') != 1 or digest(value.get('payload')) != value.get('sha256'):
        raise ValueError('RESUME_CHECKPOINT_HASH_MISMATCH: ' + str(path))
    return value['payload']


def contained(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('RESUME_PATH_OUTSIDE_OWNER')
    return path


@contextmanager
def exclusive_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.resume.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('RESUME_RUN_LOCKED') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def disk_usage(root):
    total = count = 0
    if Path(root).exists():
        for directory, dirs, files in os.walk(root):
            dirs[:] = [n for n in dirs if not (Path(directory) / n).is_symlink()]
            for name in files:
                path = Path(directory) / name
                # Validation rotates its current workspace into history while
                # summaries are being refreshed.  A file returned by os.walk can
                # therefore disappear during either check below on Windows.  Disk
                # usage is informational, so omit that transient entry rather
                # than aborting an otherwise durable batch checkpoint.
                try:
                    if path.is_symlink():
                        continue
                    size = path.stat().st_size
                except FileNotFoundError:
                    continue
                total += size
                count += 1
    return {'logical_bytes': total, 'files': count}
