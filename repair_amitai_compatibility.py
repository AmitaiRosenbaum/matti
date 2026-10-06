"""Apply portable Unicode tags and unnumbered album folders to amitai-songs."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import unicodedata

from mutagen.id3 import ID3, Frames, TXXX, SYLT
from clean_metadata import audio_hash
from music import load_links

ROOT = Path(__file__).resolve().parent
LIBRARY = ROOT / 'downloads' / 'amitai-songs'


def normalize(tags):
    for frame in tags.values():
        if hasattr(frame, 'encoding'):
            frame.encoding = 1  # UTF-16, explicitly marked with a byte-order mark
        if hasattr(frame, 'text'):
            if isinstance(frame, SYLT):
                frame.text = [(unicodedata.normalize('NFC', text), timestamp)
                              for text, timestamp in frame.text]
            elif isinstance(frame.text, str):
                frame.text = unicodedata.normalize('NFC', frame.text)
            else:
                frame.text = [unicodedata.normalize('NFC', str(s)) for s in frame.text]
        if hasattr(frame, 'desc'):
            frame.desc = unicodedata.normalize('NFC', frame.desc)
        if hasattr(frame, 'sub_frames'):
            normalize(frame.sub_frames)


def artwork(tags):
    return [hashlib.sha256(f.data).hexdigest() for f in tags.getall('APIC')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    folder_file = LIBRARY / 'playlist-folders.json'
    mapping = json.loads(folder_file.read_text(encoding='utf-8')) if folder_file.exists() else {}
    links = load_links(ROOT / 'amitai-songs.txt')
    groups = []
    targets = set()
    for folder in sorted((LIBRARY / 'Playlists').iterdir()):
        if not folder.is_dir():
            continue
        target = folder.with_name(re.sub(r'^\d+\s*-\s*', '', folder.name))
        assert target.resolve().is_relative_to(LIBRARY.resolve())
        if str(target).casefold() in targets or (target != folder and target.exists()):
            raise ValueError(f'Folder collision: {target!r}')
        targets.add(str(target).casefold())
        files = sorted(folder.glob('*.mp3'))
        tags = [ID3(f) for f in files]
        if not tags:
            continue
        albums = {str(t.get('TALB', '')) for t in tags}
        artists = {str(t.get('TPE2', '')) for t in tags}
        assert len(albums) == len(artists) == 1 and '' not in albums | artists
        dates = Counter(str(t['TDRC']) for t in tags if 'TDRC' in t)
        date = dates.most_common(1)[0][0] if dates else None
        groups.append((folder, target, files, artists.pop(), date))
        match = re.match(r'^(\d+) - ', folder.name)
        if match:
            item = next(i for i in links if i['input_index'] == int(match[1]))
            mapping[item['url']] = str(target.relative_to(LIBRARY)).replace('\\', '/')
    print(f'{sum(len(g[2]) for g in groups)} files; {sum(g[0] != g[1] for g in groups)} folder renames.', flush=True)
    if not args.apply:
        return
    backup = ROOT / 'metadata-backups' / ('musicolet-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True)
    manifest = {'files': [], 'folders': [], 'policy': 'ID3v2.3 UTF-16; artist=album artist; original track artists in PERFORMERS'}
    protected = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in LIBRARY.iterdir()
                 if p.name in ('downloaded.txt', 'completed-collections.json', 'failed.txt')}
    count = 0
    try:
        for folder, target, files, artist, date in groups:
            for path in files:
                saved = backup / path.relative_to(LIBRARY)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, saved)
                before = audio_hash(path)
                tags = ID3(path)
                pictures = artwork(tags)
                original_artist = str(tags['TPE1'])
                if original_artist != artist and 'TXXX:PERFORMERS' not in tags:
                    tags.add(TXXX(encoding=1, desc='PERFORMERS', text=[original_artist]))
                tags.add(Frames['TPE1'](encoding=1, text=[artist]))
                old_date = str(tags.get('TDRC', ''))
                if date and old_date != date:
                    if old_date:
                        tags.add(TXXX(encoding=1, desc='SOURCE_RELEASE_DATE', text=[old_date]))
                    tags.add(Frames['TDRC'](encoding=1, text=[date]))
                normalize(tags)
                expected = {k: str(tags[k]) for k in ('TIT2', 'TPE1', 'TPE2', 'TALB', 'TRCK')}
                tags.update_to_v23()
                try:
                    tags.save(path, v2_version=3, v1=0, v23_sep='; ')
                    actual = ID3(path)
                    assert actual.version == (2, 3, 0)
                    assert all(str(actual[k]) == v for k, v in expected.items())
                    assert artwork(actual) == pictures
                    assert audio_hash(path) == before
                except BaseException:
                    shutil.copy2(saved, path)
                    raise
                manifest['files'].append({'file': str(path.relative_to(LIBRARY)), 'audio_sha256': before})
                count += 1
                if count % 100 == 0:
                    print(f'{count} files saved and verified', flush=True)
        # All tag writes must succeed before moving any folder.
        for folder, target, *_ in groups:
            if folder != target:
                folder.rename(target)
                manifest['folders'].append({'old': str(folder.relative_to(LIBRARY)), 'new': str(target.relative_to(LIBRARY))})
        folder_file.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding='utf-8')
        assert all(hashlib.sha256((LIBRARY / n).read_bytes()).hexdigest() == h for n, h in protected.items())
    finally:
        (backup / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Verified {count} files. Renamed {len(manifest["folders"])} folders. Backup: {backup}', flush=True)


if __name__ == '__main__':
    main()
