"""``python -m essay_transformer`` 入口（委托给唯一适配层 cli.main）。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
