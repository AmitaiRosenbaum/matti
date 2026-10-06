"""Read-only coverage reporting for any MP3 library."""
from collections import Counter
import json
from mutagen.id3 import ID3
from .tracks import iter_mp3


def status(library):
    if not library.is_dir():
        raise ValueError(f'Library does not exist: {library}')
    path = library / 'lyrics-state.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    coverage = Counter()
    for song in sorted(iter_mp3(library)):
        if not song.resolve().is_relative_to(library.resolve()):
            raise ValueError(f'MP3 resolves outside library: {song}')
        try:
            tags = ID3(song)
            if song.with_suffix('.lrc').is_file() or tags.getall('SYLT'):
                coverage['synced'] += 1
            elif song.with_suffix('.txt').is_file() or tags.getall('USLT'):
                coverage['plain'] += 1
            elif state.get(str(song.relative_to(library)), {}).get('status') == 'instrumental':
                coverage['instrumental'] += 1
            else:
                coverage['missing'] += 1
        except Exception:
            coverage['unreadable'] += 1
    return {'library': str(library), 'tracks': sum(coverage.values()), 'coverage': dict(coverage)}
