from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

import httpx

from job_discovery.adapters import ADAPTERS
from job_discovery.adapters.base import AdapterError
from job_discovery.schemas import CompanyConfig, SourcePlatform

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_RAW_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 4
_MAX_PAGE_BYTES = 1_000_000
_MAX_CANDIDATES = 12

Resolver = Callable[[str, int], Awaitable[set[str]]]


class DiscoveryError(RuntimeError):
    """A safe, user-facing automatic-discovery failure."""


@dataclass(frozen=True, order=True)
class AtsCandidate:
    platform: SourcePlatform
    identifier: str


@dataclass(frozen=True)
class DiscoveryResult:
    platform: SourcePlatform
    identifier: str
    job_count: int
    careers_url: str


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name.casefold() == "href" and value:
                self.links.append(value)


def _candidate(platform: SourcePlatform, identifier: str | None) -> AtsCandidate | None:
    if identifier is None:
        return None
    cleaned = unquote(identifier).strip()
    if not _IDENTIFIER.fullmatch(cleaned):
        return None
    return AtsCandidate(platform=platform, identifier=cleaned)


def _candidates_from_url(value: str) -> set[AtsCandidate]:
    normalized = value.replace(r"\/", "/")
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").casefold().rstrip(".")
    segments = [segment for segment in parsed.path.split("/") if segment]
    query = parse_qs(parsed.query)
    found: set[AtsCandidate] = set()

    candidate: AtsCandidate | None = None
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        identifier = query.get("for", [None])[0]
        candidate = _candidate(
            SourcePlatform.GREENHOUSE,
            identifier or (segments[0] if segments and segments[0] != "embed" else None),
        )
    elif host == "boards-api.greenhouse.io" and len(segments) >= 3:
        if segments[:2] == ["v1", "boards"]:
            candidate = _candidate(SourcePlatform.GREENHOUSE, segments[2])
    elif host == "jobs.lever.co" and segments:
        candidate = _candidate(SourcePlatform.LEVER, segments[0])
    elif host == "api.lever.co" and len(segments) >= 3:
        if segments[:2] == ["v0", "postings"]:
            candidate = _candidate(SourcePlatform.LEVER, segments[2])
    elif host == "jobs.ashbyhq.com" and segments:
        candidate = _candidate(SourcePlatform.ASHBY, segments[0])
    elif host == "api.ashbyhq.com" and len(segments) >= 3:
        if segments[:2] == ["posting-api", "job-board"]:
            candidate = _candidate(SourcePlatform.ASHBY, segments[2])

    if candidate is not None:
        found.add(candidate)
    return found


def extract_ats_candidates(page: str, base_url: str) -> set[AtsCandidate]:
    """Extract supported ATS board identifiers without executing page content."""
    found = _candidates_from_url(base_url)
    parser = _LinkParser()
    parser.feed(page)
    for link in parser.links:
        found.update(_candidates_from_url(urljoin(base_url, link)))
    searchable = unescape(page).replace(r"\/", "/")
    for match in _RAW_URL.findall(searchable):
        found.update(_candidates_from_url(match.rstrip("),.;")))
    return found


async def _resolve_host(host: str, port: int) -> set[str]:
    def resolve() -> set[str]:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return {record[4][0].split("%", maxsplit=1)[0] for record in records}

    try:
        return await asyncio.to_thread(resolve)
    except socket.gaierror as exc:
        raise DiscoveryError("The careers-page hostname could not be resolved.") from exc


