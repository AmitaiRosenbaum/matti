import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import music


class DownloaderTests(unittest.TestCase):
    def test_renamed_playlist_folder_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            item = dict(kind='playlist', input_index=1, name='Example', url='https://www.youtube.com/playlist?list=example')
            mapping = output / 'playlist-folders.json'
            mapping.write_text(json.dumps({item['url']: 'Playlists/An Album'}), encoding='utf-8')
            command = music.build_command(item, output)
            self.assertEqual(command[command.index('--output') + 1],
                             str((output / 'Playlists/An Album').resolve()) + '/%(playlist_index)03d - %(title).120B.%(ext)s')
            mapping.write_text(json.dumps({item['url']: '../escape'}), encoding='utf-8')
            with self.assertRaises(ValueError):
                music.build_command(item, output)

    def setUp(self):
        self.item = music.load_catalogue(music.ROOT / "catalogue.json")[0]
        self.logger = Mock(spec=logging.Logger)

    def test_catalogue_has_all_18_collections(self):
        items = music.load_catalogue(music.ROOT / "catalogue.json")
        self.assertEqual(len(items), 18)
        self.assertEqual(sum('/playlist?' in i['url'] for i in items), 11)

    def test_new_batch_and_selected_playlist_songs(self):
        items = music.load_links(music.ROOT / 'new-songs.txt')
        self.assertEqual(len(items), 26)
        self.assertEqual(sum(i['kind'] == 'playlist' for i in items), 3)
        self.assertEqual([i['url'].split('v=')[1] for i in items[-4:]],
                         ['YuBeBjqKSGQ', 'noHQXogDsA0', 'gMY3Ou9L5xE', 'Sf-tjXevlyQ'])
        import yt_dlp
        for item in items:
            command = music.build_command(item, Path('new-output'))
            options = yt_dlp.parse_options(command[3:]).ydl_opts
            self.assertEqual(options['noplaylist'], item['kind'] == 'video')
            self.assertIn('--match-filter', command)
            with yt_dlp.YoutubeDL(options) as downloader:
                filename = downloader.prepare_filename(dict(id='abcdefghijk', title='A Song', ext='mp3',
                    playlist_title='An Album', playlist_index=2))
                self.assertNotIn('abcdefghijk', filename)
                self.assertIn('Songs' if item['kind'] == 'video' else 'An Album', filename)

    def test_pasted_markdown_escapes_and_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'links.txt'
            path.write_text('# comment\n\n[Song](https://www.youtube.com/watch?v=oIv\\_Y2RPQ\\_A\\&list=example)\n'
                            'youtube.com/watch?v=oIv_Y2RPQ_A\n'
                            'https://youtu.be/3sur4BmjQt8\n', encoding='utf-8')
            items = music.load_links(path)
            self.assertEqual(len(items), 2)
            self.assertTrue(all(i['kind'] == 'video' for i in items))

    def test_invalid_links_do_not_start_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'links.txt'
            for value in ['https://evil.example/watch?v=3sur4BmjQt8',
                          'https://youtube.com/@channel', 'https://youtube.com/watch?v=bad', '# empty']:
                path.write_text(value)
                with self.assertRaises(ValueError):
                    music.load_links(path)

    def test_links_default_output_is_separate_from_classical(self):
        with patch('music.preflight', return_value=(None, None)), \
                patch('music.load_links', return_value=[dict(self.item, kind='video', input_index=1)]), \
                patch('music.ROOT', Path(tempfile.gettempdir()) / 'music-tests'), \
                patch('music.download', return_value=True) as download:
            with tempfile.TemporaryDirectory() as tmp, patch('music.ROOT', Path(tmp)):
                self.assertEqual(music.main(['--links', 'new-songs.txt']), 0)
                command = download.call_args.args[1]
                self.assertEqual(command[command.index('--download-archive') + 1],
                                 str(Path(tmp) / 'downloads/new-songs/downloaded.txt'))

    def test_nonzero_exit_moves_on_without_retry_or_backoff(self):
        runner = Mock(return_value=1)
        with patch('music.time.sleep') as sleep:
            self.assertFalse(music.download(self.item, [], self.logger, runner))
        runner.assert_called_once_with([], self.logger)
        sleep.assert_not_called()
        self.assertIn('Moving on', self.logger.warning.call_args.args[0])

    def test_pending_skips_completed_but_keeps_failed_and_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            items = music.load_links(music.ROOT / 'new-songs.txt')[:6]
            completed = {items[0]['url'], items[4]['url']}
            music.save_completed(output, completed)
            (output / 'downloaded.txt').write_text('youtube oIv_Y2RPQ_A\nyoutube JN_Vyg_wURY\n')
            music.update_failures(output / 'failed.txt', items[2], True)
            pending = music.pending_collections(items, output, music.completed_collections(output))
            self.assertEqual(pending, [items[2], items[3], items[5]])

    def test_successful_rerun_has_no_process_or_sleep(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            music.save_completed(output, {self.item['url']})
            with patch('music.load_catalogue', return_value=[self.item]), \
                    patch('music.download') as download, patch('music.time.sleep') as sleep:
                self.assertEqual(music.main(['--output', tmp]), 0)
                download.assert_not_called()
                sleep.assert_not_called()

    def test_success_runs_once(self):
        runner = Mock(return_value=0)
        self.assertTrue(music.download(self.item, [], self.logger, runner))
        runner.assert_called_once()

    def test_launch_error_moves_on(self):
        runner = Mock(side_effect=OSError('Access denied'))
        self.assertFalse(music.download(self.item, [], self.logger, runner))
        runner.assert_called_once()

    def test_interrupt_never_retries(self):
        runner = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            music.download(self.item, [], self.logger, runner)
        self.assertEqual(runner.call_count, 1)

    def test_run_continues_after_error_and_preserves_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'downloaded.txt'
            archive.write_text('youtube completed-video\n')
            with patch('music.preflight', return_value=(None, None)), \
                    patch('music.load_catalogue', return_value=[self.item, dict(self.item, name='Second')]), \
                    patch('music.download', side_effect=[False, True]) as download, \
                    patch('music.time.sleep') as sleep, \
                    patch('music.random.randint', return_value=20):
                self.assertEqual(music.main(['--output', tmp]), 1)
            self.assertEqual(download.call_count, 2)
            sleep.assert_called_once_with(20)
            self.assertEqual(archive.read_text(), 'youtube completed-video\n')
            self.assertIn('1 collections completed with one or more errors',
                          (Path(tmp) / 'download.log').read_text())

    def test_failure_report_deduplicates_and_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'failed.txt'
            other = dict(name='Other', url='https://youtu.be/example')
            for item in [self.item, self.item, other]:
                music.update_failures(path, item, True)
            self.assertEqual(len(path.read_text().splitlines()), 2)
            music.update_failures(path, self.item, False)
            self.assertEqual(path.read_text(), 'Other\thttps://youtu.be/example\n')

    def test_rejects_folder_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'catalogue.json'
            path.write_text(json.dumps([dict(self.item, name='../escape')]))
            with self.assertRaises(ValueError):
                music.load_catalogue(path)

    def test_ytdlp_options_and_output_names(self):
        import yt_dlp
        command = music.build_command(self.item, Path('output'))
        parsed = yt_dlp.parse_options(command[3:])
        self.assertIs(parsed.ydl_opts['ignoreerrors'], True)
        self.assertIn('--no-post-overwrites', command)
        self.assertIn('--continue', command)
        self.assertIn('--embed-metadata', command)
        self.assertIn('--embed-thumbnail', command)
        with yt_dlp.YoutubeDL(parsed.ydl_opts) as downloader:
            info = dict(id='video123', title='Allegro', ext='mp3', playlist_index=2)
            self.assertIn('002 - Allegro.mp3', downloader.prepare_filename(info))
            info.pop('playlist_index')
            self.assertTrue(downloader.prepare_filename(info).endswith('Allegro.mp3'))
            match = parsed.ydl_opts['match_filter']
            self.assertIsNotNone(match(dict(title='UNRELEASED recording')))
            self.assertIsNone(match(dict(title='Allegro')))

    def test_unreleased_filter_for_link_downloads(self):
        import yt_dlp
        for kind in ('playlist', 'video'):
            item = dict(self.item, kind=kind, input_index=1)
            parsed = yt_dlp.parse_options(music.build_command(item, Path('output'))[3:])
            match = parsed.ydl_opts['match_filter']
            self.assertIsNotNone(match(dict(title='UnReLeAsEd spam')))
            self.assertIsNone(match(dict(title='Real song')))

    def test_no_ffmpeg_startup_execution(self):
        with patch('music.importlib.util.find_spec', return_value=object()), \
                patch('music.shutil.which', return_value='node'), \
                patch('music.subprocess.run') as run:
            self.assertEqual(music.preflight('custom/bin'), ('custom/bin', 'deno'))
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
