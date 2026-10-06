"""Library scanning excludes internal caches and backed-up copies."""
import os
from pathlib import Path


def iter_mp3(library):
    for directory, folders, files in os.walk(library, followlinks=False):
        folders[:] = sorted(name for name in folders if not name.startswith('.') and name != 'metadata-backups')
        for name in sorted(files):
            if name.lower().endswith('.mp3'):
                path = Path(directory) / name
                if not path.resolve().is_relative_to(library.resolve()):
                    raise ValueError(f'MP3 resolves outside library: {path}')
                yield path
