"""Remove YouTube IDs and tidy filenames; retain an auditable rename manifest."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from mutagen.id3 import ID3

ROOT = Path(__file__).resolve().parent


def tidy(name):
    name = unicodedata.normalize('NFC', name)
    name = re.sub(r'\s*\[[A-Za-z0-9_-]{11}\](?=\.mp3$)', '', name)
    name = re.sub(r'[＂"]\s*([^＂"]*?)\s*[＂"]', lambda m: '“' + m[1].strip() + '”', name)
    name = re.sub(r'“\s*(.*?)\s*”', lambda m: '“' + m[1].strip() + '”', name)
    return re.sub(r'\s+', ' ', name).strip()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    library = ROOT / 'classical_music'
    plans, targets = [], set()
    for source in sorted(library.glob('*/*.mp3')):
        if source.name.endswith(('.temp.mp3', '.orig.mp3')):
            continue
        target = source.with_name(tidy(source.name))
        key = str(target).casefold()
        if key in targets or (target != source and target.exists()):
            raise ValueError(f'Filename collision: {target}')
        targets.add(key)
        if target != source:
            plans.append(dict(old=str(source.relative_to(library)), new=str(target.relative_to(library))))
    print(f'{len(plans)} filenames to clean.', flush=True)
    if not args.apply:
        return
    manifest = ROOT / ('filename-renames-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    manifest.write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        for entry in plans:
            source, target = library / entry['old'], library / entry['new']
            try:
                before = digest(source)
                source.rename(target)
                entry['status'] = 'renamed'
                assert digest(target) == before, 'File content changed'
                entry['sha256'] = before
            except Exception as error:
                entry['error'] = str(error)
    finally:
        manifest.write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Renamed: {sum(e.get("status") == "renamed" for e in plans)}; errors: {sum("error" in e for e in plans)}')
    print(f'Rename manifest: {manifest}')
    titles_with_ids = []
    for file in library.glob('*/*.mp3'):
        if re.search(r'\[[A-Za-z0-9_-]{11}\]', str(ID3(file).get('TIT2', ''))):
            titles_with_ids.append(file.name)
    print(f'Embedded titles containing video IDs: {len(titles_with_ids)}')


if __name__ == '__main__':
    main()
