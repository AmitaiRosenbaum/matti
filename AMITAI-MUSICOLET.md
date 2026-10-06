# Musicolet compatibility repair

The local library contained 850 MP3 files in 75 album folders. The title,
artist and album strings decoded correctly locally, including accented Latin
letters and Hebrew. Tags used ID3v2.4 with mixed UTF-8, UTF-16 and Latin-1
frame encodings. Album and album-artist values were consistent within each
folder, but track artists and the presence of release dates varied.

The phone's scanner/cache cannot be inspected from this computer. This repair
addresses plausible encoding and grouping compatibility problems; the previous
ID3v2.4 tags were not inherently invalid.

Applied changes:

- Write ID3v2.3 tags with UTF-16 Unicode text and NFC normalization.
- Use the same artist and album artist across each album. Where the track's
  artist differed, retain that credit in `TXXX:PERFORMERS` (some players do not
  display custom tags).
- Use the album's existing most common source release date consistently where
  available. This is not a researched original-release date. Preserve any
  differing nonempty date in `TXXX:SOURCE_RELEASE_DATE`.
- Remove numeric prefixes from album folders only; retain track filenames and
  track numbers.
- Preserve audio and artwork, checking their hashes for every file.
- Leave download archives and completion records intact. `playlist-folders.json`
  maps playlist URLs to the renamed folders for future downloader runs.

Original files and the rename manifest are in `metadata-backups/musicolet-*`.
The older `clean_amitai_metadata.py` is guarded against undoing this migration.
`repair_amitai_compatibility.py` previews by default; `--apply` writes backups and
applies the compatibility policy. Newly downloaded tracks can be processed by
running it again; it currently backs up and checks the whole library.

Copy the repaired `downloads/amitai-songs/Playlists` to the phone, replacing the
old library rather than leaving both numbered and unnumbered copies. Then run
a full Musicolet scan/reload of metadata, including existing songs. If characters
still display incorrectly, check the preferred scanner setting and try the
Musicolet scanner. Exact menu labels depend on the installed version.

Encoding reference: https://mutagen.readthedocs.io/en/latest/user/id3.html