async def _public_url(value: str, resolver: Resolver) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise DiscoveryError("Enter a valid public careers-page URL.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DiscoveryError("Enter a full public URL beginning with http:// or https://.")
    if parsed.username or parsed.password:
        raise DiscoveryError("Careers-page URLs cannot include credentials.")
    expected_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != expected_port:
        raise DiscoveryError("Careers-page URLs must use the standard web port.")

    host = parsed.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith(".local"):
        raise DiscoveryError("The careers URL must point to a public internet address.")
    try:
        literal = ipaddress.ip_address(host)
        addresses = {str(literal)}
    except ValueError:
        addresses = await resolver(host, expected_port)
    if not addresses:
        raise DiscoveryError("The careers-page hostname did not resolve to an address.")
    try:
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise DiscoveryError("The careers URL must point to a public internet address.")
    except ValueError as exc:
        raise DiscoveryError("The careers-page hostname returned an invalid address.") from exc
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


async def _fetch_public_page(
    url: str,
    client: httpx.AsyncClient,
    resolver: Resolver,
) -> tuple[str, list[str]]:
    current = url
    visited: list[str] = []
    for redirect_count in range(_MAX_REDIRECTS + 1):
        current = await _public_url(current, resolver)
        visited.append(current)
        try:
            async with client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise DiscoveryError("The careers page returned an invalid redirect.")
                    if redirect_count >= _MAX_REDIRECTS:
                        raise DiscoveryError("The careers page redirected too many times.")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise DiscoveryError("The careers page could not be read successfully.")
                declared_size = response.headers.get("content-length")
                if (
                    declared_size
                    and declared_size.isdigit()
                    and int(declared_size) > _MAX_PAGE_BYTES
                ):
                    raise DiscoveryError("The careers page is too large to inspect safely.")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > _MAX_PAGE_BYTES:
                        raise DiscoveryError("The careers page is too large to inspect safely.")
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8", errors="replace"), visited
        except DiscoveryError:
            raise
        except httpx.HTTPError as exc:
            raise DiscoveryError("The careers page could not be reached.") from exc
    raise DiscoveryError("The careers page redirected too many times.")


async def _validate_candidates(
    candidates: set[AtsCandidate],
    company_name: str,
    client: httpx.AsyncClient,
) -> list[tuple[AtsCandidate, int]]:
    valid: list[tuple[AtsCandidate, int]] = []
    for item in sorted(candidates)[:_MAX_CANDIDATES]:
        company = CompanyConfig(
            company_name=company_name,
            ats_platform=item.platform,
            ats_identifier=item.identifier,
        )
        try:
            result = await ADAPTERS[item.platform].fetch(company, client, retries=0)
        except AdapterError:
            continue
        valid.append((item, len(result.jobs)))
    return valid


async def discover_company_source(
    company_name: str,
    careers_url: str,
    *,
    timeout_seconds: float = 20,
    user_agent: str = "PersonalJobDiscovery/0.1 (+local personal-use monitor)",
    client: httpx.AsyncClient | None = None,
    resolver: Resolver = _resolve_host,
) -> DiscoveryResult:
    """Detect and validate one supported public ATS board without saving it."""
    cleaned_name = company_name.strip()
    cleaned_url = careers_url.strip()
    if not cleaned_name:
        raise DiscoveryError("Enter a company name.")

    async def run(active_client: httpx.AsyncClient) -> DiscoveryResult:
        candidates = _candidates_from_url(cleaned_url)
        if not candidates:
            page, visited = await _fetch_public_page(cleaned_url, active_client, resolver)
            for visited_url in visited:
                candidates.update(_candidates_from_url(visited_url))
            candidates.update(extract_ats_candidates(page, visited[-1]))
        if not candidates:
            raise DiscoveryError(
                "No Greenhouse, Lever, or Ashby job board was found on that careers page."
            )
        if len(candidates) > _MAX_CANDIDATES:
            raise DiscoveryError("Too many ATS links were found to identify one company safely.")
        valid = await _validate_candidates(candidates, cleaned_name, active_client)
        if not valid:
            raise DiscoveryError(
                "An ATS link was found, but its public job feed could not be validated."
            )
        if len(valid) > 1:
            raise DiscoveryError(
                "More than one supported ATS board was found. "
                "Paste the company's direct careers URL."
            )
        candidate, job_count = valid[0]
        return DiscoveryResult(
            platform=candidate.platform,
            identifier=candidate.identifier,
            job_count=job_count,
            careers_url=cleaned_url,
        )

    if client is not None:
        return await run(client)
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"},
    ) as active_client:
        return await run(active_client)
