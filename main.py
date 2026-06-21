"""
Convenience entry point – run ``python main.py`` from the project root.

This simply delegates to ``app.main.main()``.
"""
import sys
from pathlib import Path

# Ensure the project root is on sys.path.
_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from app.main import main

if __name__ == "__main__":
    main()
