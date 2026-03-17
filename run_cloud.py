"""Startet die SecureSend Cloud API aus dem richtigen Verzeichnis."""
import os
import sys
from pathlib import Path

project_dir = Path(__file__).parent
cloud_dir = project_dir / "cloud"
os.chdir(cloud_dir)
sys.path.insert(0, str(cloud_dir))
sys.path.insert(1, str(project_dir))  # für core.storage, core.email, core.sms

import uvicorn

# Im Docker-Container auf 0.0.0.0 lauschen, lokal auf 127.0.0.1
host = os.environ.get("UVICORN_HOST", "127.0.0.1")
uvicorn.run("main:app", host=host, port=8001, reload=False)
