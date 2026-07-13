from __future__ import annotations

from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return "\n".join(parser.parts)


def infer_remote_status(location: str, title: str = "") -> str:
    value = f"{location} {title}".casefold()
    if "hybrid" in value:
        return "hybrid"
    if "remote" in value:
        return "remote"
    if location and location.casefold() not in {"unknown", "multiple locations"}:
        return "onsite"
    return "unknown"

