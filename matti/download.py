"""Resumable YouTube MP3 downloads with archive and collection checkpoints."""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse, parse_qs

from matti.paths import workspace
ROOT = workspace()


def load_links(path):
    """Accept plain URLs or pasted Markdown links, one entry per line."""
    items, seen = [], set()
    for line_number, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        markdown = re.fullmatch(r'\[.*?\]\((.*?)\)', line)
        url = (markdown[1] if markdown else line).strip('<> ')
        url = url.replace(r'\_', '_').replace(r'\&', '&').replace('&amp;', '&')
        if url.startswith(('youtube.com/', 'www.youtube.com/', 'music.youtube.com/', 'youtu.be/')):
            url = 'https://' + url
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or parsed.hostname not in (
                'youtube.com', 'www.youtube.com', 'music.youtube.com', 'youtu.be'):
            raise ValueError(f'Line {line_number}: expected a YouTube link')
        query = parse_qs(parsed.query)
        video = query.get('v', [None])[0]
        if parsed.hostname == 'youtu.be':
            video = parsed.path.strip('/')
        elif parsed.path.startswith(('/shorts/', '/live/')):
            video = parsed.path.split('/')[2]
        if video:
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
                raise ValueError(f'Line {line_number}: invalid video ID')
            url, kind = f'https://www.youtube.com/watch?v={video}', 'video'
        elif parsed.path == '/playlist' and query.get('list'):
            playlist = query['list'][0]
            if not re.fullmatch(r'[A-Za-z0-9_-]+', playlist):
                raise ValueError(f'Line {line_number}: invalid playlist ID')
            url, kind = f'https://www.youtube.com/playlist?list={playlist}', 'playlist'
        else:
            raise ValueError(f'Line {line_number}: use a video or playlist link, not a channel/search link')
        if url in seen:
            continue
        seen.add(url)
        index = len(items) + 1
        items.append(dict(name=f'{index:03d} - {kind.title()}', url=url, kind=kind,
                          input_index=index))
    if not items:
        raise ValueError('The links file contains no videos or playlists')
    return items


def load_catalogue(path):
    items = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(items, list) or not items:
        raise ValueError("Catalogue must be a nonempty JSON list")
    names = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each collection must contain name and url")
        name, url = item.get("name", ""), item.get("url", "")
        if (not isinstance(name, str) or not name or
                re.search(r'[<>:"/\\|?*\x00-\x1f%]', name) or
                name.endswith((".", " ")) or name in (".", "..") or
                re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name)):
            raise ValueError(f"Invalid collection folder: {name!r}")
        if name.casefold() in names:
            raise ValueError(f"Duplicate collection: {name}")
        names.add(name.casefold())
        if not isinstance(url, str):
            raise ValueError(f"Invalid URL for {name}")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in (
                "youtube.com", "www.youtube.com", "music.youtube.com", "youtu.be"):
            raise ValueError(f"Expected a YouTube HTTPS URL for {name}")
    return items


def preflight(location):
    if importlib.util.find_spec("yt_dlp") is None:
        raise ValueError("Install dependencies: python -m pip install -r requirements.txt")
    runtime = next((r for r in ("deno", "node") if shutil.which(r)), None)
    if not runtime:
        raise ValueError("Install a JavaScript runtime: winget install DenoLand.Deno")
    if importlib.util.find_spec("yt_dlp_ejs") is None:
        raise ValueError("Install YouTube support: python -m pip install -r requirements.txt")
    return location, runtime


