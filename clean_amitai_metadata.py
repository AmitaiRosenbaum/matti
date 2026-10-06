"""Compatibility entry point; new workflows use the matti CLI."""
import sys
from matti.legacy import amitai_metadata as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
