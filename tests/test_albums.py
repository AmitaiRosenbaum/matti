from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from mutagen.id3 import ID3, TALB, TPE2, TIT2, APIC, USLT, SYLT
from matti.albums import album_title, folder_name, standardize
from matti.library import status


def song(folder, album='ALBUM NAME (Full Album)', artist='Artist'):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / '001 - Track.mp3'
    path.write_bytes(b'audio payload' * 200)
    tags = ID3()
    tags.add(TALB(encoding=1, text=[album]))
    tags.add(TPE2(encoding=1, text=[artist]))
    tags.add(TIT2(encoding=1, text=['Track']))
    tags.add(APIC(encoding=1, mime='image/jpeg', type=3, data=b'artwork'))
    tags.add(USLT(encoding=1, lang='und', desc='', text='Plain lyrics'))
    tags.add(SYLT(encoding=1, lang='und', format=2, type=1, desc='', text=[('Timed', 1000)]))
    tags.save(path, v2_version=3)
    path.with_suffix('.lrc').write_text('[00:01]Timed', encoding='utf-8')
    return path


class AlbumTests(unittest.TestCase):
    def test_titles_preserve_identity_and_editions(self):
        self.assertEqual(album_title('Artist - ALBUM NAME - FULL ALBUM', 'Artist'), 'Album Name')
        self.assertEqual(album_title('עיר (Deluxe Edition) (Full Album)', 'שלומי שבן'), 'עיר (Deluxe Edition)')
        self.assertEqual(album_title('מתי כספי (1974)', 'מתי כספי'), 'מתי כספי (1974)')
        self.assertEqual(album_title('Either/Or'), 'Either/Or')
        self.assertEqual(album_title('Al Final de Este Viaje...'), 'Al Final de Este Viaje…')
        self.assertEqual(folder_name('A; B', 'Either/Or'), 'A & B - Either／Or')

    def test_preview_migration_idempotency_and_backup_exclusion(self):
        with TemporaryDirectory() as tmp:
            library = Path(tmp)
            original = song(library / 'Playlists' / '001 - Messy Full Album')
            before = original.read_bytes()
            key = str(original.relative_to(library))
            mapping = {'url': 'Playlists/001 - Messy Full Album'}
            (library / 'playlist-folders.json').write_text(json.dumps(mapping))
            state = {key: {'status': 'synced', 'saved': str(original.with_suffix('.lrc').relative_to(library)), 'sha256': 'preserved'}}
            (library / 'lyrics-state.json').write_text(json.dumps(state))
            (library / 'downloaded.txt').write_text('youtube abc\n')
            standardize(library)
            self.assertEqual(before, original.read_bytes())
            self.assertEqual(mapping, json.loads((library / 'playlist-folders.json').read_text()))
            standardize(library, True)
            new = library / 'Playlists/Artist - Album Name/001 - Track.mp3'
            self.assertTrue(new.exists())
            tags = ID3(new)
            self.assertEqual(str(tags['TALB']), 'Album Name')
            self.assertEqual(tags['USLT::und'].text, 'Plain lyrics')
            self.assertEqual(tags['SYLT::und'].text, [('Timed', 1000)])
            self.assertEqual(tags.getall('APIC')[0].data, b'artwork')
            self.assertEqual(str(tags['TXXX:SOURCE_ALBUM']), 'ALBUM NAME (Full Album)')
            migrated = json.loads((library / 'lyrics-state.json').read_text())
            self.assertEqual(migrated[str(new.relative_to(library))]['sha256'], 'preserved')
            self.assertTrue((library / migrated[str(new.relative_to(library))]['saved']).exists())
            self.assertEqual((library / 'downloaded.txt').read_text(), 'youtube abc\n')
            self.assertEqual(status(library)['tracks'], 1)
            self.assertEqual(next((library / '.matti/backups').rglob('*.mp3')).read_bytes(), before)
            again = standardize(library, True)
            self.assertFalse(again['tags'])
            self.assertTrue(all(row['old'] == row['new'] for row in again['folders']))

    def test_existing_policy_applies_to_future_downloads(self):
        with TemporaryDirectory() as tmp:
            library = Path(tmp)
            path = song(library / 'Playlists/Artist - Album Name', album='Upload Title - Full Album')
            (library / 'playlist-folders.json').write_text(json.dumps({'url': 'Playlists/Artist - Album Name'}))
            (library / 'album-policy.json').write_text(json.dumps({'url': {'album': 'Album Name', 'artist': 'Artist'}}))
            standardize(library, True)
            self.assertEqual(str(ID3(path)['TALB']), 'Album Name')

    def test_first_download_saves_canonical_destination_by_url(self):
        from matti import download
        with TemporaryDirectory() as tmp:
            library = Path(tmp)
            song(library / 'Playlists/001 - Messy Full Album')
            item = {'name': 'Example', 'url': 'https://www.youtube.com/playlist?list=example',
                    'kind': 'playlist', 'input_index': 1}
            download.run_albums(library, [item])
            self.assertEqual(json.loads((library / 'playlist-folders.json').read_text())[item['url']],
                             'Playlists/Artist - Album Name')
            self.assertEqual(json.loads((library / 'album-policy.json').read_text())[item['url']]['album'], 'Album Name')
            command = download.build_command(item, library)
            self.assertIn('Artist - Album Name', command[command.index('--output') + 1])
            download.run_albums(library, [item])
            self.assertFalse((library / 'Playlists/001 - Messy Full Album').exists())

    def test_completed_download_pipeline_normalizes_before_lyrics(self):
        from matti import download
        with TemporaryDirectory() as tmp:
            library = Path(tmp) / 'library'
            original = song(library / 'Playlists/Old')
            links = Path(tmp) / 'sources.txt'
            links.write_text('https://www.youtube.com/watch?v=abcdefghijk\n')
            (library / 'completed-collections.json').write_text(json.dumps(['https://www.youtube.com/watch?v=abcdefghijk']))
            def lyrics(output):
                self.assertFalse(original.exists())
                self.assertTrue((output / 'Playlists/Artist - Album Name/001 - Track.mp3').exists())
                return 0
            with patch('matti.download.run_lyrics', side_effect=lyrics) as stage, patch('matti.download.preflight') as preflight:
                self.assertEqual(download.main(['--links', str(links), '--output', str(library), '--lyrics']), 0)
                stage.assert_called_once()
                preflight.assert_not_called()

    def test_collisions_reject_before_writing(self):
        with TemporaryDirectory() as tmp:
            library = Path(tmp)
            original = song(library / 'Playlists/Old')
            other = library / 'Playlists/Artist - Album Name'
            other.mkdir()
            before = original.read_bytes()
            with self.assertRaises(ValueError):
                standardize(library, True)
            self.assertEqual(before, original.read_bytes())
            self.assertFalse((library / '.matti').exists())

    def test_mixed_playlist_retains_distinct_albums(self):
        with TemporaryDirectory() as tmp:
            library = Path(tmp)
            first = song(library / 'Playlists/Mix', album='One')
            second = first.with_name('002 - Another.mp3')
            second.write_bytes(first.read_bytes())
            tags = ID3(second)
            tags.add(TALB(encoding=1, text=['Two']))
            tags.save(second, v2_version=3)
            result = standardize(library, True)
            self.assertFalse(result['folders'])
            self.assertEqual(str(ID3(first)['TALB']), 'One')
            self.assertEqual(str(ID3(second)['TALB']), 'Two')

    def test_failed_state_write_rolls_back_tags_folders_and_history(self):
        with TemporaryDirectory() as tmp:
            library = Path(tmp)
            original = song(library / 'Playlists/Old')
            before = original.read_bytes()
            (library / 'playlist-folders.json').write_text(json.dumps({'url': 'Playlists/Old'}))
            snapshot = (library / 'playlist-folders.json').read_bytes()
            from matti.state import atomic_json
            def fail(path, data):
                if path == library / 'playlist-folders.json':
                    raise OSError('injected failure')
                atomic_json(path, data)
            with patch('matti.albums.atomic_json', side_effect=fail):
                with self.assertRaises(OSError):
                    standardize(library, True)
            self.assertEqual(before, original.read_bytes())
            self.assertEqual(snapshot, (library / 'playlist-folders.json').read_bytes())
            self.assertTrue(original.with_suffix('.lrc').exists())
            self.assertFalse((library / 'album-policy.json').exists())
