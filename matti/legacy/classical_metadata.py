"""Preview or apply reversible ID3 cleanup without re-encoding MP3 audio."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import unicodedata

from mutagen.id3 import ID3, Frames

from matti.paths import workspace
ROOT = workspace()
COMPOSERS = {
    'Bach': 'Johann Sebastian Bach', 'Beethoven': 'Ludwig van Beethoven',
    'Brahms': 'Johannes Brahms', 'Kodaly': 'Zoltán Kodály', 'Mahler': 'Gustav Mahler',
    'Mozart': 'Wolfgang Amadeus Mozart', 'Ravel': 'Maurice Ravel',
    'Shostakovich': 'Dmitri Shostakovich', 'Verdi': 'Giuseppe Verdi', 'Ysaye': 'Eugène Ysaÿe',
}
# Overrides are based on the existing embedded titles/descriptions, not uploader names.
OVERRIDES = {
    'Mozart - Piano Concertos': dict(artist='Murray Perahia; English Chamber Orchestra'),
    'Mozart - Great Mass in C Minor K427': dict(
        title='Great Mass in C minor, K. 427', artist='Berliner Philharmoniker; Herbert von Karajan',
        conductor='Herbert von Karajan', date='1981'),
    'Mozart - Requiem': dict(title='Requiem in D minor, K. 626', artist='Unknown performers'),
    'Beethoven - Missa Solemnis': dict(
        title='Missa solemnis in D major, Op. 123',
        artist='Camilla Nylund; Birgit Remmert; Christian Elsner; René Pape; Staatskapelle Dresden; Chorus of the Sächsische Staatsoper Dresden; Fabio Luisi',
        conductor='Fabio Luisi', date='2005'),
    'Kodaly - Sonata for Solo Cello': dict(title='Sonata for Solo Cello, Op. 8', artist='János Starker', date='1957'),
    'Shostakovich - Cello Concerto': dict(title='Cello Concerto No. 1, Op. 107', artist='Mstislav Rostropovich'),
    'Mahler - Symphony No 3': dict(title='Symphony No. 3',
        artist='Nathalie Stutzmann; hr-Sinfonieorchester; Limburger Domsingknaben; Frauenchor des Europachores Frankfurt; Andrés Orozco-Estrada',
        conductor='Andrés Orozco-Estrada', date='2015'),
    'Mahler - Symphony No 5': dict(title='Symphony No. 5', artist='Berliner Philharmoniker'),
}
ALBUM_ARTISTS = {
    'Verdi - La Traviata': 'Joan Sutherland; Luciano Pavarotti; National Philharmonic Orchestra; Richard Bonynge',
    'Ravel - Chamber Music': 'Renaud Capuçon; Gautier Capuçon; Frank Braley',
    'Mahler - Symphony No 2': 'Berliner Philharmoniker; Sir Simon Rattle',
}
CONDUCTORS = {'Beethoven - Piano Concertos': 'Sir Simon Rattle',
              'Mozart - Symphonies': 'Sir Charles Mackerras',
              'Verdi - La Traviata': 'Richard Bonynge', 'Mahler - Symphony No 2': 'Sir Simon Rattle'}


def clean(text):
    text = unicodedata.normalize('NFC', text)
    for old, new in [('Ysaye', 'Ysaÿe'), ('Ysaýe', 'Ysaÿe'), ('Kodaly', 'Kodály'),
                     ('Jaqueline', 'Jacqueline'), ('du Pre', 'du Pré'), ('Capucon', 'Capuçon'),
                     ('：', ':'), ('＂', '"'), ('⧸', '/'), ('｜', '|')]:
        text = text.replace(old, new)
    text = re.sub(r'\bno\.\s*(\d)', r'No. \1', text, flags=re.I)
    text = re.sub(r'\bNo\s+(\d)', r'No. \1', text)
    return re.sub(r'\s+', ' ', text).strip()


def plan(path):
    tags = ID3(path)
    name = path.parent.name
    composer = COMPOSERS[name.split(' - ')[0]]
    description = str(tags.get('TXXX:description', ''))
    title = clean(str(tags.get('TIT2', path.stem)))
    title = re.sub(r'^(Mozart|Beethoven|Brahms|Bach|Shostakovich|Ravel|Verdi|Ysaÿe|Kodály|Mahler)\s*[:–-]\s*', '', title)
    title = re.sub(r'\s*\(Murray Perahia\)$', '', title)
    title = re.sub(r'^6 Sonatas for Violin Solo, Op\. 27 / (Sonata No\..*)', r'\1, Op. 27', title)
    title = re.sub(r'^6 Sonatas for Violin Solo, Op\. 27:\s*', '', title)
    # Put Op. 27 before the movement instead of after it.
    if name.startswith('Ysaye'):
        title = title.removesuffix(', Op. 27')
        if ', Op. 27' not in title:
            parts = title.split(':', 1)
            title = parts[0] + ', Op. 27' + (':' + parts[1] if len(parts) > 1 else '')
    raw_artists = str(tags.get('TPE1', ''))
    # Topic descriptions delimit artist names unambiguously with middle dots.
    credit = next((line for line in description.splitlines() if ' · ' in line), '')
    artists = credit.split(' · ')[1:] if credit else re.split(r',\s*|/', raw_artists)
    artists = [clean(a) for a in artists if clean(a) != composer and a.strip()]
    override = OVERRIDES.get(name, {})
    artist = override.get('artist', '; '.join(dict.fromkeys(artists)))
    title = override.get('title', title)
    album = clean(str(tags.get('TALB', ''))) or clean(name.replace(' - ', ': ', 1))
    if override.get('title'):
        album = clean(name.split(' - ')[0]) + ': ' + override['title']
    album = album.replace('Solo Violin Sonatas', 'Sonatas for Solo Violin, Op. 27')
    number = re.match(r'^(\d+) - ', path.name)
    track = str(int(number[1])) if number else '1'
    updates = dict(TIT2=title, TPE1=artist, TPE2=ALBUM_ARTISTS.get(name, artist),
                   TCOM=composer, TALB=album, TRCK=track, TCON='Classical')
    conductor = override.get('conductor', CONDUCTORS.get(name))
    if conductor:
        updates['TPE3'] = conductor
    released = re.search(r'^Released on: (\d{4}-\d{2}-\d{2})', description, re.M)
    if released:
        updates['TDRC'] = released[1]
    elif override.get('date'):
        updates['TDRC'] = override['date']
    else:
        # Unverified YouTube upload year is not a recording/release date.
        updates['TDRC'] = None
    return {k: v for k, v in updates.items() if str(tags.get(k, '')) != (v or '')}


from matti.audio import audio_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    files = sorted((ROOT / 'classical_music').glob('*/*.mp3'))
    files = [p for p in files if not p.name.endswith(('.temp.mp3', '.orig.mp3'))]
    entries = [dict(file=str(p.relative_to(ROOT / 'classical_music')), changes=plan(p)) for p in files]
    (ROOT / 'metadata-preview.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(entries)} MP3s inspected; {sum(bool(e["changes"]) for e in entries)} need changes.', flush=True)
    if not args.apply:
        return
    backup = ROOT / 'metadata-backups' / datetime.now().strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True)
    results = []
    try:
        for entry in entries:
            if not entry['changes']:
                continue
            source = ROOT / 'classical_music' / entry['file']
            saved = backup / entry['file']
            try:
                before = audio_hash(source)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, saved)
                original = ID3(source)
                artwork = [hashlib.sha256(f.data).hexdigest() for f in original.getall('APIC')]
                for key, value in entry['changes'].items():
                    original.delall(key)
                    if value is not None:
                        original.add(Frames[key](encoding=3, text=[value]))
                try:
                    original.save(source, v2_version=4, v1=0)
                    verified = ID3(source)
                    assert audio_hash(source) == before, 'Audio payload changed'
                    assert [hashlib.sha256(f.data).hexdigest() for f in verified.getall('APIC')] == artwork, 'Artwork changed'
                    for key, value in entry['changes'].items():
                        assert str(verified.get(key, '')) == (value or ''), f'Tag verification failed: {key}'
                except BaseException:
                    shutil.copy2(saved, source)
                    raise
                results.append(dict(entry, status='updated', audio_sha256=before))
            except Exception as error:
                results.append(dict(entry, status='error', error=str(error)))
            if len(results) % 25 == 0:
                print(f'{len(results)}/{len(entries)} processed', flush=True)
    finally:
        (backup / 'manifest.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Backups and manifest: {backup}', flush=True)
        print(f'Updated: {sum(r["status"] == "updated" for r in results)}; errors: {sum(r["status"] == "error" for r in results)}', flush=True)


if __name__ == '__main__':
    main()
