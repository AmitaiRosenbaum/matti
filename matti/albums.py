"""Normalize album titles and folders while retaining download and lyric history."""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import unicodedata

from mutagen.id3 import ID3, TALB, TXXX
from .audio import audio_hash
from .state import atomic_json
from .tracks import iter_mp3


def album_title(text, artist=''):
    """Remove upload decoration without guessing a different release or language."""
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'[\u200e\u200f\u202a-\u202e\u2066-\u2069]', '', text)
    text = text.translate(str.maketrans({'⧸': '/', '／': '/', '：': ':', '＂': '"', '｜': '|'}))
    text = re.sub(r'\.{3,}', '…', text)
    text = re.sub(r'\s+[–—]\s+', ' - ', text)
    decoration = r'(?:full\s+album|complete\s+album|[áa]lbum\s+completo|אלבום\s+מלא|האלבום\s+המלא)'
    text = re.sub(r'\s*[\[(]\s*' + decoration + r'\s*[\])]', '', text, flags=re.I)
    text = re.sub(r'(?:\s*[-|:]\s*|\s+)' + decoration + r'\s*$', '', text, flags=re.I)
    text = re.sub(r'^album\s*:\s*', '', text, flags=re.I)
    text = re.sub(r'\s+', ' ', text).strip(' -|')
    credits = {artist, artist.replace('; ', ' & ')}
    for credit in sorted(credits, key=len, reverse=True):
        if credit:
            text = re.sub(r'^' + re.escape(credit) + r'\s+[-/]\s+', '', text, flags=re.I)
    if text.isupper() and re.search(r'[A-Z]', text):
        small = {'a', 'as', 'o', 'os', 'e', 'de', 'da', 'das', 'do', 'dos', 'of', 'the', 'and', 'to', 'in', 'is'}
        words = text.split()
        text = ' '.join(word if re.fullmatch(r'[IVX]+', word) else
                        word.lower() if index and word.lower() in small else word.capitalize()
                        for index, word in enumerate(words))
    text = re.sub(r'\(deluxe\s+edition\)', '(Deluxe Edition)', text, flags=re.I)
    return text


def folder_name(artist, album):
    value = unicodedata.normalize('NFC', f'{artist.replace("; ", " & ")} - {album}')
    value = value.translate(str.maketrans(dict(zip('<>:"/\\|?*', '＜＞：＂／＼｜？＊'))))
    value = re.sub(r'[\x00-\x1f]', '', value).rstrip(' .')
    if len(value) > 160:
        value = value[:145].rstrip(' .') + ' - ' + hashlib.sha256(value.encode()).hexdigest()[:10]
    return value


def read_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def relative(path, library):
    if not path.resolve().is_relative_to(library):
        raise ValueError(f'Path resolves outside library: {path}')
    return path.relative_to(library).as_posix()


def migrate_path(value, renames):
    """Support existing Windows keys and keep their separator style."""
    normalized = value.replace('\\', '/')
    for old, new in renames.items():
        if normalized == old or normalized.startswith(old + '/'):
            updated = new + normalized[len(old):]
            return updated.replace('/', '\\') if '\\' in value else updated
    return value


def plan(library):
    library = library.resolve()
    if not library.is_dir():
        raise ValueError(f'Library does not exist: {library}')
    mapping = read_json(library / 'playlist-folders.json', {})
    policies = read_json(library / 'album-policy.json', {})
    if not isinstance(mapping, dict) or not isinstance(policies, dict):
        raise ValueError('Album policy and playlist mapping must be JSON objects')
    by_folder = defaultdict(list)
    for path in iter_mp3(library):
        by_folder[path.parent].append(path)
    entries, folders, skipped = [], [], []
    targets = set()
    for folder, files in sorted(by_folder.items()):
        old = relative(folder, library)
        urls = [url for url, destination in mapping.items() if destination.replace('\\', '/') == old]
        policy = next((policies[url] for url in urls if url in policies), None)
        if policy and (not isinstance(policy, dict) or not isinstance(policy.get('album'), str) or not isinstance(policy.get('artist'), str)):
            raise ValueError(f'Invalid album policy for {old}')
        tags = [(path, ID3(path)) for path in files]
        pairs = {(album_title(str(tag.get('TALB', '')), str(tag.get('TPE2', tag.get('TPE1', '')))),
                  str(tag.get('TPE2', tag.get('TPE1', ''))).strip()) for _, tag in tags}
        # A playlist containing multiple albums is a collection, not a release.
        single = len(pairs) == 1 and all(next(iter(pairs)))
        movable = folder != library and old != 'Songs' and (policy or single)
        canonical, artist = (policy['album'], policy['artist']) if policy else next(iter(pairs)) if single else ('', '')
        canonical = album_title(canonical, artist)
        if movable:
            destination = folder.with_name(folder_name(artist, canonical))
            new = relative(destination, library)
            key = new.casefold()
            if key in targets or (new != old and destination.exists() and not destination.samefile(folder)):
                raise ValueError(f'Album folder collision: {new}')
            targets.add(key)
            folders.append({'old': old, 'new': new, 'album': canonical, 'artist': artist, 'tracks': len(files)})
            for url in urls:
                policies[url] = {'album': canonical, 'artist': artist}
                mapping[url] = new
        else:
            skipped.append({'folder': old, 'reason': 'mixed albums, missing metadata, or individual songs'})
        for path, tag in tags:
            original = str(tag.get('TALB', ''))
            desired = canonical if policy and movable else album_title(original, str(tag.get('TPE2', tag.get('TPE1', ''))))
            if desired and desired != original:
                entries.append({'file': relative(path, library), 'old': original, 'new': desired})
    return {'library': str(library), 'folders': folders, 'tags': entries, 'skipped': skipped,
            'mapping': mapping, 'policies': policies}


