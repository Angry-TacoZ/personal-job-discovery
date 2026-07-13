from __future__ import annotations

import argparse
import json
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from job_discovery.web.app import create_app

CONTROL_URL = "http://127.0.0.1:8000/control"
HEALTH_URL = "http://127.0.0.1:8000/health"


def dashboard_is_ready() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=0.7) as response:
            body = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and body.get("status") == "ok"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _open_control_center() -> None:
    webbrowser.open(CONTROL_URL)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Personal Job Discovery control center")
    parser.add_argument("--config", default="config/companies.yml")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    if dashboard_is_ready():
        _open_control_center()
        return 0
    threading.Timer(0.8, _open_control_center).start()
    uvicorn.run(create_app(config_path), host="127.0.0.1", port=8000, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
