import os
import sys
from pathlib import Path

# Tests never touch a database or the network. Settings still need to resolve.
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