def build_command(item, output, ffmpeg=None, runtime=None, simulate=False):
    # Escape literal percent signs in the parent path for yt-dlp's template parser.
    folder = str(output / item["name"]).replace("%", "%%")
    template = folder + "/%(playlist_index&{:03d} - |)s%(title).120B.%(ext)s"
    if item.get('kind') == 'video':
        template = str(output / 'Songs').replace('%', '%%') + f'/{item["input_index"]:03d} - %(title).120B.%(ext)s'
    elif item.get('kind') == 'playlist':
        template = str(output / 'Playlists').replace('%', '%%') + f'/{item["input_index"]:03d} - %(playlist_title,playlist|Playlist).80B/%(playlist_index)03d - %(title).120B.%(ext)s'
        folder_map = output / 'playlist-folders.json'
        if folder_map.exists():
            relative = json.loads(folder_map.read_text(encoding='utf-8')).get(item['url'])
            if relative:
                destination = (output / relative).resolve()
                if not destination.is_relative_to(output.resolve()):
                    raise ValueError('Playlist folder must be inside the output directory')
                template = str(destination).replace('%', '%%') + '/%(playlist_index)03d - %(title).120B.%(ext)s'
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config",
               "--no-playlist" if item.get('kind') == 'video' else "--yes-playlist",
               "--ignore-errors",
               "--continue", "--no-overwrites", "--download-archive", str(output / "downloaded.txt"),
               "--sleep-requests", "2", "--sleep-interval", "6", "--max-sleep-interval", "12",
               "--concurrent-fragments", "1", "--socket-timeout", "30",
               "--retries", "10", "--fragment-retries", "10", "--extractor-retries", "5",
               "--retry-sleep", "http:exp=2:60", "--retry-sleep", "fragment:exp=2:60",
               "--retry-sleep", "extractor:exp=2:60", "--abort-on-unavailable-fragments",
               "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
               "--embed-metadata", "--embed-thumbnail", "--no-post-overwrites", "--convert-thumbnails", "jpg",
               "--parse-metadata", "playlist_index:%(meta_track)s",
               "--windows-filenames", "--newline", "--no-color", "--output", template]
    command += ["--match-filter", "title !~= (?i)unreleased"]
    if ffmpeg:
        command += ["--ffmpeg-location", ffmpeg]
    if runtime:
        command += ["--js-runtimes", runtime]
    if simulate:
        command += ["--simulate"]
    return command + ["--", item["url"]]


def run_process(command, logger):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace",
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        for line in process.stdout:
            logger.info(line.rstrip())
        code = process.wait()
        if code in (130, -2, 3221225786, -1073741510):
            raise KeyboardInterrupt
        return code
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()


def download(item, command, logger, runner=run_process):
    logger.info("Starting: %s", item["name"])
    try:
        code = runner(command, logger)
    except OSError as error:
        logger.error("Could not run downloader: %s", error)
        code = 1
    if code == 0:
        logger.info("Finished: %s", item["name"])
        return True
    logger.warning(
        "%s completed with one or more errors (exit %s). "
        "Moving on; incomplete items will be retried next run.", item["name"], code)
    return False


def update_failures(path, item, failed):
    # Preserve failures from unselected collections; remove recovered entries.
    records = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                name, url = line.split("\t", 1)
                records[name] = url
    if failed:
        records[item["name"]] = item["url"]
    else:
        records.pop(item["name"], None)
    temp = path.with_suffix(".tmp")
    temp.write_text("".join(f"{n}\t{u}\n" for n, u in records.items()), encoding="utf-8")
    temp.replace(path)


def completed_collections(output):
    path = output / 'completed-collections.json'
    if not path.exists():
        return set()
    values = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError(f'Invalid completion state: {path}')
    return set(values)


def save_completed(output, completed):
    path = output / 'completed-collections.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(sorted(completed), indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def pending_collections(items, output, completed):
    archive_path = output / 'downloaded.txt'
    archive = set(archive_path.read_text(encoding='utf-8').splitlines()) if archive_path.exists() else set()
    failures_path = output / 'failed.txt'
    failures = {line.split('\t', 1)[1] for line in failures_path.read_text(encoding='utf-8').splitlines()
                if '\t' in line} if failures_path.exists() else set()
    pending = []
    for item in items:
        url = item['url']
        query = parse_qs(urlparse(url).query)
        video = query.get('v', [None])[0] if 'list' not in query else None
        if url in failures or not (url in completed or (video and f'youtube {video}' in archive)):
            pending.append(item)
    return pending


