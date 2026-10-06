"""Compare catalogue playlists to local MP3s without downloading media."""
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlparse
from mutagen.id3 import ID3

from matti.paths import workspace
ROOT = workspace()
LIBRARY = ROOT / 'classical_music'


def main():
    catalogue = json.loads((ROOT / 'catalogue.json').read_text(encoding='utf-8'))
    archive = set((LIBRARY / 'downloaded.txt').read_text(encoding='utf-8').splitlines())
    report = []
    for collection in catalogue:
        name, url = collection['name'], collection['url']
        if name == 'Mozart - Symphonies':
            continue
        print(f'Checking {name}', flush=True)
        folder = LIBRARY / name
        files = list(folder.glob('*'))
        complete = {}
        for file in files:
            match = re.search(r'\[([^]]+)\]\.mp3$', file.name)
            if match and file.stat().st_size > 0:
                complete[match[1]] = file.name
            elif file.suffix == '.mp3' and not file.name.endswith(('.temp.mp3', '.orig.mp3')) and file.stat().st_size > 0:
                tags = ID3(file)
                source = str(tags.get('TXXX:purl', ''))
                video_id = parse_qs(urlparse(source).query).get('v', [None])[0]
                if video_id:
                    complete[video_id] = file.name
        row = dict(collection, local_mp3_count=len(complete), missing=[], excluded=[],
                   leftovers=[f.name for f in files if f.name not in complete.values()])
        if '/playlist?' in url:
            result = subprocess.run([
                sys.executable, '-m', 'yt_dlp', '--ignore-config', '--flat-playlist',
                '--dump-single-json', '--skip-download', '--sleep-requests', '2',
                '--extractor-retries', '1', '--socket-timeout', '20', url,
            ], capture_output=True, text=True, encoding='utf-8', errors='replace')
            if result.returncode:
                row['error'] = result.stderr
                report.append(row)
                continue
            data = json.loads(result.stdout)
            entries = data.get('entries', [])
            row['playlist_count'] = data.get('playlist_count')
        else:
            entries = [dict(id=parse_qs(urlparse(url).query)['v'][0], title=name)]
        row['listed_count'] = len(entries)
        for index, entry in enumerate(entries, 1):
            if not entry:
                row['missing'].append(dict(position=index, title='Unknown playlist entry'))
                continue
            video_id, title = entry.get('id'), entry.get('title') or ''
            details = dict(position=index, id=video_id, title=title,
                           availability=entry.get('availability'),
                           archived=f'youtube {video_id}' in archive)
            if 'unreleased' in title.casefold():
                row['excluded'].append(details)
            elif video_id not in complete:
                row['missing'].append(details)
        row['matched_count'] = len(entries) - len(row['missing']) - len(row['excluded'])
        report.append(row)
    (ROOT / 'library-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for row in report:
        print(f"{row['name']}: {len(row['missing'])} missing, {len(row['excluded'])} excluded"
              + ('; LOOKUP FAILED' if row.get('error') else ''), flush=True)


if __name__ == '__main__':
    main()
