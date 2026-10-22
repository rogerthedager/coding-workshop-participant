"""Throwaway backend for the Selenium suite. Run it with the backend's Python.

    stack.py create        new acme_facility_e2e database: migrations + showcase seed
    stack.py serve PORT    run the API against that database (blocks)
    stack.py drop          remove the database

It uses the same local PostgreSQL cluster as `bin/facility-local.sh`, but never
touches the dev database (acme_facility_dev). Secrets stay inside this process.
"""

from __future__ import annotations

import asyncio
import glob
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

SERVICE = Path(__file__).resolve().parents[1] / "backend" / "facility-service"
sys.path.insert(0, str(SERVICE / "scripts"))

# Debian/Ubuntu installs PostgreSQL outside PATH; pick the newest version found.
if "FACILITY_PG_BIN" not in os.environ:
    found = sorted(glob.glob("/usr/lib/postgresql/*/bin"), key=lambda p: int(p.split("/")[-2]))
    if found:
        os.environ["FACILITY_PG_BIN"] = found[-1]

import local  # backend/facility-service/scripts/local.py

DATABASE = "acme_facility_e2e"
PASSWORD = os.environ.get("E2E_PASSWORD", "E2e-Passphrase-2026")


def environment() -> dict[str, str]:
    config = local.credentials()
    secret = quote(config["password"], safe="")
    env = os.environ.copy()
    env.update(
        FACILITY_DATABASE_URL=(
            f"postgresql+asyncpg://{config['username']}:{secret}"
            f"@127.0.0.1:{local.PORT}/{DATABASE}"
        ),
        FACILITY_JWT_SECRET=config["jwt_secret"],
        IS_LOCAL="true",
        # The showcase seed outside acme_facility_dev needs an explicit opt-in.
        FACILITY_ALLOW_DEMO_SEED="true",
        DEMO_PASSWORD=PASSWORD,
    )
    # No S3 in this stack: showcase photos are skipped.
    env.pop("FACILITY_ATTACHMENTS_BUCKET", None)
    return env


async def admin(sql: str) -> None:
    import asyncpg

    config = local.credentials()
    connection = await asyncpg.connect(
        host="127.0.0.1", port=local.PORT, user=config["username"],
        password=config["password"], database="postgres", timeout=15,
    )
    try:
        await connection.execute(sql)
    finally:
        await connection.close()


def create() -> None:
    # Starts the local cluster if it is stopped (idempotent).
    subprocess.run([sys.executable, str(SERVICE / "scripts" / "local.py"), "init"],
                   cwd=SERVICE, check=True, stdout=subprocess.DEVNULL)
    asyncio.run(admin(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)'))
    asyncio.run(admin(f'CREATE DATABASE "{DATABASE}"'))
    env = environment()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=SERVICE,
                   env=env, check=True, capture_output=True)
    seed = ("import asyncio; from facility.seed import seed; "
            "print(asyncio.run(seed(include_demo=True, incidents=120, showcase=True)))")
    subprocess.run([sys.executable, "-c", seed], cwd=SERVICE, env=env, check=True)


def serve(port: str) -> None:
    os.chdir(SERVICE)
    os.execve(sys.executable, [sys.executable, "-m", "uvicorn", "facility.main:app",
                               "--host", "127.0.0.1", "--port", port, "--log-level", "warning"],
              environment())


def drop() -> None:
    asyncio.run(admin(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)'))


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "create":
        create()
    elif action == "serve":
        serve(sys.argv[2])
    elif action == "drop":
        drop()
    else:
        sys.exit(__doc__)