def run_lyrics(output):
    """Run the resumable lyrics stage, including when downloads were skipped."""
    return subprocess.run([sys.executable, '-m', 'matti.lyrics.pipeline',
                           '--library', str(output)]).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--catalogue", type=Path, default=ROOT / "catalogue.json")
    source.add_argument("--links", type=Path, help="Text file of YouTube video/playlist URLs, one per line")
    parser.add_argument("--output", type=Path, help="Output folder (default: downloads/LINKS_FILENAME for --links; classical_music otherwise)")
    parser.add_argument("--collection", action="append", default=[], help="Name substring; repeat to select more")
    parser.add_argument("--ffmpeg-location", default=os.environ.get("FFMPEG_LOCATION"))
    parser.add_argument('--refresh', action='store_true', help='Recheck completed collections for new playlist items; retain video archive')
    lyrics = parser.add_mutually_exclusive_group()
    lyrics.add_argument('--lyrics', dest='lyrics', action='store_true', default=None,
                        help='Fetch synced/plain lyrics after downloads (automatic for amitai-songs)')
    lyrics.add_argument('--no-lyrics', dest='lyrics', action='store_false', help='Skip lyrics post-processing')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="List collections offline")
    mode.add_argument("--dry-run", action="store_true", help="Print commands offline without writing files")
    mode.add_argument("--check", action="store_true", help="Check installed dependencies")
    mode.add_argument("--simulate", action="store_true", help="Check YouTube extraction without downloading media")
    args = parser.parse_args(argv)
    try:
        items = load_links(args.links) if args.links else load_catalogue(args.catalogue)
        if args.collection:
            items = [i for i in items if any(s.casefold() in i["name"].casefold() for s in args.collection)]
            if not items:
                raise ValueError("No collections match --collection")
        output = (args.output or (ROOT / 'downloads' / args.links.stem if args.links else ROOT / 'classical_music')).expanduser().resolve()
        fetch_lyrics = (args.lyrics if args.lyrics is not None else
                        bool(args.links and args.links.stem == 'amitai-songs'))
        completed = completed_collections(output)
        if not (args.list or args.check or args.simulate or args.refresh):
            total = len(items)
            items = pending_collections(items, output, completed)
            print(f'Skipping {total - len(items)} completed collections; {len(items)} pending.')
            if not items:
                print('Nothing to download. Use --refresh to check playlists for additions.')
                return run_lyrics(output) if fetch_lyrics and not args.dry_run else 0
        if args.list or args.dry_run:
            for item in items:
                print(f'{item["name"]}\n  {item["url"]}')
                if args.dry_run:
                    print(subprocess.list2cmdline(build_command(item, output, args.ffmpeg_location)))
            return 0
        ffmpeg, runtime = preflight(args.ffmpeg_location)
        if args.check:
            print(f"Python dependencies ready. JavaScript runtime: {runtime}\nCollections: {len(items)}")
            return 0
        output.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("matti.download")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(output / "download.log", encoding="utf-8")]
        for handler in handlers:
            handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            logger.addHandler(handler)
        failed = 0
        try:
            logger.info("Starting %s collections. Output: %s", len(items), output)
            for index, item in enumerate(items):
                command = build_command(item, output, ffmpeg, runtime, args.simulate)
                ok = download(item, command, logger)
                failed += not ok
                if not args.simulate:
                    if ok:
                        completed.add(item['url'])
                    else:
                        completed.discard(item['url'])
                    save_completed(output, completed)
                    update_failures(output / "failed.txt", item, not ok)
                if index < len(items) - 1:
                    time.sleep(random.randint(15, 30))
            logger.info("%s finished: %s collections completed without reported errors; "
                        "%s collections completed with one or more errors",
                        "Simulation" if args.simulate else "Run", len(items) - failed, failed)
            lyrics_code = run_lyrics(output) if fetch_lyrics and not args.simulate else 0
            return 1 if failed else lyrics_code
        except KeyboardInterrupt:
            logger.info("Stopped. Rerun the script later; downloaded.txt allows completed videos to be skipped and outstanding work to resume.")
            return 130
        finally:
            for handler in handlers:
                logger.removeHandler(handler)
                handler.close()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
