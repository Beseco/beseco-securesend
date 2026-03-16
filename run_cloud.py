"""Startet die SecureSend Cloud API aus dem richtigen Verzeichnis."""
import os
import sys
from pathlib import Path

cloud_dir = Path(__file__).parent / "cloud"
os.chdir(cloud_dir)
sys.path.insert(0, str(cloud_dir))

import uvicorn
uvicorn.run("main:app", host="127.0.0.1", port=8001, reload=False)
