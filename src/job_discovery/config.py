from __future__ import annotations

from pathlib import Path

import yaml

from job_discovery.schemas import AppConfig


def load_config(path: str | Path) -> tuple[AppConfig, Path]:
    config_path = Path(path).expanduser().resolve()
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be an object")
    config = AppConfig.model_validate(raw)
    return config, _find_project_root(config_path)


def _find_project_root(config_path: Path) -> Path:
    for candidate in [config_path.parent, *config_path.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    if config_path.parent.name.casefold() == "config":
        return config_path.parent.parent
    return Path.cwd().resolve()


def resolve_project_path(project_root: Path, configured_path: str) -> Path:
    path = Path(configured_path).expanduser()
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()
