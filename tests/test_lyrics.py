import tempfile
from pathlib import Path
import unittest

from mutagen.id3 import ID3, TIT2, APIC, USLT, SYLT
from embed_txt_lyrics import embed, main


class LyricsTests(unittest.TestCase):
    def test_metadata_repair_preserves_plain_and_synced_lyric_types(self):
        from repair_amitai_compatibility import normalize
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'song.mp3'
            path.write_bytes(b'audio' * 100)
            tags = ID3()
            tags.add(USLT(encoding=1, lang='und', desc='', text='Cafe\u0301\nשלום'))
            tags.add(SYLT(encoding=1, lang='und', format=2, type=1, desc='',
                          text=[('Cafe\u0301', 1000), ('שלום', 2000)]))
            normalize(tags)
            tags.save(path, v2_version=3)
            actual = ID3(path)
            self.assertEqual(actual.getall('USLT')[0].text, 'Café\nשלום')
            self.assertEqual(actual.getall('SYLT')[0].text, [('Café', 1000), ('שלום', 2000)])

    def test_unicode_audio_artwork_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'song.mp3'
            payload = b'fake audio payload' * 100
            path.write_bytes(payload)
            tags = ID3()
            tags.add(TIT2(encoding=1, text=['Original title']))
            tags.add(APIC(encoding=1, mime='image/jpeg', type=3, data=b'artwork'))
            tags.save(path, v2_version=3)
            original = path.read_bytes()
            backup = Path(tmp) / 'backup.mp3'
            text = 'שלום\nCoração'
            self.assertTrue(embed(path, text, backup))
            actual = ID3(path)
            self.assertEqual(actual.version, (2, 3, 0))
            self.assertEqual(actual.getall('USLT')[0].text, text)
            self.assertEqual(str(actual['TIT2']), 'Original title')
            self.assertEqual(actual.getall('APIC')[0].data, b'artwork')
            self.assertTrue(path.read_bytes().endswith(payload))
            self.assertEqual(backup.read_bytes(), original)
            self.assertFalse(embed(path, 'different', backup))
            self.assertEqual(actual.getall('USLT')[0].text, text)
            self.assertTrue(embed(path, 'replacement', backup, replace=True))
            self.assertEqual(ID3(path).getall('USLT')[0].text, 'replacement')

    def test_preview_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'song.mp3'
            path.write_bytes(b'audio' * 100)
            ID3().save(path, v2_version=3)
            path.with_suffix('.txt').write_text('plain lyrics', encoding='utf-8')
            original = path.read_bytes()
            self.assertEqual(main(['--library', tmp]), 0)
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
