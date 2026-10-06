"""Validated project configuration; paths are relative to the config file."""
from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Project:
    library: Path
    links: Path | None = None
    catalogue: Path | None = None
    aliases: Path | None = None
    lyrics: bool = True
    workers: int = 4
    web: bool = True


def load(path: Path) -> Project:
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    allowed = {'library', 'links', 'catalogue', 'aliases', 'lyrics', 'workers', 'web'}
    if data.keys() - allowed:
        raise ValueError(f'Unknown config keys: {sorted(data.keys() - allowed)}')
    if data.get('links') and data.get('catalogue'):
        raise ValueError('Choose links or catalogue, not both')
    for key in ('lyrics', 'web'):
        if key in data and not isinstance(data[key], bool):
            raise ValueError(f'{key} must be true or false')
    workers = data.get('workers', 4)
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError('workers must be an integer between 1 and 8')
    def resolve(key, default=None):
        value = data.get(key, default)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{key} must be a nonempty path string')
        p = Path(value).expanduser()
        return (path.resolve().parent / p).resolve()
    return Project(resolve('library', 'library'), resolve('links'), resolve('catalogue'),
                   resolve('aliases'), data.get('lyrics', True), workers, data.get('web', True))
