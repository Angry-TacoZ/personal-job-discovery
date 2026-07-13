from __future__ import annotations

from pathlib import Path

import yaml

from job_discovery.schemas import AppConfig, ResumeProfile


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


def load_resume_profile(config: AppConfig, project_root: Path) -> ResumeProfile | None:
    if not config.resume_profile_path:
        return None
    profile_path = resolve_project_path(project_root, config.resume_profile_path)
    if not profile_path.is_relative_to(project_root):
        raise ValueError("resume profile must stay inside the project directory")
    if not profile_path.is_file():
        return None
    with profile_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("resume profile root must be an object")
    return ResumeProfile.model_validate(raw)
