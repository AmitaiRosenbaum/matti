"""Run history; existing per-track and downloader checkpoints stay authoritative."""
from contextlib import contextmanager
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
import os


@contextmanager
def library_lock(folder):
    """OS-managed lock releases automatically even if the process crashes."""
    with (folder / 'writer.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
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
        except OSError as error:
            raise ValueError('Another matti stage is writing this library; retry after it finishes') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def record(library, stage):
    folder = library / '.matti'
    folder.mkdir(parents=True, exist_ok=True)
    with library_lock(folder), closing(sqlite3.connect(folder / 'runs.sqlite3')) as db:
        db.execute('CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, stage TEXT, started TEXT, finished TEXT, exit_code INTEGER)')
        cursor = db.execute('INSERT INTO runs(stage, started) VALUES (?, ?)',
                            (stage, datetime.now(timezone.utc).isoformat()))
        db.commit()
        outcome = {'code': 1}
        try:
            yield outcome
        finally:
            db.execute('UPDATE runs SET finished=?, exit_code=? WHERE id=?',
                       (datetime.now(timezone.utc).isoformat(), outcome['code'], cursor.lastrowid))
            db.commit()
