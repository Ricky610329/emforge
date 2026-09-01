"""`python -m emforge …`（與 console script `emforge` 等價）。"""
import sys

from .cli import main

sys.exit(main())
