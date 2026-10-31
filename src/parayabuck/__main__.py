from __future__ import annotations

import sys

from parayabuck.cli import main

if __name__ == "__main__":
    # F3-5-11: forward the arguments, so `python -m parayabuck halt` runs the halt
    # command. With no arguments the CLI starts the web console, as before.
    sys.exit(main())
