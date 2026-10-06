# Classical music downloader

## New projects: paste links, no JSON needed

Reruns now skip successful collections locally, without launching yt-dlp or
waiting between skipped entries. Completion is stored by URL in
`completed-collections.json` beside the video archive. Failed, interrupted and
new entries remain eligible. Single videos already in the archive are also
skipped locally unless listed in `failed.txt`. A failed playlist still uses
the video archive to skip its already downloaded tracks.

To explicitly check successful playlists for newly added songs, use
`python music.py --links new-songs.txt --refresh`. This retains the video archive.
`--dry-run` displays only pending work; `--list` shows the whole input.

Your new batch is already saved in `new-songs.txt`. Start it with:

```powershell
python music.py --links new-songs.txt
```

This reads 23 individual video links and 3 full playlists. The four video links
that include playlist context download only the specified videos, not the entire
playlist. Nothing starts until you run the command.

For another project, create a text file such as `my-songs.txt` and paste one link
per line. Plain URLs and `[label](https://...)` Markdown links are both accepted,
including the escaped underscores/ampersands sometimes copied from chat.
Blank lines and lines starting with `#` are ignored; duplicate links are removed.

```text
https://www.youtube.com/watch?v=3sur4BmjQt8
https://www.youtube.com/playlist?list=PL1dfVf_Yq5rGGTKx4Fi5PwX5TfPBXNP8p
[Selected song](https://www.youtube.com/watch?v=YuBeBjqKSGQ&list=PLOwSo8kHs4XT4Hx7OufxbXJLestNUWg7F&index=1)
```

```powershell
python music.py --links my-songs.txt
python music.py --links my-songs.txt --list
python music.py --links my-songs.txt --output "D:\Music\My Project"
```

Each links file defaults to a separate `downloads/<filename-without-extension>/`
folder, containing `Songs/`, named playlist folders under `Playlists/`, and its
own `downloaded.txt`, `download.log`, and `failed.txt`. Filenames have numeric
prefixes for ordering and no video IDs. The classical library stays separate.
Keep the same input filename and output folder when resuming, and append new
links rather than reordering existing ones to keep numbering stable. Use distinct
text-file names or explicit output folders for separate projects.

The same MP3 quality, embedded artwork/metadata, conservative request delays,
yt-dlp retries and Ctrl+C handling apply. There are no outer playlist retries.
Unlike the old classical catalogue, plain link batches do not automatically
exclude titles containing “Unreleased”. A video already archived in this project
is skipped even if it appears in another supplied playlist. The classical-specific
metadata cleanup scripts are not applied to new projects.

## Original classical project

Implements the final downloader from the shared conversation, with all 18 supplied
YouTube collections in `catalogue.json`. No Zehu Ze material is included.

## Run

```powershell
python -m pip install -U -r requirements.txt
python music.py
```

FFmpeg must already be available to yt-dlp. The script does not check or execute
FFmpeg during startup. If necessary, supply its location yourself:

```powershell
python music.py --ffmpeg-location "C:\path\to\ffmpeg\bin"
```

YouTube extraction also needs a supported JavaScript runtime (Deno or Node).
The script selects an installed runtime. Connect your VPN manually before running
if you need it; the script does not control or rotate VPN connections.

## Behaviour

- Handles playlists and individual videos, downloading sequentially to MP3.
- Skips titles containing `Unreleased`, regardless of case.
- Keeps source metadata and cover art; playlist positions become track tags and
  padded filename prefixes. Video IDs remain in source-link tags and the archive,
  but are omitted from filenames.
- Creates one folder per collection inside `classical_music/`, next to the script.
- Uses `downloaded.txt` to skip completed video IDs across all collections and
  resumes partial downloads where supported. Keep this file when restarting.
- Waits 2 seconds between extraction requests, 6–12 seconds before downloads,
  and 15–30 seconds between collections, with one fragment at a time.
- Retains yt-dlp's internal retries for transient download/network problems. Each
  collection is invoked only once per run; there is no Python retry/backoff loop.
- Uses `--ignore-errors` to continue past problematic playlist items, and
  `--no-post-overwrites` to avoid overwriting existing post-processing outputs
  where the post-processor supports it. Metadata and artwork embedding remain enabled.
- Logs downloader output and timestamps to `download.log`. Nonzero exits are
  reported as collections completed with errors, recorded in `failed.txt`, and
  followed by the next collection. A later zero exit removes that error entry.

Press **Ctrl+C** to stop, then run the same command to resume. The script never
clears or recreates `downloaded.txt`; archived videos are skipped even if they
occur in another collection. Unarchived incomplete items are attempted on a
future run, subject to the existing overwrite settings. A single long video
stays one MP3 rather than being split into movements.

Resumability caveat: `--ignore-errors` can allow a video to be archived despite
an ignored post-processing error. Such a video will be skipped next run and may
need manual repair. `--no-post-overwrites` is not a blanket guarantee that
metadata or thumbnail embedding will never touch an existing file, nor does it
repair file locks. Check `download.log` for details of any remaining errors.

## Other commands

```powershell
python music.py --list
python music.py --dry-run
python music.py --collection "Bach"
python music.py --collection "Mozart" --collection "Mahler"
python music.py --simulate --collection "Mozart - Requiem"
python music.py --output "D:\Music"
python -m unittest discover -s tests -v
```

`--dry-run` is offline and writes nothing. `--simulate` contacts YouTube without
downloading media or updating the archive/failure list; it writes diagnostic logs
and makes one attempt per collection. Exit codes: 0 = no reported errors,
1 = collections with reported errors, 2 = setup/input error, 130 = interrupted.

Edit `catalogue.json` to change the collection names or URLs. Only run one instance
against a given output directory at a time. Keep collection names stable when
resuming. Source availability and metadata depend on YouTube; a successful exit
does not guarantee that a playlist has no private or filtered items.

Implementation reference: [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp#readme).

## Metadata cleanup

After downloads finish, preview or apply the library tag cleanup:

```powershell
python clean_metadata.py
python clean_metadata.py --apply
```

The cleanup uses existing recording descriptions and curated corrections to set
titles, performers, album artists, composers, albums, track numbers, genre and
known conductors. It normalizes Unicode accents, keeps source descriptions and
artwork, and removes unverified upload years from the release-date field.
Unidentified performers remain explicitly unknown. Recording attribution is
based on source claims, not independent audio identification.

Preview details are saved to `metadata-preview.json`. Before editing each MP3,
the script saves a full copy under `metadata-backups/<timestamp>/`, retaining
the original collection folders. It verifies the audio payload hash, artwork,
and changed tags after writing; unsuccessful verification restores the original.
The backup manifest records updated files and errors. To undo a change, copy
the corresponding backup MP3 over its counterpart in `classical_music/` while
the downloader and music player are stopped.

Filenames, folders and the download archive are unchanged. Run this only after
the downloader finishes, then rescan the library in your music player. The
recording overrides apply to this catalogue's existing recordings; review them
before using this script on replacement recordings or an expanded catalogue.

## Filename cleanup

Run `python clean_filenames.py --apply` to remove trailing video IDs and tidy
spacing and quotation marks in existing filenames. Track prefixes are retained.
The script checks for collisions and verifies whole-file hashes after renaming;
a timestamped `filename-renames-*.json` records old and new paths. Embedded tags
and audio are unchanged. The library audit uses embedded source links for renamed
files. Keep `downloaded.txt`: the archive still skips completed videos regardless
of the new names. Without that archive, downloads may create duplicates because
cleaned filenames can differ from the original YouTube titles.
