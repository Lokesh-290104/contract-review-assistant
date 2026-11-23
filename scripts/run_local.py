"""Run the whole app WITHOUT Docker: embedded Postgres + pgvector, the API and the web app.

    .venv\\Scripts\\python scripts\\run_local.py

Then open http://localhost:5173. Press Ctrl+C to stop everything.
Data is kept in ./.localdb between runs.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / ".localdb"
PORTS = {8000: "API", 5173: "web app"}


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop_tree(proc: subprocess.Popen) -> None:
    """Stop a process AND its children (on Windows, stopping npm alone leaves node running)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()


def stop_postgres() -> None:
    """Shut Postgres down cleanly, even if an earlier run started it."""
    import pgserver

    pg_ctl = Path(pgserver.__file__).parent / "pginstall" / "bin" / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")
    if (DB_DIR / "postmaster.pid").exists():
        subprocess.run([str(pg_ctl), "-D", str(DB_DIR), "stop", "-m", "fast"], capture_output=True)


def main() -> int:
    import pgserver  # from backend/requirements-dev.txt

    if not (ROOT / ".env").exists():
        print("No .env file found. Copy .env.example to .env and fill in your keys first.")
        return 1
    busy = [f"{name} (port {port})" for port, name in PORTS.items() if port_in_use(port)]
    if busy:
        print("Already running: " + ", ".join(busy) + ". Stop it first (Ctrl+C in its window), then try again.")
        return 1

    db_dir = DB_DIR
    db_dir.mkdir(exist_ok=True)
    try:
        server = pgserver.get_server(db_dir, cleanup_mode="stop")
    except subprocess.TimeoutExpired:
        # After an unclean stop Postgres runs crash recovery, which can take longer than
        # pgserver's 10-second start timeout. Give it time, then attach to it.
        print("Postgres is recovering from an unclean stop; waiting 40 seconds...")
        time.sleep(40)
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
        print("Stopping...")
        for p in (web, api):
            stop_tree(p)
        server.cleanup()
        stop_postgres()
        print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
