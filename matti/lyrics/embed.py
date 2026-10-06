"""Embed matching UTF-8 .txt lyrics into MP3s; preview unless --apply is supplied."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil

from mutagen.id3 import ID3, USLT
from matti.audio import audio_hash
from matti.tracks import iter_mp3

from matti.paths import workspace
ROOT = workspace()


def embed(path, text, backup, replace=False):
    tags = ID3(path)
    if tags.getall('USLT') or tags.getall('SYLT'):
        if not replace or tags.getall('SYLT'):
            return False
        if len(tags.getall('USLT')) == 1 and tags.getall('USLT')[0].text == text:
            return False
    saved = Path(backup)
    saved.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, saved)
    before = audio_hash(path)
    pictures = [f.data for f in tags.getall('APIC')]
    version = tags.version[1]
    try:
        tags.delall('USLT')
        tags.add(USLT(encoding=1, lang='und', desc='', text=text))
        tags.save(path, v2_version=version)
        actual = ID3(path)
        if (actual.getall('USLT')[0].text != text or
                [f.data for f in actual.getall('APIC')] != pictures or
                audio_hash(path) != before):
            raise ValueError('Lyrics, artwork or audio verification failed')
    except BaseException:
        shutil.copy2(saved, path)
        raise
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=ROOT / 'library')
    parser.add_argument('--backup-dir', type=Path, help='Backup root (default: library/.matti/backups)')
    parser.add_argument('--apply', action='store_true', help='Back up, embed and verify lyrics')
    parser.add_argument('--replace', action='store_true', help='Replace existing plain lyrics; preserve synced lyrics')
    args = parser.parse_args(argv)
    library = args.library.resolve()
    if not library.is_dir():
        parser.error(f'Library directory does not exist: {library}')
    backup = (args.backup_dir or library / '.matti' / 'backups') / ('lyrics-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    records = []
    errors = 0
    for path in sorted(iter_mp3(library)):
        txt = path.with_suffix('.txt')
        if not txt.is_file():
            continue
        record = {'file': str(path.relative_to(library))}
        try:
            if not path.resolve().is_relative_to(library) or not txt.resolve().is_relative_to(library):
                raise ValueError('File resolves outside library')
            text = txt.read_text(encoding='utf-8-sig').strip()
            tags = ID3(path)
            if not text:
                record['status'] = 'empty text'
            elif tags.getall('SYLT') or (tags.getall('USLT') and not args.replace):
                record['status'] = 'existing lyrics preserved'
            elif len(tags.getall('USLT')) == 1 and tags.getall('USLT')[0].text == text:
                record['status'] = 'already embedded'
            elif args.apply:
                embed(path, text, backup / path.relative_to(library), args.replace)
                record['status'] = 'embedded'
            else:
                record['status'] = 'would embed'
        except Exception as error:
            record.update(status='error', error=str(error))
            errors += 1
            print(f'Error: {str(path)!r}: {error}', flush=True)
        records.append(record)
    if args.apply and backup.exists():
        (backup / 'manifest.json').write_text(json.dumps(
            {'library': str(library), 'files': records}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Backup: {backup}')
    from collections import Counter
    print(dict(Counter(r['status'] for r in records)))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
