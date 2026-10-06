"""Fetch synced LRC and plain-text lyrics unattended, then embed plain lyrics."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

from matti.paths import workspace
ROOT = workspace()

import requests
from mutagen.mp3 import MP3
from matti.lyrics.embed import main as embed_main
from .matching import normal, similar, identity, title_variants
from .matching import plain_from_lrc, valid_plain, valid_lrc, duration_matches
from matti.state import atomic_json
from .http import Client
from .providers import result, lrclib, lrcmux, slug, extract_page, regional, web_fallback

STAMP = re.compile(r'\[(\d+):([0-5]\d)(?:[.:](\d{1,3}))?\]')
REVISION = 2


def read_track(path, aliases=None):
    audio = MP3(path)
    tags = audio.tags
    artists = []
    for key in ('TPE1', 'TXXX:PERFORMERS'):
        value = str(tags.get(key, ''))
        if value:
            artists.append(value)
        artists.extend(a.strip() for a in re.split(r';| & ', value) if a.strip())
    for a in list(artists):
        if a.endswith(' ולהקה'):
            artists.append(a.removesuffix(' ולהקה'))
    # Optional project aliases do not depend on curated metadata code.
    for a in list(artists):
        artists.extend(k for k, v in (aliases or {}).items() if v == a)
    artists = list(dict.fromkeys(artists))
    return {'path': path, 'title': str(tags.get('TIT2', path.stem)),
            'titles': title_variants(str(tags.get('TIT2', path.stem)), artists),
            'artists': artists, 'duration': audio.info.length,
            'album': str(tags.get('TALB', '')), 'embedded': bool(tags.getall('USLT') or tags.getall('SYLT'))}


def write_sidecar(path, text):
    """Never overwrite an existing lyric file or leave an interrupted new file."""
    created = False
    try:
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            created = True
            stream.write(text.strip() + '\n')
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise


def fetch_one(client, track, web=True):
    plain = None
    instrumental = None
    for provider in (lrclib, lrcmux, regional, web_fallback):
        if provider == web_fallback and (not web or plain or track['embedded'] or track['path'].with_suffix('.txt').exists()):
            continue
        try:
            candidates = provider(client, track)
        except Exception as error:
            with client.lock:
                name = getattr(provider, '__name__', 'provider')
                client.errors[f'{name}: {type(error).__name__}'] += 1
            continue
        for candidate in candidates:
            if candidate['synced']:
                return candidate
            if candidate['instrumental'] and duration_matches(candidate['duration'], track['duration']):
                instrumental = candidate
            text = candidate['text']
            if STAMP.search(text or ''):
                text = plain_from_lrc(text)
            if not plain and valid_plain(text):
                plain = dict(candidate, text=text)
        # Regional and web sources provide only plain text. Avoid redundant work.
        if provider == lrcmux and (plain or track['embedded'] or track['path'].with_suffix('.txt').exists()):
            break
    return plain or instrumental


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=ROOT / 'library')
    parser.add_argument('--aliases', type=Path, help='JSON artist alias mapping')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--limit', type=int, help='Process at most this many tracks without LRC')
    parser.add_argument('--retry-missing', action='store_true', help='Retry cached misses and provider responses')
    parser.add_argument('--no-web', action='store_true', help='Disable the automated web-search fallback')
    parser.add_argument('--no-embed', action='store_true', help='Save sidecars without embedding plain lyrics')
    parser.add_argument('--only-unresolved', action='store_true', help='Search only tracks without any lyrics or instrumental match')
    args = parser.parse_args(argv)
    aliases = json.loads(args.aliases.read_text(encoding='utf-8')) if args.aliases else {}
    if not isinstance(aliases, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()):
        parser.error('Aliases must map strings to strings')
    if not args.library.is_dir() or not 1 <= args.workers <= 8:
        parser.error('Library must exist; workers must be between 1 and 8')
    library = args.library.resolve()
    state_path = library / 'lyrics-state.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    client = Client(library / '.lyrics-cache', retry=args.retry_missing)
    tracks, skipped = [], Counter()
    for path in sorted(library.rglob('*.mp3')):
        if not path.resolve().is_relative_to(library):
            raise ValueError(f'MP3 resolves outside library: {path}')
        if path.with_suffix('.lrc').is_file():
            skipped['existing LRC'] += 1
            continue
        track = read_track(path, aliases)
        key = str(path.relative_to(library))
        fingerprint = hashlib.sha256(json.dumps([track['title'], track['artists'],
                                                round(track['duration'], 2), REVISION]).encode()).hexdigest()
        prior = state.get(key, {})
        if args.only_unresolved and (track['embedded'] or path.with_suffix('.txt').exists() or prior.get('status') == 'instrumental'):
            skipped['already covered'] += 1
            continue
        if (not args.retry_missing and prior.get('status') not in ('missing', 'error') and prior.get('fingerprint') == fingerprint and
                time.time() - prior.get('checked_at', 0) < 7 * 86400):
            skipped['recently checked'] += 1
            continue
        track.update(key=key, fingerprint=fingerprint)
        tracks.append(track)
    if args.limit is not None:
        tracks = tracks[:max(0, args.limit)]
    print(f'Fetching {len(tracks)} tracks; skipped {dict(skipped)}.', flush=True)
    counts = Counter()
    pool = ThreadPoolExecutor(args.workers)
    try:
        futures = {pool.submit(fetch_one, client, track, not args.no_web): track for track in tracks}
        for index, future in enumerate(as_completed(futures), 1):
            track = futures[future]
            row = {'fingerprint': track['fingerprint'], 'checked_at': time.time(),
                   'title': track['title'], 'artist': track['artists'][0], 'duration': track['duration']}
            try:
                found = future.result()
                if found:
                    row.update({k: v for k, v in found.items() if k != 'text'})
                    if found['synced']:
                        dest = track['path'].with_suffix('.lrc')
                        text = found['text']
                        row['status'] = 'synced'
                    elif valid_plain(found['text']):
                        dest = track['path'].with_suffix('.txt')
                        text = found['text']
                        row['status'] = 'plain'
                    else:
                        dest = None
                        row['status'] = 'instrumental'
                    if dest and not dest.exists():
                        if not dest.resolve().is_relative_to(library):
                            raise ValueError('Sidecar resolves outside library')
                        write_sidecar(dest, text)
                        row['saved'] = str(dest.relative_to(library))
                        row['sha256'] = hashlib.sha256(dest.read_bytes()).hexdigest()
                else:
                    row['status'] = 'existing plain' if track['embedded'] or track['path'].with_suffix('.txt').exists() else 'missing'
            except Exception as error:
                row.update(status='error', error=str(error))
            prior = state.get(track['key'], {})
            for field in ('saved', 'sha256'):
                if field not in row and field in prior:
                    row[field] = prior[field]
            state[track['key']] = row
            counts[row['status']] += 1
            atomic_json(state_path, state)
            if index % 10 == 0 or index == len(tracks):
                print(f'{index}/{len(tracks)}: {dict(counts)}; provider errors: {sum(client.errors.values())}', flush=True)
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        print('Interrupted; completed results are saved. Rerun to resume.', flush=True)
        return 130
    else:
        pool.shutdown(wait=True)
    summary = {'checked_at': datetime.now(timezone.utc).isoformat(), 'processed': dict(counts),
               'skipped': dict(skipped), 'provider_errors': dict(client.errors),
               'coverage': dict(Counter('synced' if p.with_suffix('.lrc').exists() else
                   'plain' if p.with_suffix('.txt').exists() or MP3(p).tags.getall('USLT') else
                   'instrumental' if state.get(str(p.relative_to(library)), {}).get('status') == 'instrumental' else
                   'missing' for p in library.rglob('*.mp3')))}
    atomic_json(library / 'lyrics-report.json', summary)
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)
    if not args.no_embed:
        embedded = embed_main(['--library', str(library), '--apply'])
        if embedded:
            return embedded
    return 1 if counts['error'] or (tracks and sum(counts[s] for s in ('synced', 'plain', 'instrumental')) == 0 and client.errors) else 0


if __name__ == '__main__':
    raise SystemExit(main())
