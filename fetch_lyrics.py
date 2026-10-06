"""Fetch synced LRC and plain-text lyrics unattended, then embed plain lyrics."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import unicodedata
import uuid
from urllib.parse import parse_qs, unquote, urljoin, urlparse

ROOT = Path(__file__).resolve().parent
if (ROOT / '.lyrics-deps').is_dir():
    sys.path.insert(0, str(ROOT / '.lyrics-deps'))

import requests
from bs4 import BeautifulSoup
from mutagen.mp3 import MP3
from embed_txt_lyrics import main as embed_main

STAMP = re.compile(r'\[(\d+):([0-5]\d)(?:[.:](\d{1,3}))?\]')
REVISION = 2


def normal(text):
    text = unicodedata.normalize('NFKD', text).casefold()
    text = ''.join(c for c in text if not unicodedata.combining(c))
    # Apostrophes are not word separators (Algiers, Cupid's Trick, etc.).
    text = re.sub(r"['’‘\"״׳]", '', text)
    return ' '.join(re.sub(r'[^\w\s]', ' ', text).split())


def similar(a, b):
    a, b = normal(a), normal(b)
    return bool(a and b) and (a == b or SequenceMatcher(None, a, b).ratio() >= .90)


def identity(title, artist, track):
    credits = [artist] + re.split(r'\s*(?:;|,| & | feat\.? | featuring | and | e )\s*', artist, flags=re.I)
    return (any(similar(title, t) for t in track['titles']) and
            any(similar(credit, a) for credit in credits for a in track['artists']))


def title_variants(title, artists):
    title = re.sub(r'^\s*\d+[.\-]\s*|^\s*\d+\s+', '', title).strip(' “”—-')
    title = re.sub(r'^A FLG Maurepas upload\s*-\s*', '', title, flags=re.I)
    for artist in sorted(artists, key=len, reverse=True):
        title = re.sub(r'^(?:&\s*|ו)' + re.escape(artist) + r'\s+', '', title, flags=re.I)
        title = re.sub(r'^' + re.escape(artist) + r'\s*[-–—]\s*', '', title, flags=re.I)
        title = re.sub(r'\s*[-–—]\s*' + re.escape(artist) + r'\s*$', '', title, flags=re.I)
    title = re.sub(r'\s*[|]\s*(?:קליפ|official.*|lyrics.*).*$', '', title, flags=re.I)
    title = re.sub(r'\s*[-–—]\s*MPB\s*$', '', title, flags=re.I)
    title = re.sub(r'\s*\([^)]*(?:remaster|official|oficial|audio|lyrics|HD|vinyl|RPM|קליפ)[^)]*\)', '', title, flags=re.I)
    title = re.sub(r'\s+Original Y2K\s+\d{4}.*$', '', title, flags=re.I)
    result = [title.strip(' “”—-')]
    base = re.sub(r'\s*\([^)]*\)', '', result[0]).strip()
    if base and base != result[0]:
        result.append(base)
    return list(dict.fromkeys(result))


def read_track(path):
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
    # These aliases are already curated by the existing metadata cleanup.
    from clean_amitai_metadata import ALIASES
    for a in list(artists):
        artists.extend(k for k, v in ALIASES.items() if v == a)
    artists = list(dict.fromkeys(artists))
    return {'path': path, 'title': str(tags.get('TIT2', path.stem)),
            'titles': title_variants(str(tags.get('TIT2', path.stem)), artists),
            'artists': artists, 'duration': audio.info.length,
            'album': str(tags.get('TALB', '')), 'embedded': bool(tags.getall('USLT') or tags.getall('SYLT'))}


def plain_from_lrc(text):
    return '\n'.join(STAMP.sub('', line).strip() for line in text.splitlines()
                     if STAMP.search(line)).strip()


def valid_plain(text):
    return (isinstance(text, str) and len(text.strip()) >= 30 and
            len([line for line in text.splitlines() if line.strip()]) >= 3 and
            '\ufffd' not in text and '<html' not in text.casefold())


def valid_lrc(text, duration):
    if not isinstance(text, str) or not valid_plain(plain_from_lrc(text)):
        return False
    values = [int(m[1]) * 60 + int(m[2]) + float('0.' + (m[3] or '0')) for m in STAMP.finditer(text)]
    return (len(values) >= 3 and len(set(values)) >= 3 and min(values) >= 0 and
            max(values) <= duration + 3 and values == sorted(values))


def duration_matches(candidate, actual):
    return candidate is not None and abs(float(candidate) - actual) <= max(3, actual * .02)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        for attempt in range(6):
            try:
                temp.replace(path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.05 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


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


class Client:
    def __init__(self, cache, timeout=15, delay=.5, retry=False):
        self.cache, self.timeout, self.delay, self.retry = cache, timeout, delay, retry
        self.lock = threading.Lock()
        self.next_request = {}
        self.failures = Counter()
        self.errors = Counter()
        self.key_locks = {}
        self.last_failure = {}

    def get(self, url, params=None, headers=None):
        key = hashlib.sha256(json.dumps([url, params, headers], sort_keys=True).encode()).hexdigest()
        with self.lock:
            lock = self.key_locks.setdefault(key, threading.Lock())
        with lock:
            return self._get(url, params, headers)

    def _get(self, url, params=None, headers=None):
        key = hashlib.sha256(json.dumps([url, params, headers], sort_keys=True).encode()).hexdigest()
        saved = self.cache / (key + '.json')
        if saved.exists() and not self.retry:
            try:
                data = json.loads(saved.read_text(encoding='utf-8'))
                if time.time() - data['time'] < 30 * 86400:
                    return data['body'], data['url']
            except (ValueError, OSError, KeyError):
                pass
        host = urlparse(url).hostname
        with self.lock:
            if self.failures[host] >= 5:
                if time.monotonic() - self.last_failure.get(host, 0) < 60:
                    self.errors[f'{host}: temporarily paused'] += 1
                    return None, url
                self.failures[host] = 0
            wait = max(0, self.next_request.get(host, 0) - time.monotonic())
            self.next_request[host] = time.monotonic() + wait + self.delay
        if wait:
            time.sleep(wait)
        try:
            response = requests.get(url, params=params, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; LocalMusicLyrics/1.0)', **(headers or {})},
                timeout=(5, self.timeout))
            if response.status_code in (404, 204):
                body = None
            else:
                response.raise_for_status()
                response.encoding = 'utf-8'
                body = response.text
            with self.lock:
                self.failures[host] = 0
            atomic_json(saved, {'time': time.time(), 'url': response.url, 'body': body})
            return body, response.url
        except requests.RequestException as error:
            with self.lock:
                self.failures[host] += 1
                self.last_failure[host] = time.monotonic()
                status = error.response.status_code if error.response is not None else None
                self.errors[f'{host}: {type(error).__name__}' + (f' {status}' if status else '')] += 1
                if status == 429:
                    # Back off the whole host instead of retrying each song.
                    self.failures[host] = 5
                    self.next_request[host] = max(self.next_request.get(host, 0), time.monotonic() + 60)
            return None, url

    def json(self, url, params=None):
        body, source = self.get(url, params)
        if not body:
            return None, source
        try:
            return json.loads(body), source
        except ValueError:
            return None, source


def result(text, source, provider, track, duration=None, instrumental=False):
    return {'text': text, 'source': source, 'provider': provider,
            'duration': duration, 'instrumental': instrumental,
            'synced': duration_matches(duration, track['duration']) and valid_lrc(text, track['duration'])}


def lrclib(client, track):
    results = []
    def collect(data):
        for row in data if isinstance(data, list) else []:
            if not identity(row.get('trackName', ''), row.get('artistName', ''), track):
                continue
            text = row.get('syncedLyrics') or row.get('plainLyrics') or ''
            candidate = result(text, f'https://lrclib.net/api/get/{row["id"]}', 'lrclib', track,
                               row.get('duration'), row.get('instrumental', False))
            if not candidate['synced'] and row.get('plainLyrics'):
                candidate['text'] = row['plainLyrics']
            results.append(candidate)

    for title in track['titles'][:2]:
        for artist in track['artists'][:2]:
            data, url = client.json('https://lrclib.net/api/search', {'track_name': title, 'artist_name': artist})
            collect(data)
            if any(r['synced'] for r in results):
                return [r for r in results if r['synced']]
        if not results:
            data, url = client.json('https://lrclib.net/api/search', {'track_name': title})
            collect(data)
    return sorted(results, key=lambda r: (not r['synced'], abs((r['duration'] or 0) - track['duration'])))


def lrcmux(client, track):
    results = []
    # The service searches several upstreams, including YT Music and Musixmatch.
    for artist in track['artists'][:2]:
        data, url = client.json('https://api.lrcmux.dev/get', {
            'artist': artist, 'title': track['titles'][0], 'duration': round(track['duration'])})
        if not isinstance(data, dict):
            continue
        found, meta = data.get('track', {}), data.get('meta', {})
        if not identity(found.get('title', ''), found.get('artist', ''), track):
            continue
        lines = data.get('lines') or []
        plain = '\n'.join(line.get('text', '') for line in lines)
        timed = []
        for line in lines:
            if line.get('start') is None:
                continue
            cs = round(float(line['start']) / 10)
            timed.append(f'[{cs // 6000:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}]' + line.get('text', ''))
        text = '\n'.join(timed) if meta.get('level') in ('line', 'word') else plain
        provider = 'lrcmux/' + meta.get('source', {}).get('id', 'unknown')
        candidate = result(text, url, provider, track, found.get('duration'), meta.get('instrumental', False))
        if not candidate['synced']:
            candidate['text'] = plain
        results.append(candidate)
        if candidate['synced']:
            break
    return results


def slug(text):
    return normal(text).replace(' ', '-')


def extract_page(html, url, track):
    soup = BeautifulSoup(html, 'html.parser')
    host = urlparse(url).hostname or ''
    title_text = soup.title.get_text(' ', strip=True) if soup.title else ''
    if host.endswith('vagalume.com.br'):
        title = soup.select_one('#lyricContent h1')
        artist = soup.select_one('#lyricContent h2')
        if not title or not artist or not identity(title.get_text(), artist.get_text(), track):
            return None
        node = soup.select_one('#lyrics')
        text = node.get_text('\n', strip=True) if node else ''
    elif host.endswith('tab4u.com'):
        decoded = unquote(urlparse(url).path).replace('_', ' ')
        match = re.search(r'/tabs/songs/(?:\d+) (.*?) - (.*?)\.html', decoded)
        if not match or not identity(match[2], match[1], track):
            return None
        # .song contains lyric rows and guitar diagrams. Keep Hebrew rows only;
        # chord rows use separate .chords elements and are never copied.
        text = '\n'.join(' '.join(n.get_text(' ', strip=True).split())
                         for n in soup.select('#songContentTPL .song')
                         if re.search(r'[\u05d0-\u05ea]', n.get_text()))
    else:
        # Search results must name the title and artist before extraction.
        heading = normal(title_text)
        if not (any(normal(t) in heading for t in track['titles']) and
                any(normal(a) in heading for a in track['artists'])):
            return None
        selectors = {
            'genius.com': '[data-lyrics-container="true"]',
            'letras.mus.br': '.lyric-original',
            'shironet.mako.co.il': '.artist_lyrics_text',
            'mshironet.mako.co.il': '.artist_lyrics_text',
        }
        selector = selectors.get(host.removeprefix('www.'))
        if not selector:
            return None
        text = '\n'.join(n.get_text('\n', strip=True) for n in soup.select(selector))
    if valid_plain(text):
        return result(text, url, host, track)
    return None


def regional(client, track):
    results = []
    if any(re.search(r'[\u05d0-\u05ea]', a) for a in track['artists']):
        for title in track['titles'][:2]:
            body, url = client.get('https://www.tab4u.com/resultsSimple', {'tab': 'songs', 'q': title})
            if not body:
                continue
            soup = BeautifulSoup(body, 'html.parser')
            urls = list(dict.fromkeys(urljoin(url, a['href']) for a in soup.select('a[href*="tabs/songs/"]')))
            for link in urls:
                decoded = unquote(urlparse(link).path).replace('_', ' ')
                match = re.search(r'/tabs/songs/(?:\d+) (.*?) - (.*?)\.html', decoded)
                if not match or not identity(match[2], match[1], track):
                    continue
                html, source = client.get(link)
                found = extract_page(html, source, track) if html else None
                if found:
                    return [found]
    else:
        for artist in track['artists'][:3]:
            url = f'https://www.vagalume.com.br/{slug(artist)}/'
            body, source = client.get(url)
            if not body:
                continue
            soup = BeautifulSoup(body, 'html.parser')
            for a in soup.select('a[href*=".html"]'):
                if not any(similar(a.get_text(' ', strip=True), t) for t in track['titles']):
                    continue
                link = urljoin(source, a['href'].split('#')[0])
                if urlparse(link).hostname != 'www.vagalume.com.br':
                    continue
                html, link = client.get(link)
                found = extract_page(html, link, track) if html else None
                if found:
                    return [found]
    return results


def web_fallback(client, track):
    query = f'"{track["artists"][0]}" "{track["titles"][0]}" lyrics'
    links = []
    key = os.environ.get('BRAVE_SEARCH_API_KEY')
    if key:
        body, _ = client.get('https://api.search.brave.com/res/v1/web/search', {'q': query, 'count': 8},
                             {'X-Subscription-Token': key})
        if body:
            links = [r['url'] for r in json.loads(body).get('web', {}).get('results', [])]
    else:
        body, url = client.get('https://www.google.com/search', {'q': query, 'gbv': '1'})
        if body:
            for a in BeautifulSoup(body, 'html.parser').find_all('a', href=True):
                link = a['href']
                if link.startswith('/url?'):
                    query_params = parse_qs(urlparse(link).query)
                    link = query_params.get('q', query_params.get('url', ['']))[0]
                if link.startswith('https://'):
                    links.append(link)
    allowed = {'genius.com', 'www.genius.com', 'www.letras.mus.br', 'www.vagalume.com.br',
               'shironet.mako.co.il', 'mshironet.mako.co.il', 'www.tab4u.com'}
    for link in list(dict.fromkeys(links))[:20]:
        if urlparse(link).hostname not in allowed:
            continue
        body, source = client.get(link)
        found = extract_page(body, source, track) if body else None
        if found:
            return [found]
    return []


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
    parser.add_argument('--library', type=Path, default=ROOT / 'downloads/amitai-songs')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--limit', type=int, help='Process at most this many tracks without LRC')
    parser.add_argument('--retry-missing', action='store_true', help='Retry cached misses and provider responses')
    parser.add_argument('--no-web', action='store_true', help='Disable the automated web-search fallback')
    parser.add_argument('--no-embed', action='store_true', help='Save sidecars without embedding plain lyrics')
    parser.add_argument('--only-unresolved', action='store_true', help='Search only tracks without any lyrics or instrumental match')
    args = parser.parse_args(argv)
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
        track = read_track(path)
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
