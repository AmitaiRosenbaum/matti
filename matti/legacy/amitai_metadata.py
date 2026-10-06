"""Curated cleanup of amitai-songs tags. Preview by default; --apply writes backups."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import unicodedata

from mutagen.id3 import ID3, Frames
from matti.audio import audio_hash

from matti.paths import workspace
ROOT = workspace()
LIBRARY = ROOT / 'downloads/amitai-songs'
ALIASES = {
    'Antonio Carlos Jobim': 'Antônio Carlos Jobim', 'Joao Gilberto': 'João Gilberto',
    'Vinicius de Moraes': 'Vinícius de Moraes', 'Paulinho Da Viola': 'Paulinho da Viola',
    'Shem-Tov Levi': 'שם טוב לוי', 'Shlomi Shaban': 'שלומי שבן',
    'Eviatar Banai': 'אביתר בנאי', 'Efrat Ben Zur': 'אפרת בן צור',
    'Aviv Geffen': 'אביב גפן', 'Shalom Hanoch': 'שלום חנוך', 'Yehudit Ravitz': 'יהודית רביץ',
    'Alon Eder': 'אלון עדר', 'Nadav Hollander': 'נדב הולנדר', 'Rona Kenan': 'רונה קינן',
    'Shai Tsabari': 'שי צברי', 'Berry Sakharof': 'ברי סחרוף',
    'Angela Maria': 'Ângela Maria',
}


def tidy(text):
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'[\u200e\u200f\u202a-\u202e\u2066-\u2069]', '', text)
    text = text.replace('\\', '').replace('＂', '"').replace('：', ':').replace('⧸', '/').replace('｜', '|')
    text = re.sub(r'"\s*(.*?)\s*"', lambda m: '“' + m[1].strip() + '”', text)
    return re.sub(r'\s+', ' ', text).strip()


def names(values):
    return '; '.join(dict.fromkeys(ALIASES.get(tidy(v), tidy(v)) for v in values if tidy(v)))


def prepare(row, albums):
    path = Path(row['file'])
    group = int(path.parent.name[:3])
    album_artist, album = albums[group]
    tags = row['tags']
    desc = tags.get('TXXX:description', '')
    title = tidy(tags.get('TIT2', path.stem))
    official = desc.startswith('Provided to YouTube by')
    credit = next((line for line in desc.splitlines() if ' · ' in line), '') if official else ''
    artist = album_artist
    if credit:
        artist = names(credit.split(' · ')[1:]) or album_artist
    elif official:
        artist = names(tags.get('TPE1', album_artist).split(', '))
    # Strip only identified artist prefixes/suffixes, not arbitrary hyphenated titles.
    aliases = set(album_artist.split('; ')) | {album_artist, tags.get('TPE1', '')}
    aliases |= {k for k,v in ALIASES.items() if v in album_artist}
    aliases |= {'Milton Nascimento & Lô Borges'} if group == 2 else set()
    for prefix in sorted(aliases, key=len, reverse=True):
        if prefix:
            title = re.sub(r'^' + re.escape(prefix) + r'\s*[-–—/]\s*', '', title, flags=re.I)
            title = re.sub(r'\s*[-–—/]\s*' + re.escape(prefix) + r'$', '', title, flags=re.I)
    if group in (2,4,5,48,49,50):
        title = re.sub(r'^\d{1,2}\s*[.\-]?\s+', '', title)
        title = re.sub(r'^\d{1,2}\.\-\s*', '', title)
    if group == 10:
        title = re.sub(r'^Caetano V(?:e)?loso\s*/\s*', '', title, flags=re.I)
        title = re.sub(r'\s*-\s*(?:Muito\s*-\s*)?Dentro da Estrela Azulada$', '', title, flags=re.I)
    if group in (11,24,25,26):
        # These channel titles append lyricist/composer credits.
        title = re.split(r'\s+-\s+', title, maxsplit=1)[0]
    if group in (12,13,14,16,17,18,19,22,23,27,28,29,30,31,32,33,35,36,64,65,66,67,68,69):
        prefixes = {12:['אריק איינשטיין ויהודית רביץ','אריק איינשטיין'],13:['אריק איינשטיין'],
                    14:['יוני רכטר'],16:['אלון עדר ולהקה','אלון עדר'],17:['דניאלה ספקטור'],
                    18:['דניאלה ספקטור'],19:['רונה קינן'],22:["אלג'יר"],23:['נועם רותם','נעם רותם'],
                    27:['אריק איינשטיין'],28:['אריק איינשטיין'],29:['אריק איינשטיין'],30:['אריק איינשטיין'],
                    31:['אביב גפן'],32:['אביב גפן'],33:['אביב גפן'],35:['אביתר בנאי'],36:['אביתר בנאי'],
                    64:['שלום חנוך'],65:['מאיר אריאל'],66:['יהודית רביץ'],67:['שלמה גרוניך'],68:['ברי סחרוף'],69:['שי צברי']}
        if group == 12 and 'אריק איינשטיין ויהודית רביץ' in title:
            artist = 'אריק איינשטיין; יהודית רביץ'
        for prefix in prefixes[group]:
            title = re.sub(r'^' + re.escape(prefix) + r'(?:\s*[-–—]\s*|\s+)', '', title)
            title = re.sub(r'\s*-\s*' + re.escape(prefix) + '$', '', title)
        for suffix in ['Arik Einstein','Rona Kenan']:
            title = re.split(r'\s*(?:-\s*)?' + suffix, title, maxsplit=1, flags=re.I)[0]
        if group in (14,67):
            title = re.sub('^' + re.escape(album) + r'\s*-\s*', '', title)
        title = re.sub(r'\s*-\s*קליפ(?:\s+\d{4})?$', '', title)
        if group == 69:
            title = re.sub(r'\s*-\s*בהופעה\s*//.*$', ' (בהופעה)', title)
    if group == 21 and '|' in title:
        parts = [p.strip() for p in title.split('|')]
        title = parts[-1] if len(parts) >= 3 else parts[0]
    if group == 38:
        title = re.sub(r'^CHICO BUARQUE\s+', '', title)
        title = title.capitalize() if title.isupper() else title
    if group == 42:
        title = re.sub(r'^Ron Carter\s*-\s*', '', title)
        title = re.split(r'\s*-\s*from Stone Flower', title, flags=re.I)[0]
    if group == 45:
        title = re.split(r'\s*-\s*Toquinho', title, flags=re.I)[0]
    if group == 46:
        title = re.sub(r'^Vinicius de Moraes e Toquinho\s*-\s*', '', title, flags=re.I)
    if group in (71,72,73,74):
        match = re.match(r'^(.+?)\s+-\s+(.+)$', tidy(tags.get('TIT2','')))
        if match and (match[1].startswith('João Bosco') or match[1].startswith('Tamar Eisenman')):
            artist = names(re.split(r',\s*|\s+&\s+', match[1]))
            title = match[2]
    title = re.sub(r'\s*[\[(](?:official (?:music )?(?:video|audio)|pseudo video|audio|video oficial|official lyric video|by EarpJohn)[\])]','',title,flags=re.I)
    title = re.sub(r'\s*\(from Either/Or\)', '', title, flags=re.I)
    title = re.sub(r'\s*\(Clube da Esquina\)', '', title, flags=re.I)
    if group == 5:
        title = re.split(r'\s*-\s*Getz\s*/\s*Gilberto', title, flags=re.I)[0]
    if group == 18 and 'ודאנה איבגי' in title:
        artist = 'דניאלה ספקטור; דאנה איבגי'
        title = re.sub(r'^ודאנה איבגי\s*-\s*', '', title)
    title = re.sub(r'\s*\|\s*הקליפ הרשמי$', '', title)
    if group == 21:
        title = re.sub(r'^דודו טסה\s*-\s*', '', title)
    if group == 31:
        if re.match(r'^והתעויו?ת\s*-', title):
            artist = 'אביב גפן והתעויוט'
        title = re.sub(r'^והתעויו?ט\s*-\s*|^והתעויו?ת\s*-\s*', '', title)
        title = re.sub(r'\s*-\s*(פסטיבל ערד \d{4}|הופעה \d{4})$', r' (\1)', title)
    if group == 73 and ' ft. ' in title:
        title, guest = title.split(' ft. ', 1)
        artist = names([artist, guest])
    title = tidy(title).strip(' -')
    # Stable album-level tags; track numbering follows the downloaded playlist order.
    desired = dict(TIT2=title, TPE1=artist, TPE2=album_artist, TALB=album,
                   TRCK=str(int(re.match(r'\d+', path.name)[0])))
    released = re.search(r'^Released on:\s*(\d{4}-\d{2}-\d{2})',desc,re.M)
    desired['TDRC'] = released[1] if released else None
    composers = re.findall(r'^(?:Composer|Compositor):\s*(.+)$',desc,re.M)
    lyricists = re.findall(r'^Lyricist:\s*(.+)$',desc,re.M)
    if composers: desired['TCOM'] = names(composers)
    if lyricists: desired['TEXT'] = names(lyricists)
    if not title or not artist:
        raise ValueError(f'Empty title/artist: {path}')
    return desired


def main():
    if (LIBRARY / 'playlist-folders.json').exists():
        raise SystemExit('This library has been migrated to unnumbered folders and Musicolet-compatible tags. Use repair_amitai_compatibility.py instead; this older cleanup would undo those changes.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    albums = {}
    for line in (ROOT/'amitai-albums.txt').read_text(encoding='utf-8').splitlines():
        if line and not line.startswith('#'):
            number, artist, album = line.split('|')
            albums[int(number)] = (artist, album)
    inventory = json.loads((ROOT/'amitai-metadata-inventory.json').read_text(encoding='utf-8'))
    plan = []
    for row in inventory:
        path = LIBRARY/row['file']
        tags = ID3(path)
        desired = prepare(row, albums)
        changes = {k:v for k,v in desired.items() if str(tags.get(k,'')) != (v or '')}
        plan.append(dict(file=row['file'], changes=changes))
    (ROOT/'amitai-metadata-preview.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'{len(plan)} inspected; {sum(bool(r["changes"]) for r in plan)} need changes.',flush=True)
    if not args.apply:return
    backup = ROOT/'metadata-backups'/('amitai-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True)
    results=[]
    try:
        for i,row in enumerate(plan,1):
            if not row['changes']:continue
            path=LIBRARY/row['file']; saved=backup/row['file']
            try:
                before=audio_hash(path); tags=ID3(path)
                pictures=[hashlib.sha256(f.data).hexdigest() for f in tags.getall('APIC')]
                saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,saved)
                for k,v in row['changes'].items():
                    tags.delall(k)
                    if v is not None:tags.add(Frames[k](encoding=3,text=[v]))
                try:
                    tags.save(path,v2_version=4,v1=0)
                    actual=ID3(path)
                    assert audio_hash(path)==before, 'Audio changed'
                    assert [hashlib.sha256(f.data).hexdigest() for f in actual.getall('APIC')]==pictures, 'Artwork changed'
                    assert all(str(actual.get(k,''))==(v or '') for k,v in row['changes'].items()), 'Tags differ'
                except BaseException:
                    shutil.copy2(saved,path);raise
                results.append(dict(row,status='updated',audio_sha256=before))
            except Exception as e:results.append(dict(row,status='error',error=str(e)))
            if i%50==0:print(f'{i}/{len(plan)} processed',flush=True)
    finally:
        (backup/'manifest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'Backup: {backup}',flush=True)
        print(f'Updated: {sum(r["status"]=="updated" for r in results)}; errors: {sum(r["status"]=="error" for r in results)}',flush=True)


if __name__=='__main__':main()
