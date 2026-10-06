"""Provider adapters return validated candidates; orchestration chooses coverage."""
import json
import os
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from bs4 import BeautifulSoup
from .matching import normal, similar, identity, duration_matches, valid_lrc, valid_plain

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


