from __future__ import annotations

import httpx
import pytest

from job_discovery.discovery import (
    AtsCandidate,
    DiscoveryError,
    discover_company_source,
    extract_ats_candidates,
)
from job_discovery.schemas import SourcePlatform


async def public_resolver(_host: str, _port: int) -> set[str]:
    return {"93.184.216.34"}


def test_extract_candidates_from_supported_public_urls():
    page = """
    <a href="https://job-boards.greenhouse.io/example-green/jobs/123">Greenhouse</a>
    <a href="https://jobs.lever.co/example-lever/abc">Lever</a>
    <script>window.board = "https:\\/\\/jobs.ashbyhq.com\\/example-ashby";</script>
    """

    assert extract_ats_candidates(page, "https://example.com/careers") == {
        AtsCandidate(SourcePlatform.GREENHOUSE, "example-green"),
        AtsCandidate(SourcePlatform.LEVER, "example-lever"),
        AtsCandidate(SourcePlatform.ASHBY, "example-ashby"),
    }


@pytest.mark.asyncio
async def test_discovery_validates_detected_board_with_existing_adapter():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "company.example":
            return httpx.Response(
                200,
                text='<a href="https://job-boards.greenhouse.io/example">Jobs</a>',
            )
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 1,
                            "title": "Operations Analyst",
                            "absolute_url": "https://example.com/jobs/1",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover_company_source(
            "Example",
            "https://company.example/careers",
            client=client,
            resolver=public_resolver,
        )

    assert result.platform == SourcePlatform.GREENHOUSE
    assert result.identifier == "example"
    assert result.job_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/careers",
        "http://localhost/careers",
        "https://company.example:8443/careers",
        "file:///etc/passwd",
    ],
)
async def test_discovery_rejects_nonpublic_or_nonweb_targets(url: str):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: None)) as client:
        with pytest.raises(DiscoveryError):
            await discover_company_source(
                "Example",
                url,
                client=client,
                resolver=public_resolver,
            )


@pytest.mark.asyncio
async def test_discovery_rechecks_redirect_targets_for_private_addresses():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DiscoveryError, match="public internet address"):
            await discover_company_source(
                "Example",
                "https://company.example/careers",
                client=client,
                resolver=public_resolver,
            )


@pytest.mark.asyncio
async def test_discovery_rejects_oversized_pages():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": "1000001"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DiscoveryError, match="too large"):
            await discover_company_source(
                "Example",
                "https://company.example/careers",
                client=client,
                resolver=public_resolver,
            )
