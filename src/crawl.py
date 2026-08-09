#!/usr/bin/env python3
"""Run the WeChat public-account crawler."""

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "third_party" / "wechat-mp-tools" / ".venv" / "bin" / "python"
if RUNTIME.is_file() and Path(sys.executable).resolve() != RUNTIME.resolve():
    os.execv(str(RUNTIME), [str(RUNTIME), str(Path(__file__).resolve()), *sys.argv[1:]])

from crawler.cli import main


if __name__ == "__main__":
    main()
