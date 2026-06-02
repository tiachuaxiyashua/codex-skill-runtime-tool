from __future__ import annotations

import html.parser
import json
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class BrowserPage:
    url: str = ""
    title: str = ""
    content_type: str = ""
    text: str = ""
    html: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    fetched_at: str = ""


class LightweightBrowser:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.state_path = session_dir / "web-browser" / "state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def open(self, url: str, *, timeout: int = 20, max_bytes: int = 500000) -> BrowserPage:
        request = urllib.request.Request(url, headers={"User-Agent": "codex-skill-runtime/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes)
            final_url = response.geturl()
            content_type = response.headers.get("content-type", "")
        html_text = body.decode(_encoding_from_content_type(content_type), errors="replace")
        parser = _HTMLTextParser(base_url=final_url)
        parser.feed(html_text)
        page = BrowserPage(
            url=final_url,
            title=parser.title.strip(),
            content_type=content_type,
            text="\n".join(line.strip() for line in parser.text_parts if line.strip())[:200000],
            html=html_text[:200000],
            links=parser.links[:500],
            fetched_at=datetime.now().isoformat(timespec="seconds"),
        )
        self._write_page(page)
        return page

    def current(self) -> BrowserPage:
        data = _load_json(self.state_path)
        page = data.get("page") if isinstance(data, dict) else {}
        return BrowserPage(**{key: page.get(key, getattr(BrowserPage(), key)) for key in BrowserPage.__dataclass_fields__}) if isinstance(page, dict) else BrowserPage()

    def click(self, index: int | None = None, *, text: str = "", href: str = "", timeout: int = 20) -> BrowserPage:
        current = self.current()
        target = href
        if not target and index is not None:
            try:
                target = current.links[index]["href"]
            except (IndexError, KeyError):
                raise ValueError(f"link index not found: {index}") from None
        if not target and text:
            needle = text.lower()
            for link in current.links:
                if needle in link.get("text", "").lower():
                    target = link.get("href", "")
                    break
        if not target:
            raise ValueError("web_browser click requires index, href, or text")
        return self.open(target, timeout=timeout)

    def find(self, pattern: str) -> list[dict[str, Any]]:
        page = self.current()
        matches: list[dict[str, Any]] = []
        lowered = page.text.lower()
        needle = pattern.lower()
        start = 0
        while needle and len(matches) < 100:
            index = lowered.find(needle, start)
            if index < 0:
                break
            matches.append({"offset": index, "preview": page.text[max(0, index - 120): index + len(pattern) + 120]})
            start = index + len(pattern)
        return matches

    def _write_page(self, page: BrowserPage) -> None:
        self.state_path.write_text(json.dumps({"page": asdict(page)}, ensure_ascii=False, indent=2), encoding="utf-8")


class _HTMLTextParser(html.parser.HTMLParser):
    def __init__(self, *, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.title = ""
        self.links: list[dict[str, str]] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._current_href = ""
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "a":
            attrs_dict = {key.lower(): value or "" for key, value in attrs}
            href = attrs_dict.get("href", "")
            self._current_href = urllib.parse.urljoin(self.base_url, href) if href else ""
            self._current_link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
        if tag.lower() == "a" and self._current_href:
            text = " ".join(part.strip() for part in self._current_link_text if part.strip())
            self.links.append({"text": text[:300], "href": self._current_href})
            self._current_href = ""
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._current_href:
            self._current_link_text.append(data)
        self.text_parts.append(data)


def _encoding_from_content_type(content_type: str) -> str:
    lowered = content_type.lower()
    marker = "charset="
    if marker in lowered:
        return lowered.split(marker, 1)[1].split(";", 1)[0].strip() or "utf-8"
    return "utf-8"


def _load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
