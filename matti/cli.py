"""Public CLI. Stage modules remain usable without invoking the command line."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

from . import __version__
from .config import Project, load


def project(args):
    if args.config:
        if not args.config.is_file():
            raise ValueError(f'Config does not exist: {args.config}')
        return load(args.config)
    default = Path('matti.toml')
    return load(default) if default.is_file() else Project(Path('library').resolve())


def execute(command, config, extra):
    from . import download
    from .lyrics import pipeline, embed
    from . import albums
    from .ledger import record

    def option_path(option):
        index = extra.index(option)
        if index + 1 == len(extra) or extra[index + 1].startswith('--'):
            raise ValueError(f'{option} requires a path')
        return Path(extra[index + 1]).expanduser().resolve()

    if command == 'run':
        if '--output' in extra:
            config = replace(config, library=option_path('--output'))
        if '--no-lyrics' in extra or '--lyrics' in extra:
            config = replace(config, lyrics='--no-lyrics' not in extra)
            extra = [value for value in extra if value not in ('--lyrics', '--no-lyrics')]

    def downloading():
        source = ['--links', str(config.links)] if config.links else (
            ['--catalogue', str(config.catalogue)] if config.catalogue else [])
        if '--links' in extra or '--catalogue' in extra:
            source = []
        if not source and not any(flag in extra for flag in ('--links', '--catalogue', '--help', '-h')):
            raise ValueError('Set links or catalogue in matti.toml, or pass --links FILE')
        return download.main(source + ['--output', str(config.library), '--no-lyrics'] + extra)

    def lyrics():
        options = ['--library', str(config.library), '--workers', str(config.workers)]
        if config.aliases:
            options += ['--aliases', str(config.aliases)]
        if not config.web:
            options += ['--no-web']
        return pipeline.main(options + extra)

    if command == 'run':
        # Run options belong to download; lyrics uses validated project settings.
        code = execute('download', config, extra)
        if any(flag in extra for flag in ('--dry-run', '--list', '--check', '--simulate', '--help', '-h')):
            return code
        if code in (2, 130):
            return code
        if config.lyrics and config.library.is_dir():
            lyric_code = execute('lyrics', config, [])
            return code or lyric_code
        return code
    action = downloading if command == 'download' else lyrics if command == 'lyrics' else (
        lambda: (albums if command == 'albums' else embed).main(['--library', str(config.library)] + extra))
    if any(flag in extra for flag in ('--help', '-h', '--dry-run', '--list', '--check', '--simulate')) or (command in ('embed', 'albums') and '--apply' not in extra):
        return action()
    # Overrides determine where stage state and history are written.
    destination = config.library
    for option in ('--library', '--output'):
        if option in extra:
            destination = option_path(option)
    if command in ('lyrics', 'embed', 'albums') and not destination.is_dir():
        raise ValueError(f'Library does not exist: {destination}')
    with record(destination, command) as outcome:
        outcome['code'] = action()
        return outcome['code']


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(prog='matti', description='Resumable music downloads and automatic local lyrics.')
    parser.add_argument('--version', action='version', version=f'matti {__version__}')
    parser.add_argument('--config', type=Path, help='Project TOML file (default: ./matti.toml)')
    subs = parser.add_subparsers(dest='command', required=True)
    init = subs.add_parser('init', help='Create a reusable project configuration')
    init.add_argument('--library', default='library')
    init.add_argument('--links', default='songs.txt')
    init.add_argument('--name', default='matti.toml', type=Path)
    status = subs.add_parser('status', help='Report library coverage without networking')
    status.add_argument('--library', type=Path)
    for name, help_text in [('run', 'Download then acquire and embed lyrics'), ('download', 'Download/resume sources'),
                            ('lyrics', 'Acquire synced or plain lyrics'), ('embed', 'Embed local TXT lyrics; preview by default'),
                            ('albums', 'Standardize album tags and folders; preview by default')]:
        subs.add_parser(name, help=help_text, add_help=False)
    args, extra = parser.parse_known_args(argv)
    try:
        if args.command in ('init', 'status') and extra:
            parser.error(f'Unrecognized arguments: {" ".join(extra)}')
        if args.command == 'init':
            content = '\n'.join([f'library = {json.dumps(args.library)}', f'links = {json.dumps(args.links)}',
                                 'lyrics = true', 'workers = 4', 'web = true', ''])
            with args.name.open('x', encoding='utf-8') as stream:
                stream.write(content)
            print(f'Created {args.name}. Add YouTube links to {args.links}, then run matti run.')
            return 0
        config = project(args)
        if args.command == 'status':
            from .library import status
            print(json.dumps(status(args.library.resolve() if args.library else config.library), indent=2))
            return 0
        return execute(args.command, config, extra)
    except (ValueError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
