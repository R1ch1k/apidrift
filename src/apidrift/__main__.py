"""Enable ``python -m apidrift`` as an alias for the console script."""

from __future__ import annotations

from apidrift.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
