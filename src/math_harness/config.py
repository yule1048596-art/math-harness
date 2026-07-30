from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_local_environment(path: Path | str | None = None) -> bool:
    """Load local secrets without overriding explicitly exported variables."""

    configured_path = path or os.getenv("MATH_HARNESS_ENV_FILE")
    if configured_path:
        return load_dotenv(dotenv_path=Path(configured_path), override=False)
    return load_dotenv(override=False)
