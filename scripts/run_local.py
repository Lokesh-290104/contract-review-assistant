"""Run the whole app WITHOUT Docker: embedded Postgres + pgvector, the API and the web app.

    .venv\\Scripts\\python scripts\\run_local.py

Then open http://localhost:5173. Press Ctrl+C to stop everything.
Data is kept in ./.localdb between runs.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    import pgserver  # from backend/requirements-dev.txt

    if not (ROOT / ".env").exists():
        print("No .env file found. Copy .env.example to .env and fill in your keys first.")
        return 1

    db_dir = ROOT / ".localdb"
    db_dir.mkdir(exist_ok=True)
    server = pgserver.get_server(db_dir, cleanup_mode="stop")
    exists = server.psql("SELECT 1 FROM pg_database WHERE datname = 'contracts';")
    if "1 row" not in exists:
        server.psql("CREATE DATABASE contracts;")
    url = server.get_uri("contracts").replace("postgresql://", "postgresql+psycopg://", 1)
    print(f"Postgres + pgvector running ({url})")

    env = {**os.environ, "DATABASE_URL": url}
    npm = shutil.which("npm") or "npm"
    if not (ROOT / "frontend" / "node_modules").exists():
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=ROOT / "frontend", check=True)

    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000"], cwd=ROOT / "backend", env=env
    )
    web = subprocess.Popen([npm, "run", "dev"], cwd=ROOT / "frontend", env=env)
    print("\nOpen http://localhost:5173  (API on http://localhost:8000). Ctrl+C to stop.\n")
    try:
        api.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in (web, api):
            p.terminate()
        server.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
