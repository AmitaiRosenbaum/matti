# matti

A local music pipeline that downloads YouTube sources, finds synced or plain lyrics,
and safely embeds plain lyrics into MP3s for players such as Musicolet.
Requires Python 3.11+, FFmpeg, and Deno or Node for YouTube extraction.

## Install and start

```powershell
python -m pip install -e .
matti init --library "D:/Music/My Library" --links songs.txt
```

Add one YouTube video or playlist URL per line to `songs.txt`, then:

```powershell
matti run --dry-run
matti run
matti status
```

Plain URLs and Markdown links are accepted. `python -m matti` works wherever the
package is installed. Config paths are relative to the TOML file, independently
of your current directory. To use another project: `matti --config project.toml run`.

## Existing Amitai library

```powershell
matti --config profiles/amitai.toml status
matti --config profiles/amitai.toml run --dry-run
matti --config profiles/amitai.toml run
```

This profile uses the existing library, source list, and curated artist aliases.
It retains `downloaded.txt`, `completed-collections.json`, `failed.txt`,
`playlist-folders.json`, `lyrics-state.json`, and `.lyrics-cache` in place.
No media files or download checkpoints need migration. Existing backups stay in place.
New lyric embedding backups are under `LIBRARY/.matti/backups`.

## Commands

| Command | Purpose |
| --- | --- |
| `matti init` | Create a project config without overwriting an existing one |
| `matti run` | Resume downloads, then fetch and embed lyrics |
| `matti download` | Download only; supports `--refresh`, `--collection`, `--dry-run` |
| `matti lyrics` | Fetch lyrics for an existing library; supports `--only-unresolved`, `--retry-missing`, `--no-web`, `--no-embed` |
| `matti embed` | Preview embedding matching TXT files; use `--apply` to write |
| `matti albums` | Preview album title/folder normalization; use `--apply` to write |
| `matti status` | Inspect lyric coverage offline |

Use `matti COMMAND --help` for stage options. `--library PATH` overrides the library
for lyrics, embedding, or status. `--output PATH` overrides download and run output.
`run --no-lyrics` disables its lyrics stage. `download --lyrics` explicitly enables
its legacy post-processing option; prefer `run` for configured workflows.

## Configuration

```toml
library = "library"
links = "songs.txt"
lyrics = true
workers = 4
web = true
# aliases = "artist-aliases.json"
# Use catalogue = "catalogue.json" instead of links for named collections.
```

Aliases are a JSON object mapping alternate artist names to canonical names.
Unknown config keys and invalid values are rejected. Each library has independent
checkpoints, cache, and SQLite stage history in `.matti/runs.sqlite3`.

## Behavior and safety

Downloads automatically standardize album names before the lyrics stage, including
when downloads are already complete. Single-album folders use `Album Artist - Album Title`;
tags contain the album title alone. Upload labels such as “Full Album” are removed,
accents and original languages are retained, and edition labels and disambiguating
years are preserved. Windows-forbidden filename characters use fullwidth equivalents
(for example `Either/Or` becomes `Either／Or` in its folder name).
Mixed-album playlists retain their separate album tags and collection folders.

To standardize an existing library without downloading or fetching lyrics:

```powershell
matti --config profiles/amitai.toml albums
matti --config profiles/amitai.toml albums --apply
```

Album changes back up affected MP3s and state files under `.matti/backups/albums-*`.
Verification protects audio, artwork, lyrics, and other tags. Failures roll back
tag writes, folder renames, and checkpoints. `playlist-folders.json` and lyric-state
paths migrate with the folders; `album-policy.json` retains canonical album names
by playlist URL so subsequent downloads use them. Internal backups are excluded
from all library scans.

Downloads reject titles containing `Unreleased`, case-insensitively. Completed
collections are skipped locally; `--refresh` checks for new playlist additions
while retaining the video archive. Interrupted or failed collections remain retryable.

Lyrics are queried automatically through LRCLIB, lrcmux, regional sources, then
web search where needed. Artist/title identity and recording duration are checked
before accepting synced lyrics; plain lyrics are retained when timings do not fit.
Providers can be unavailable or rate limited, so coverage cannot be guaranteed.
Set `BRAVE_SEARCH_API_KEY` to enable Brave search instead of the Google fallback.

Existing sidecars and embedded lyrics are preserved. Embedding writes a full MP3
backup and verifies audio, artwork, and lyrics, restoring on failure. `embed` previews
by default. Download previews, dependency checks, and simulations do not fetch lyrics.
Exit codes: 0 success, 1 stage failures, 2 configuration/input errors, 130 interruption.

## Development

```powershell
python -m unittest discover -s tests -v
```

See [architecture](docs/architecture.md) for module boundaries and extension guidance.
Previous script entry points remain compatibility shims. Project-specific cleanup
is preserved under `matti/legacy`; its rules are not applied automatically to generic
libraries. See [legacy workflows](docs/legacy-workflows.md) for those maintenance commands.