def verify_tags(path, tags, original):
    before = audio_hash(path)
    protected = {key: frame.pprint() for key, frame in tags.items() if key != 'TALB'}
    pictures = [frame.data for frame in tags.getall('APIC')]
    version = tags.version[1]
    tags.add(TALB(encoding=1, text=[original['new']]))
    if 'TXXX:SOURCE_ALBUM' not in tags:
        tags.add(TXXX(encoding=1, desc='SOURCE_ALBUM', text=[original['old']]))
    tags.save(path, v2_version=version)
    actual = ID3(path)
    if (str(actual.get('TALB', '')) != original['new'] or audio_hash(path) != before or
            [frame.data for frame in actual.getall('APIC')] != pictures or
            any(key not in actual or actual[key].pprint() != value for key, value in protected.items())):
        raise ValueError(f'Audio, artwork, lyrics or metadata verification failed: {path}')
    return before


def standardize(library, apply=False):
    library = library.expanduser().resolve()
    preview = plan(library)
    renames = {row['old']: row['new'] for row in preview['folders'] if row['old'] != row['new']}
    print(f'Albums: {len(preview["folders"])} consistent folders; {len(renames)} renames; {len(preview["tags"])} tag changes.', flush=True)
    for old, new in renames.items():
        print(f'  {old} -> {new}', flush=True)
    if not apply:
        return preview
    state_files = ('playlist-folders.json', 'album-policy.json', 'lyrics-state.json')
    snapshots = {name: (library / name).read_bytes() if (library / name).exists() else None for name in state_files}
    state = read_json(library / 'lyrics-state.json', {})
    migrated = {}
    for key, row in state.items():
        new_key = migrate_path(key, renames)
        if new_key in migrated:
            raise ValueError(f'Lyric checkpoint collision: {new_key}')
        updated = dict(row)
        if 'saved' in updated:
            updated['saved'] = migrate_path(updated['saved'], renames)
        migrated[new_key] = updated
    updates = {'playlist-folders.json': preview['mapping'], 'album-policy.json': preview['policies']}
    if 'lyrics-state.json' in snapshots and snapshots['lyrics-state.json'] is not None:
        updates['lyrics-state.json'] = migrated
    updates = {name: value for name, value in updates.items() if value != read_json(library / name, {})}
    if not (renames or preview['tags'] or updates):
        print('Album names already standardized.', flush=True)
        return preview
    backup = library / '.matti' / 'backups' / ('albums-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True)
    protected = {name: (library / name).read_bytes() for name in ('downloaded.txt', 'completed-collections.json', 'failed.txt') if (library / name).exists()}
    manifest = {'plan': preview, 'status': 'prepared', 'tag_verifications': [], 'backup': str(backup)}
    for name, data in snapshots.items():
        if data is not None:
            (backup / name).write_bytes(data)
    # All original MP3s are backed up before changing any tags or folders.
    for entry in preview['tags']:
        path = library / entry['file']
        saved = backup / 'tracks' / entry['file']
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, saved)
        if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(saved.read_bytes()).digest():
            raise ValueError(f'Backup verification failed: {path}')
    atomic_json(backup / 'manifest.json', manifest)
    moved = []
    try:
        for entry in preview['tags']:
            path = library / entry['file']
            before = verify_tags(path, ID3(path), entry)
            manifest['tag_verifications'].append({'file': entry['file'], 'audio_sha256': before})
        for old, new in renames.items():
            source, destination = library / old, library / new
            relative(source, library)
            relative(destination, library)
            source.rename(destination)
            moved.append((old, new))
        for name, value in updates.items():
            atomic_json(library / name, value)
        if any((library / name).read_bytes() != data for name, data in protected.items()):
            raise ValueError('Download history changed during album normalization')
        manifest['status'] = 'complete'
        atomic_json(backup / 'manifest.json', manifest)
    except BaseException:
        for old, new in reversed(moved):
            (library / new).rename(library / old)
        for entry in preview['tags']:
            shutil.copy2(backup / 'tracks' / entry['file'], library / entry['file'])
        for name, data in snapshots.items():
            path = library / name
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
        manifest['status'] = 'rolled back'
        atomic_json(backup / 'manifest.json', manifest)
        raise
    print(f'Albums standardized. Backup and manifest: {backup}', flush=True)
    return preview


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=Path('library'))
    parser.add_argument('--apply', action='store_true', help='Apply verified changes with backups')
    args = parser.parse_args(argv)
    standardize(args.library, args.apply)
    return 0
