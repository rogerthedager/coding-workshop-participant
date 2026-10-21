"""Operate the isolated ACME development service without modifying other projects."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
OWNER = "acme-facility-service-local-v1"
PORT = 55439
DATABASE = "acme_facility_dev"


def run(command: list[str], env: dict[str, str] | None = None) -> None:
    """Run a child process and propagate its failure to the caller."""
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def pg_bin() -> Path:
    """Locate the explicitly selected or installed PostgreSQL binaries."""
    configured = os.environ.get("FACILITY_PG_BIN")
    if configured:
        result = Path(configured)
    elif shutil.which("initdb"):
        result = Path(shutil.which("initdb") or "").parent
    else:
        # Newest installed version first; 16 is the target, newer majors also work.
        candidates = [Path(rf"C:\Program Files\PostgreSQL\{major}\bin") for major in (18, 17, 16)]
        result = next((p for p in candidates if (p / "initdb.exe").exists()), candidates[-1])
    if not (result / ("initdb.exe" if os.name == "nt" else "initdb")).exists():
        raise RuntimeError("Install PostgreSQL 16, or set FACILITY_PG_BIN to its bin directory.")
    return result


def executable(name: str) -> str:
    """Return a PostgreSQL program's absolute path."""
    return str(pg_bin() / (name + (".exe" if os.name == "nt" else "")))


def credentials(create: bool = False) -> dict[str, str]:
    """Read only this service's credentials; refuse to adopt unowned directories."""
    marker = RUNTIME / "local.json"
    if marker.exists():
        result = json.loads(marker.read_text(encoding="utf-8"))
        if result.get("owner") != OWNER:
            raise RuntimeError("Runtime owner marker does not match this service.")
        return result
    if not create:
        raise RuntimeError("Run 'python scripts/local.py init' first.")
    if RUNTIME.exists() and any(RUNTIME.iterdir()):
        raise RuntimeError(
            "Refusing to adopt a nonempty .runtime directory without its owner marker."
        )
    RUNTIME.mkdir(exist_ok=True)
    result = {
        "owner": OWNER,
        "username": "acme_local",
        "password": secrets.token_urlsafe(32),
        "jwt_secret": secrets.token_urlsafe(48),
    }
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(result, stream)
    if os.name == "nt":
        # Credentials remain readable only by their creating Windows user.
        principal = subprocess.check_output(["whoami"], text=True).strip()
        run(["icacls", str(marker), "/inheritance:r", "/grant:r", f"{principal}:(F)"])
    return result


def environment(create: bool = False) -> dict[str, str]:
    """Build a child environment without printing secrets."""
    result = os.environ.copy()
    if "FACILITY_DATABASE_URL" not in result:
        config = credentials(create)
        password = quote(config["password"], safe="")
        result["FACILITY_DATABASE_URL"] = (
            f"postgresql+asyncpg://{config['username']}:{password}@127.0.0.1:{PORT}/{DATABASE}"
        )
        result.setdefault("FACILITY_JWT_SECRET", config["jwt_secret"])
    if not result.get("FACILITY_JWT_SECRET"):
        raise RuntimeError("Set FACILITY_JWT_SECRET when using an external database URL.")
    result.setdefault("IS_LOCAL", "true")
    result.setdefault("TEST_DATABASE_URL", result["FACILITY_DATABASE_URL"])
    return result


async def create_database(config: dict[str, str]) -> None:
    """Create only the isolated development database when it is missing."""
    import asyncpg

    connection = await asyncpg.connect(
        host="127.0.0.1",
        port=PORT,
        user=config["username"],
        password=config["password"],
        database="postgres",
        timeout=15,
    )
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname=$1",
            DATABASE,
        )
        if not exists:
            await connection.execute(f'CREATE DATABASE "{DATABASE}"')
    finally:
        await connection.close()


def initialize() -> None:
    """Initialize and start a separately owned, loopback-only PostgreSQL cluster."""
    config = credentials(create=True)
    data = RUNTIME / "postgres-data"
    if not (data / "PG_VERSION").exists():
        if data.exists() and any(data.iterdir()):
            raise RuntimeError("Refusing to initialize a nonempty PostgreSQL data directory.")
        password_file = RUNTIME / "init-password.tmp"
        descriptor = os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(config["password"])
        try:
            if os.name == "nt":
                principal = subprocess.check_output(["whoami"], text=True).strip()
                run(
                    [
                        "icacls",
                        str(password_file),
                        "/inheritance:r",
                        "/grant:r",
                        f"{principal}:(F)",
                    ]
                )
            run(
                [
                    executable("initdb"),
                    "-D",
                    str(data),
                    "-U",
                    config["username"],
                    "--pwfile",
                    str(password_file),
                    "--auth-host=scram-sha-256",
                    "--auth-local=scram-sha-256",
                    "--encoding=UTF8",
                    "--locale=C",
                ]
            )
        finally:
            password_file.unlink(missing_ok=True)
    status = subprocess.run(
        [executable("pg_ctl"), "-D", str(data), "status"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if status.returncode:
        run(
            [
                executable("pg_ctl"),
                "-D",
                str(data),
                "-l",
                str(RUNTIME / "postgres.log"),
                "-o",
                f"-p {PORT} -h 127.0.0.1",
                "-w",
                "start",
            ]
        )
    asyncio.run(create_database(config))
    print(f"Isolated PostgreSQL is ready at 127.0.0.1:{PORT}/{DATABASE}.")


def main() -> None:
    """Dispatch local setup, migration, seed, serving and validation commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["init", "migrate", "seed", "serve", "jobs", "test", "status", "stop"],
    )
    arguments, remainder = parser.parse_known_args()
    if arguments.command == "init":
        initialize()
    elif arguments.command in {"status", "stop"}:
        credentials()
        command = [executable("pg_ctl"), "-D", str(RUNTIME / "postgres-data")]
        command += ["status"] if arguments.command == "status" else ["-m", "fast", "-w", "stop"]
        run(command)
    else:
        env = environment()
        if arguments.command == "test":
            env.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
        commands = {
            "migrate": [sys.executable, "-m", "alembic", "upgrade", "head"],
            "seed": [sys.executable, "-m", "facility.seed"],
            "jobs": [
                sys.executable,
                "-c",
                "import json, jobs_handler; print(json.dumps(jobs_handler.handler({}, None)))",
            ],
            "serve": [
                sys.executable,
                "-m",
                "uvicorn",
                "facility.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8019",
            ],
            "test": [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "pytest_asyncio.plugin",
                "-p",
                "pytest_cov.plugin",
                "-q",
                "--tb=short",
                "--cov=facility.permissions",
                "--cov=facility.routers.incidents",
                "--cov-report=term-missing",
                "--cov-fail-under=70",
            ],
        }
        run(commands[arguments.command] + remainder, env=env)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Local operation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
