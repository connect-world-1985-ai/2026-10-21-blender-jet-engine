"""Entry point: blender -b -P scripts/build_engine.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tf.engine import build

if __name__ == "__main__":
    build()
