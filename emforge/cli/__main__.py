"""`python -m emforge.cli …`（與 console script `emforge` 等價）。"""
import sys

from . import main

sys.exit(main())
