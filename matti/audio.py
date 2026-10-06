"""Verification helpers for safe MP3 edits."""
import hashlib
from mutagen.id3 import ID3

def audio_hash(path):
    """Hash the MP3 payload excluding leading ID3v2 and trailing ID3v1."""
    tags = ID3(path)
    end = path.stat().st_size
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        stream.seek(-128, 2)
        if stream.read(3) == b'TAG':
            end -= 128
        stream.seek(tags.size)
        remaining = end - tags.size
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise IOError('Unexpected end of MP3')
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()

