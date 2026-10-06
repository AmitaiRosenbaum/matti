from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import requests

from fetch_lyrics import (identity, title_variants, valid_lrc, duration_matches,
                          lrcmux, lrclib, extract_page, Client, write_sidecar, fetch_one)


class FetchLyricsTests(unittest.TestCase):
    def setUp(self):
        self.track = {'path': Path('song.mp3'), 'titles': ['Coração'],
                      'artists': ['João Gilberto'], 'duration': 120, 'embedded': False}

    def test_matching_requires_artist_and_title(self):
        self.assertTrue(identity('Coracao', 'Joao Gilberto', self.track))
        self.assertFalse(identity('Coracao', 'Another singer', self.track))
        self.assertFalse(identity('Another song', 'Joao Gilberto', self.track))
        self.assertTrue(identity('Coracao', 'Joao Gilberto & Stan Getz', self.track))

    def test_title_cleanup_preserves_song_name(self):
        self.assertEqual(title_variants('Fases (Audio Oficial + Lyrics)', []), ['Fases'])
        self.assertEqual(title_variants('João Gilberto - Estate - MPB', ['João Gilberto']), ['Estate'])
        self.assertEqual(title_variants('Cupid\'s Trick', []), ["Cupid's Trick"])
        self.assertEqual(title_variants("02.Mand'ela", []), ["Mand'ela"])
        self.assertEqual(title_variants('ושלום חנוך אבשלום', ['שלום חנוך']), ['אבשלום'])

    def test_fake_timestamps_and_different_edits_are_rejected(self):
        self.assertFalse(valid_lrc('[00:00]one line\n[00:00]another line\n[00:00]third line', 120))
        self.assertFalse(valid_lrc('[00:20]one line\n[00:10]another line\n[00:30]third line', 120))
        self.assertFalse(valid_lrc('[00:10]one line\n[00:20]another line\n[04:00]third line', 120))
        self.assertTrue(valid_lrc('[00:10]one line here\n[00:20]another line here\n[00:30]third line here', 120))
        self.assertFalse(duration_matches(None, 120))
        self.assertFalse(duration_matches(150, 120))
        self.assertTrue(duration_matches(122, 120))

    def test_lrcmux_milliseconds_and_plain_fallback(self):
        client = Mock()
        data = {'track': {'title': 'Coracao', 'artist': 'Joao Gilberto', 'duration': 120},
                'meta': {'level': 'line', 'source': {'id': 'ytmusic'}},
                'lines': [{'start': 10500, 'text': 'one line here'},
                          {'start': 20000, 'text': 'another line here'},
                          {'start': 30000, 'text': 'third line here'}]}
        client.json.return_value = data, 'https://api.lrcmux.dev/get'
        found = lrcmux(client, self.track)[0]
        self.assertTrue(found['synced'])
        self.assertIn('[00:10.50]', found['text'])
        data['track']['duration'] = 150
        found = lrcmux(client, self.track)[0]
        self.assertFalse(found['synced'])
        self.assertNotIn('[00:', found['text'])

    def test_lrclib_filters_wrong_artist(self):
        client = Mock()
        client.json.return_value = [{'id': 1, 'trackName': 'Coração',
                                     'artistName': 'Different artist', 'plainLyrics': 'wrong lyrics'}], 'url'
        self.assertEqual(lrclib(client, self.track), [])

    def test_tab4u_excludes_chords_diagrams_and_navigation(self):
        track = dict(self.track, titles=['המכתב'], artists=['אביב גפן'])
        html = '<div id="songContentTPL"><div class="chords">F#m</div>'
        html += '<div class="song">F#m7/B : 222222</div>'
        html += ''.join(f'<div class="song">{s}</div>' for s in
                        ['כשתתעוררי לא תראי אותי', 'אני עובר תקופה קשה', 'שרק תכביד עלייך'])
        html += '</div><nav>Navigation</nav>'
        found = extract_page(html, 'https://www.tab4u.com/tabs/songs/219_אביב_גפן_-_המכתב.html', track)
        self.assertIsNotNone(found)
        self.assertNotIn('F#m', found['text'])
        self.assertNotIn('Navigation', found['text'])
        self.assertIsNone(extract_page(html, 'https://www.tab4u.com/tabs/songs/219_זמר_אחר_-_המכתב.html', track))

    def test_concurrent_requests_share_cache_and_use_timeouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp), delay=0)
            response = Mock(status_code=200, text='{"result": true}', url='https://example.org/lyrics')
            with patch('fetch_lyrics.requests.get', return_value=response) as get:
                with ThreadPoolExecutor(4) as pool:
                    results = list(pool.map(lambda _: client.json('https://example.org/lyrics'), range(8)))
                self.assertTrue(all(value[0] == {'result': True} for value in results))
                self.assertEqual(get.call_count, 1)
                self.assertEqual(get.call_args.kwargs['timeout'], (5, 15))

    def test_existing_sidecars_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'song.lrc'
            write_sidecar(path, 'Original lyrics')
            with self.assertRaises(FileExistsError):
                write_sidecar(path, 'Replacement lyrics')
            self.assertEqual(path.read_text(encoding='utf-8'), 'Original lyrics\n')

    def test_provider_parse_failure_uses_the_next_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            found = {'synced': True, 'text': 'synced result'}
            with patch('fetch_lyrics.lrclib', side_effect=ValueError('bad provider response')), \
                    patch('fetch_lyrics.lrcmux', return_value=[found]):
                self.assertEqual(fetch_one(client, self.track), found)
                self.assertEqual(sum(client.errors.values()), 1)

    def test_rate_limit_pauses_the_provider_without_blocking_other_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp), delay=0)
            response = Mock(status_code=429)
            response.raise_for_status.side_effect = requests.HTTPError(response=response)
            with patch('fetch_lyrics.requests.get', return_value=response) as get:
                self.assertIsNone(client.get('https://example.org/first')[0])
                self.assertIsNone(client.get('https://example.org/second')[0])
                self.assertEqual(get.call_count, 1)
                self.assertEqual(client.failures['example.org'], 5)


if __name__ == '__main__':
    unittest.main()
