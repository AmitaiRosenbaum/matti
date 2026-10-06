"""Compatibility entry point; new workflows use the matti CLI."""
import sys
from matti.lyrics import pipeline as _implementation

if __name__ == "__main__":
    from pathlib import Path
    args = sys.argv[1:]
    root = Path(__file__).resolve().parent
    if "--library" not in args:
        args = ["--library", str(root / "downloads/amitai-songs")] + args
    if "--aliases" not in args and (root / "profiles/amitai-aliases.json").is_file():
        args = ["--aliases", str(root / "profiles/amitai-aliases.json")] + args
    raise SystemExit(_implementation.main(args))
else:
    sys.modules[__name__] = _implementation
