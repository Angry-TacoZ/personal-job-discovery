from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from job_discovery.config import load_config, resolve_project_path
from job_discovery.database import create_session_factory
from job_discovery.repository import Repository
from job_discovery.scan import run_scan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local-first public ATS job monitor")
    parser.add_argument("--config", default="config/companies.yml", help="YAML configuration path")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="Fetch, score, persist, and report jobs")
    subparsers.add_parser("init-db", help="Create the local database and sync companies")
    serve = subparsers.add_parser("serve", help="Start the local dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config_path = Path(args.config).resolve()
    config, project_root = load_config(config_path)

    if args.command == "serve":
        from job_discovery.web.app import create_app

        uvicorn.run(create_app(config_path), host=args.host, port=args.port)
        return 0

    database_path = resolve_project_path(project_root, config.database_path)
    repository = Repository(create_session_factory(database_path))
    repository.sync_companies(config.companies)
    if args.command == "init-db":
        print(f"Initialized {database_path}")
        return 0
    if args.command == "scan":
        summary = asyncio.run(run_scan(config, project_root, repository))
        print(json.dumps(summary, indent=2))
        return 0 if summary["status"] in {"success", "partial"} else 1
    return 2

