from __future__ import annotations

import os
import shutil
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_DIR / "runtime"


def configured_path(env_names: tuple[str, ...], default: Path) -> Path:
    """Resolve a runtime path, defaulting to the project-local portable runtime."""
    for name in env_names:
        value = os.environ.get(name)
        if value:
            path = Path(value).expanduser()
            return (PROJECT_DIR / path).resolve() if not path.is_absolute() else path.resolve()
    return default.resolve()


RUNGTAP_DIR = configured_path(("GTAP_RUNGTAP_DIR", "RUNGTAP_DIR"), RUNTIME_DIR / "rungtap")
GTAPAGG_DIR = configured_path(("GTAPAGG_DIR",), RUNTIME_DIR / "gtapagg")
GEMPACK_DIR = configured_path(("GEMPACK_DIR",), RUNTIME_DIR / "gempack")


def env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    path = Path(value).expanduser()
    return (PROJECT_DIR / path).resolve() if not path.is_absolute() else path.resolve()


def find_executable(name: str) -> str | None:
    """Find project-bundled executables before consulting the host PATH."""
    for root in (GEMPACK_DIR, RUNGTAP_DIR, GTAPAGG_DIR):
        candidate = root / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)
