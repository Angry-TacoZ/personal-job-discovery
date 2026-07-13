from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

import httpx

from job_discovery.schemas import CompanyConfig, FetchResult


class AdapterError(RuntimeError):
    pass


class BaseAdapter(ABC):
    @abstractmethod
    def endpoint(self, company: CompanyConfig) -> str: ...

    @abstractmethod
    def normalize_payload(self, company: CompanyConfig, payload: Any) -> FetchResult: ...

    async def fetch(
        self,
        company: CompanyConfig,
        client: httpx.AsyncClient,
        retries: int,
    ) -> FetchResult:
        url = self.endpoint(company)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = await client.get(url)
                if response.status_code == 429 or response.status_code >= 500:
                    raise AdapterError(f"temporary HTTP {response.status_code}")
                response.raise_for_status()
                return self.normalize_payload(company, response.json())
            except (httpx.HTTPError, ValueError, AdapterError) as exc:
                last_error = exc
                if attempt >= retries:
                    break
                await asyncio.sleep(0.5 * (2**attempt))
        raise AdapterError(f"{company.company_name}: {last_error}") from last_error


def warning(index: int, exc: Exception) -> str:
    return f"Skipped malformed listing at index {index}: {type(exc).__name__}: {exc}"

