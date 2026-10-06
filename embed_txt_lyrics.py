"""Compatibility entry point; new workflows use the matti CLI."""
import sys
from matti.lyrics import embed as _implementation

if __name__ == "__main__":
    from pathlib import Path
    args = sys.argv[1:]
    root = Path(__file__).resolve().parent
    if "--library" not in args:
        args = ["--library", str(root / "downloads/amitai-songs")] + args
    if "--backup-dir" not in args:
        args = ["--backup-dir", str(root / "metadata-backups")] + args
    raise SystemExit(_implementation.main(args))
else:
    sys.modules[__name__] = _implementation
