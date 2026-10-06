from contextlib import redirect_stdout
from contextlib import closing
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from matti.cli import main, execute
from matti.config import load, Project
from matti.library import status


class MattiTests(unittest.TestCase):
    def test_library_writer_lock_rejects_overlap_and_releases(self):
        from matti.ledger import library_lock
        with tempfile.TemporaryDirectory() as tmp:
            with library_lock(Path(tmp)):
                with self.assertRaises(ValueError):
                    with library_lock(Path(tmp)):
                        self.fail('Concurrent writer was allowed')
            with library_lock(Path(tmp)):
                pass

    def test_config_paths_relative_to_config_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'project.toml'
            config.write_text('library="audio"\nlinks="songs.txt"\nlyrics=false\n')
            value = load(config)
            self.assertEqual(value.library, Path(tmp) / 'audio')
            self.assertEqual(value.links, Path(tmp) / 'songs.txt')
            self.assertFalse(value.lyrics)
            for content in ('workers=0', 'workers=true', 'web="yes"', 'typo=true', 'links="a"\ncatalogue="b"'):
                config.write_text(content)
                with self.assertRaises(ValueError):
                    load(config)

    def test_init_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            path = Path(tmp) / 'matti.toml'
            self.assertEqual(main(['init', '--name', str(path)]), 0)
            before = path.read_bytes()
            self.assertEqual(main(['init', '--name', str(path)]), 2)
            self.assertEqual(path.read_bytes(), before)

    def test_run_uses_same_output_and_records_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / 'override'
            config = Project(Path(tmp) / 'original', links=Path(tmp) / 'songs.txt')
            with patch('matti.download.main', return_value=0) as download, patch('matti.lyrics.pipeline.main', return_value=0) as lyrics:
                self.assertEqual(execute('run', config, ['--output', str(library)]), 0)
            self.assertIn(str(library), download.call_args.args[0])
            self.assertIn(str(library), lyrics.call_args.args[0])
            with closing(sqlite3.connect(library / '.matti/runs.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT stage, exit_code FROM runs ORDER BY id').fetchall(), [('download', 0), ('lyrics', 0)])
            self.assertFalse(config.library.exists())

    def test_preview_has_no_lyrics_or_state_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / 'audio'
            config = Project(library, links=Path(tmp) / 'songs.txt')
            with patch('matti.download.main', return_value=0), patch('matti.lyrics.pipeline.main') as lyrics:
                self.assertEqual(execute('run', config, ['--dry-run']), 0)
                lyrics.assert_not_called()
            self.assertFalse(library.exists())

    def test_missing_lyrics_library_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / 'absent'
            with self.assertRaises(ValueError):
                execute('lyrics', Project(library), [])
            self.assertFalse(library.exists())

    def test_status_is_offline_and_preserves_library(self):
        from mutagen.id3 import ID3, TIT2, USLT
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp)
            for name in ('synced', 'plain', 'missing'):
                song = library / (name + '.mp3')
                song.write_bytes(b'audio' * 100)
                tags = ID3()
                tags.add(TIT2(encoding=1, text=[name]))
                if name == 'plain':
                    tags.add(USLT(encoding=1, lang='und', text='lyrics'))
                tags.save(song)
            (library / 'synced.lrc').write_text('[00:01]lyrics')
            before = {p.name: p.read_bytes() for p in library.iterdir()}
            self.assertEqual(status(library)['coverage'], {'missing': 1, 'plain': 1, 'synced': 1})
            self.assertEqual(before, {p.name: p.read_bytes() for p in library.iterdir()})
