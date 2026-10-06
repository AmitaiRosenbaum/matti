"""Conservative identity, duration and lyric validation."""
import re
import unicodedata
from difflib import SequenceMatcher
STAMP = re.compile(r"\[(\d+):([0-5]\d)(?:[.:](\d{1,3}))?\]")

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


