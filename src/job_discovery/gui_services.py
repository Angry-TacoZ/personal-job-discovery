from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from job_discovery.schemas import AppConfig, CompanyConfig

TASK_NAME = "Personal Job Discovery Scan"
ALLOWED_INTERVALS = {3, 6, 12, 24}


class ConfigStore:
    def __init__(self, config_path: str | Path) -> None:
        self.path = Path(config_path).expanduser().resolve()

    def read_raw(self) -> dict[str, Any]:
        with self.path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        if not isinstance(raw, dict):
            raise ValueError("configuration root must be an object")
        AppConfig.model_validate(raw)
        return raw

    def app_config(self) -> AppConfig:
        return AppConfig.model_validate(self.read_raw())

    def companies(self) -> list[CompanyConfig]:
        return self.app_config().companies

    def save_company(self, company: CompanyConfig, index: int | None = None) -> None:
        raw = self.read_raw()
        companies = raw.setdefault("companies", [])
        serialized = company.model_dump(mode="json")
        if index is None:
            companies.append(serialized)
        elif 0 <= index < len(companies):
            companies[index] = serialized
        else:
            raise IndexError("company selection is no longer valid")
        self._write_validated(raw)

    def delete_company(self, index: int) -> None:
        raw = self.read_raw()
        companies = raw.get("companies", [])
        if not 0 <= index < len(companies):
            raise IndexError("company selection is no longer valid")
        del companies[index]
        if not companies:
            raise ValueError("at least one company must remain in the configuration")
        self._write_validated(raw)

    def save_settings(self, threshold: int, timeout: float, retries: int) -> None:
        raw = self.read_raw()
        raw["score_alert_threshold"] = threshold
        raw["request_timeout_seconds"] = timeout
        raw["request_retries"] = retries
        self._write_validated(raw)

    def _write_validated(self, raw: dict[str, Any]) -> None:
        AppConfig.model_validate(raw)
        content = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def background_python_executable(executable: str | Path | None = None) -> Path:
    current = Path(executable or sys.executable).resolve()
    pythonw = current.with_name("pythonw.exe")
    return pythonw if pythonw.is_file() else current


def build_schedule_command(
    config_path: str | Path,
    interval_hours: int,
    executable: str | Path | None = None,
    start_at: datetime | None = None,
) -> list[str]:
    if interval_hours not in ALLOWED_INTERVALS:
        raise ValueError(f"interval must be one of {sorted(ALLOWED_INTERVALS)}")
    config = Path(config_path).expanduser().resolve()
    python = background_python_executable(executable)
    start = start_at or datetime.now() + timedelta(minutes=2)
    task_action = f'"{python}" -m job_discovery --config "{config}" scan'
    return [
        "schtasks.exe",
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        task_action,
        "/SC",
        "HOURLY",
        "/MO",
        str(interval_hours),
        "/ST",
        start.strftime("%H:%M"),
        "/RL",
        "LIMITED",
        "/F",
    ]


class TaskScheduler:
    def __init__(self, config_path: str | Path, executable: str | Path | None = None) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.executable = executable

    def is_installed(self) -> bool:
        result = self._run(["schtasks.exe", "/Query", "/TN", TASK_NAME])
        return result.returncode == 0

    def install(self, interval_hours: int) -> str:
        result = self._run(
            build_schedule_command(self.config_path, interval_hours, self.executable)
        )
        if result.returncode != 0:
            raise RuntimeError(_safe_task_error(result))
        return result.stdout.strip() or "Scheduled scan installed."

    def remove(self) -> str:
        result = self._run(["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"])
        if result.returncode != 0:
            raise RuntimeError(_safe_task_error(result))
        return result.stdout.strip() or "Scheduled scan removed."

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
            creationflags=creation_flags,
        )


def _safe_task_error(result: subprocess.CompletedProcess[str]) -> str:
    message = (result.stderr or result.stdout).strip()
    return message[:1000] or f"Task Scheduler returned exit code {result.returncode}."
