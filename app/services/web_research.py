from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.models.schemas import WebSource

SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
USER_AGENT = "Mozilla/5.0 (compatible; PacketTracerAI/1.0; +https://cisco-pt-assistant.local)"
MAX_PAGE_TEXT_CHARS = 2200


@dataclass
class WebResearchResult:
    status: str
    sources: list[WebSource]
    brief: str


class WebResearchService:
    def __init__(self) -> None:
        self.enabled = settings.WEB_RESEARCH_ENABLED
        self.timeout = settings.WEB_RESEARCH_TIMEOUT_SECONDS
        self.max_results = max(settings.WEB_RESEARCH_MAX_RESULTS, 1)
        self.allowed_domains = [domain.lower() for domain in settings.WEB_RESEARCH_ALLOWED_DOMAINS]

    async def research(self, query: str) -> WebResearchResult:
        if not self.enabled or not query.strip():
            return WebResearchResult(status="skipped", sources=[], brief="")

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as client:
                results = await self._search(client, query)
                sources: list[WebSource] = []

                for candidate in results:
                    page_text = await self._fetch_page_text(client, candidate["url"])
                    if not page_text:
                        continue

                    excerpt = self._build_excerpt(page_text)
                    title = candidate["title"] or candidate["domain"]
                    sources.append(
                        WebSource(
                            title=f"{title} | {excerpt}" if excerpt else title,
                            url=candidate["url"],
                            domain=candidate["domain"],
                        )
                    )

                    if len(sources) >= self.max_results:
                        break

                if not sources:
                    return WebResearchResult(status="unavailable", sources=[], brief="")

                return WebResearchResult(
                    status="used",
                    sources=sources,
                    brief=self._build_brief(sources),
                )
        except Exception:
            return WebResearchResult(status="unavailable", sources=[], brief="")

    async def _search(self, client: httpx.AsyncClient, query: str) -> list[dict[str, str]]:
        response = await client.get(SEARCH_ENDPOINT, params={"q": query})
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        candidates: list[dict[str, str]] = []

        for anchor in soup.select("a.result__a"):
            raw_href = anchor.get("href", "")
            url = self._normalize_search_result_url(raw_href)
            if not url:
                continue

            domain = self._extract_domain(url)
            if not self._is_allowed_domain(domain):
                continue

            title = " ".join(anchor.get_text(" ", strip=True).split())
            candidates.append({"title": title, "url": url, "domain": domain})

            if len(candidates) >= self.max_results * 3:
                break

        return candidates

    async def _fetch_page_text(self, client: httpx.AsyncClient, url: str) -> str:
        response = await client.get(url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "img"]):
            tag.decompose()

        main_node = soup.find("main") or soup.find("article") or soup.body or soup
        text = main_node.get_text(" ", strip=True)
        text = " ".join(unescape(text).split())
        return text[:MAX_PAGE_TEXT_CHARS]

    def _normalize_search_result_url(self, raw_href: str) -> str:
        if not raw_href:
            return ""

        parsed = urlparse(raw_href)
        if parsed.netloc.endswith("duckduckgo.com"):
            redirect_url = parse_qs(parsed.query).get("uddg", [""])[0]
            return unquote(redirect_url)

        if raw_href.startswith("http://") or raw_href.startswith("https://"):
            return raw_href

        return ""

    def _extract_domain(self, url: str) -> str:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc

    def _is_allowed_domain(self, domain: str) -> bool:
        return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in self.allowed_domains)

    def _build_excerpt(self, text: str) -> str:
        sentences = [segment.strip() for segment in text.split(". ") if segment.strip()]
        if not sentences:
            return ""
        excerpt = ". ".join(sentences[:2]).strip()
        return excerpt[:280]

    def _build_brief(self, sources: list[WebSource]) -> str:
        lines = ["Contexto web consultado con fuentes externas confiables:"]
        for index, source in enumerate(sources, start=1):
            lines.append(f"{index}. {source.domain}: {source.title}")
            lines.append(f"   URL: {source.url}")
        return "\n".join(lines)