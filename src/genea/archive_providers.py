from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable

from .archive_cache import ArchiveHttpCache, CachedHttpResponse, is_cacheable_public_url
from .archive_profiles import archive_provider_profile, load_archive_provider_profiles
from .workspace import search_fold, stable_id, utc_now


MAX_METADATA_BYTES = 20 * 1024 * 1024
DEFAULT_USER_AGENT = "genea-local/0.1 (+deterministic archival research)"


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    status: str
    search: bool
    manifest: bool
    download: bool
    authentication: str
    interface: str
    note: str


@dataclass(frozen=True)
class ArchiveQuery:
    query: str = ""
    given_name: str | None = None
    surname: str | None = None
    place: str | None = None
    from_year: int | None = None
    to_year: int | None = None
    event_types: tuple[str, ...] = ()
    endpoint: str | None = None
    base_url: str | None = None
    resource_url: str | None = None
    start_urls: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ArchiveQuery":
        allowed = {
            "query", "given_name", "surname", "place", "from_year", "to_year",
            "event_types", "endpoint", "base_url", "resource_url", "start_urls", "options",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"Nieznane pola specyfikacji kwerendy: {', '.join(unknown)}")
        event_types = value.get("event_types") or []
        start_urls = value.get("start_urls") or []
        options = value.get("options") or {}
        if not isinstance(event_types, list) or not all(isinstance(item, str) for item in event_types):
            raise ValueError("event_types musi być listą tekstów")
        if not isinstance(start_urls, list) or not all(isinstance(item, str) for item in start_urls):
            raise ValueError("start_urls musi być listą adresów URL")
        if not isinstance(options, dict):
            raise ValueError("options musi być obiektem JSON")
        return cls(
            **{key: value[key] for key in allowed if key in value and key not in {"event_types", "start_urls", "options"}},
            event_types=tuple(event_types),
            start_urls=tuple(start_urls),
            options=options,
        )

    def search_text(self) -> str:
        return " ".join(
            str(item) for item in (
                self.query, self.given_name, self.surname, self.place,
                self.from_year, self.to_year, *self.event_types,
            ) if item not in (None, "")
        )


@dataclass(frozen=True)
class ArchiveCandidate:
    id: str
    provider: str
    kind: str
    title: str
    source_url: str
    access: str
    locator: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int
    content_type: str | None
    headers: dict[str, str]
    body: bytes
    cache_status: str = "miss"


def _host(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Nieprawidłowy adres HTTP(S): {url}")
    return parsed.hostname.lower()


def _host_allowed(host: str, allowed_hosts: Iterable[str] | None) -> bool:
    if allowed_hosts is None:
        return True
    return any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts)


class HttpClient:
    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 45,
        retries: int = 2,
        sleeper: Callable[[float], None] = time.sleep,
        cache: ArchiveHttpCache | None = None,
        cache_ttl_seconds: int = 0,
        honor_validators: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.sleeper = sleeper
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.honor_validators = honor_validators
        self.cache_events: list[dict[str, str]] = []

    @staticmethod
    def _cached_response(cached: CachedHttpResponse, status: str) -> HttpResponse:
        return HttpResponse(
            url=cached.final_url,
            status=cached.status,
            content_type=cached.content_type,
            headers=cached.headers,
            body=cached.body,
            cache_status=status,
        )

    def get(
        self,
        url: str,
        *,
        accept: str = "application/json, application/xml, text/xml, text/html;q=0.8, */*;q=0.2",
        max_bytes: int = MAX_METADATA_BYTES,
        allowed_hosts: Iterable[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        requested_host = _host(url)
        if not _host_allowed(requested_host, allowed_hosts):
            raise ValueError(f"Host {requested_host} nie jest dozwolony dla tego adaptera")
        supplied_headers = {key.casefold(): value for key, value in (headers or {}).items()}
        cache_allowed = (
            self.cache is not None
            and self.cache_ttl_seconds > 0
            and is_cacheable_public_url(url)
            and not {"authorization", "cookie"}.intersection(supplied_headers)
        )
        cached = self.cache.lookup(url, accept) if cache_allowed and self.cache else None
        if cached and cached.is_fresh(self.cache_ttl_seconds):
            self.cache_events.append({"url": cached.request_url, "status": "hit"})
            return self._cached_response(cached, "hit")

        request_headers = {"User-Agent": self.user_agent, "Accept": accept, **(headers or {})}
        if cached and self.honor_validators:
            if cached.headers.get("etag"):
                request_headers["If-None-Match"] = cached.headers["etag"]
            if cached.headers.get("last-modified"):
                request_headers["If-Modified-Since"] = cached.headers["last-modified"]
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, headers=request_headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    final_url = response.geturl()
                    final_host = _host(final_url)
                    if not _host_allowed(final_host, allowed_hosts):
                        raise ValueError(f"Przekierowanie prowadzi do niedozwolonego hosta {final_host}")
                    declared = response.headers.get("Content-Length")
                    if declared and int(declared) > max_bytes:
                        raise ValueError(f"Odpowiedź przekracza limit {max_bytes} bajtów")
                    body = response.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        raise ValueError(f"Odpowiedź przekracza limit {max_bytes} bajtów")
                    response_headers = {key.lower(): value for key, value in response.headers.items()}
                    result = HttpResponse(
                        url=final_url,
                        status=getattr(response, "status", 200),
                        content_type=response.headers.get_content_type(),
                        headers=response_headers,
                        body=body,
                        cache_status="refresh" if cached else "miss",
                    )
                    if cache_allowed and self.cache:
                        stored = self.cache.store(
                            request_url=url,
                            accept=accept,
                            final_url=final_url,
                            status=result.status,
                            content_type=result.content_type,
                            headers=response_headers,
                            body=body,
                        )
                        status = "refresh" if cached else "miss"
                        self.cache_events.append({"url": stored.request_url, "status": status})
                    return result
            except urllib.error.HTTPError as error:
                if error.code == 304 and cached and self.cache:
                    response_headers = {
                        key.lower(): value
                        for key, value in (error.headers.items() if error.headers else [])
                    }
                    refreshed = self.cache.touch(cached, headers=response_headers)
                    self.cache_events.append({"url": refreshed.request_url, "status": "revalidated"})
                    return self._cached_response(refreshed, "revalidated")
                last_error = error
                if error.code not in {429, 500, 502, 503, 504} or attempt >= self.retries:
                    raise
            except urllib.error.URLError as error:
                last_error = error
                if attempt >= self.retries:
                    raise
            self.sleeper(min(2 ** attempt, 4))
        raise RuntimeError(f"Nie udało się pobrać {url}: {last_error}")


def validate_download_file(path: Path, declared_type: str | None = None) -> str | None:
    """Reject login/challenge HTML and malformed image responses before archiving."""
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Pobrany plik jest pusty")
    with path.open("rb") as handle:
        prefix = handle.read(4096)
    lowered = prefix.lstrip().lower()
    content_type = (declared_type or "").split(";", 1)[0].strip().lower()
    if content_type in {"text/html", "application/xhtml+xml"} or lowered.startswith(
        (b"<!doctype html", b"<html", b"<head", b"<body")
    ):
        raise ValueError("Odpowiedź jest stroną HTML, a nie plikiem źródłowym")
    signatures = (
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"II*\x00", "image/tiff"),
        (b"MM\x00*", "image/tiff"),
        (b"%PDF-", "application/pdf"),
        (b"PK\x03\x04", "application/zip"),
        (b"AT&TFORM", "image/vnd.djvu"),
    )
    detected = next((media_type for signature, media_type in signatures if prefix.startswith(signature)), None)
    suffix = path.suffix.lower()
    if (content_type.startswith("image/") or suffix in {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff"}) and not detected:
        raise ValueError("Plik deklaruje format obrazu, ale nie ma prawidłowej sygnatury")
    if detected and content_type.startswith("image/") and not detected.startswith("image/"):
        raise ValueError(f"Nagłówek {content_type} nie zgadza się z zawartością {detected}")
    if content_type and content_type != "application/octet-stream":
        return content_type
    return detected or content_type or None


def _candidate(provider: str, kind: str, title: str, url: str, **metadata: Any) -> ArchiveCandidate:
    return ArchiveCandidate(
        id=stable_id("archive_candidate", provider, url),
        provider=provider,
        kind=kind,
        title=" ".join(title.split()) or url,
        source_url=url,
        access=metadata.pop("access", "public"),
        locator=metadata.pop("locator", None),
        metadata=metadata,
    )


class ArchiveProvider:
    provider = "base"
    allowed_hosts: tuple[str, ...] | None = None

    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient()

    def discover(self, query: ArchiveQuery) -> list[ArchiveCandidate]:
        raise NotImplementedError

    def manifest(self, resource_url: str) -> dict[str, Any]:
        raise NotImplementedError(f"Adapter {self.provider} nie obsługuje manifestów")

    def validate_resource_url(self, url: str) -> None:
        host = _host(url)
        if not _host_allowed(host, self.allowed_hosts):
            raise ValueError(f"Host {host} nie jest dozwolony dla adaptera {self.provider}")


class DirectHttpProvider(ArchiveProvider):
    provider = "direct-http"

    def discover(self, query: ArchiveQuery) -> list[ArchiveCandidate]:
        urls = query.start_urls or ((query.resource_url,) if query.resource_url else ())
        if not urls:
            raise ValueError("direct-http wymaga resource_url albo start_urls")
        return [_candidate(self.provider, "file", Path(urllib.parse.urlparse(url).path).name, url) for url in urls]


def _json_response(response: HttpResponse) -> dict[str, Any]:
    try:
        value = json.loads(response.body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Nieprawidłowa odpowiedź JSON z {response.url}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Odpowiedź JSON z {response.url} nie jest obiektem")
    return value


def _label(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for entries in value.values():
            if isinstance(entries, list) and entries:
                return str(entries[0])
    return fallback


class IiifProvider(ArchiveProvider):
    provider = "iiif"

    def discover(self, query: ArchiveQuery) -> list[ArchiveCandidate]:
        url = query.resource_url or (query.start_urls[0] if query.start_urls else None)
        if not url:
            raise ValueError("iiif wymaga resource_url")
        manifest = self.manifest(url)
        return [_candidate(self.provider, "iiif-manifest", manifest["label"], url, images=len(manifest["images"]))]

    def manifest(self, resource_url: str) -> dict[str, Any]:
        response = self.client.get(resource_url, accept="application/ld+json, application/json")
        payload = _json_response(response)
        images: list[dict[str, Any]] = []
        if isinstance(payload.get("items"), list):
            for ordinal, canvas in enumerate(payload["items"], 1):
                body = {}
                pages = canvas.get("items") or []
                if pages and (pages[0].get("items") or []):
                    body = pages[0]["items"][0].get("body") or {}
                service = body.get("service") or []
                service = service[0] if isinstance(service, list) and service else service
                service_id = service.get("id") if isinstance(service, dict) else None
                image_url = body.get("id") or (f"{service_id}/full/max/0/default.jpg" if service_id else None)
                if image_url:
                    images.append({
                        "ordinal": ordinal,
                        "label": _label(canvas.get("label"), f"obraz {ordinal}"),
                        "canvas_id": canvas.get("id"),
                        "image_url": image_url,
                        "image_service": service_id,
                    })
        else:
            sequences = payload.get("sequences") or []
            canvases = sequences[0].get("canvases", []) if sequences else []
            for ordinal, canvas in enumerate(canvases, 1):
                annotations = canvas.get("images") or []
                resource = annotations[0].get("resource", {}) if annotations else {}
                service = resource.get("service") or {}
                service_id = service.get("@id") if isinstance(service, dict) else None
                image_url = resource.get("@id") or (f"{service_id}/full/full/0/default.jpg" if service_id else None)
                if image_url:
                    images.append({
                        "ordinal": ordinal,
                        "label": str(canvas.get("label") or f"obraz {ordinal}"),
                        "canvas_id": canvas.get("@id"),
                        "image_url": image_url,
                        "image_service": service_id,
                    })
        return {
            "provider": self.provider,
            "manifest_url": response.url,
            "label": _label(payload.get("label"), response.url),
            "rights": payload.get("rights") or payload.get("license"),
            "images": images,
            "captured_at": utc_now(),
        }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class OaiPmhProvider(ArchiveProvider):
    provider = "oai-pmh"

    def endpoint(self, query: ArchiveQuery) -> str:
        if not query.endpoint:
            raise ValueError("oai-pmh wymaga endpoint")
        return query.endpoint

    def discover(self, query: ArchiveQuery) -> list[ArchiveCandidate]:
        endpoint = self.endpoint(query)
        metadata_prefix = str(query.options.get("metadata_prefix") or "oai_dc")
        max_pages = max(1, min(int(query.options.get("max_pages") or 5), 100))
        parameters = {"verb": "ListRecords", "metadataPrefix": metadata_prefix}
        for key, value in (("from", query.options.get("from")), ("until", query.options.get("until")), ("set", query.options.get("set"))):
            if value:
                parameters[key] = str(value)
        candidates: list[ArchiveCandidate] = []
        wanted = [token for token in search_fold(query.search_text()).split() if len(token) > 1]
        token: str | None = None
        for _ in range(max_pages):
            page_parameters = {"verb": "ListRecords", "resumptionToken": token} if token else parameters
            url = endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode(page_parameters)
            response = self.client.get(url, accept="application/xml, text/xml")
            try:
                root = ET.fromstring(response.body)
            except ET.ParseError as error:
                raise ValueError(f"Nieprawidłowa odpowiedź OAI-PMH z {response.url}") from error
            for record in (item for item in root.iter() if _local_name(item.tag) == "record"):
                header = next((item for item in record if _local_name(item.tag) == "header"), None)
                identifier = next((item.text for item in (header or []) if _local_name(item.tag) == "identifier"), None)
                metadata_element = next((item for item in record if _local_name(item.tag) == "metadata"), None)
                values: dict[str, list[str]] = {}
                if metadata_element is not None:
                    for item in metadata_element.iter():
                        text = " ".join((item.text or "").split())
                        if text:
                            values.setdefault(_local_name(item.tag), []).append(text)
                haystack = search_fold(" ".join(value for entries in values.values() for value in entries))
                if wanted and not all(term in haystack for term in wanted):
                    continue
                links = [value for value in values.get("identifier", []) if value.startswith(("http://", "https://"))]
                source_url = links[0] if links else f"{endpoint}#{identifier or stable_id('oai', haystack)}"
                title = (values.get("title") or [identifier or source_url])[0]
                candidates.append(_candidate(
                    self.provider, "metadata-record", title, source_url,
                    locator=identifier, metadata_format=metadata_prefix, fields=values,
                ))
            token = next((" ".join((item.text or "").split()) for item in root.iter() if _local_name(item.tag) == "resumptionToken"), "")
            if not token:
                break
        return candidates


class DlibraProvider(OaiPmhProvider):
    provider = "dlibra"

    def endpoint(self, query: ArchiveQuery) -> str:
        if query.endpoint:
            return query.endpoint
        if not query.base_url:
            raise ValueError("dlibra wymaga base_url albo endpoint")
        return query.base_url.rstrip("/") + "/dlibra/oai-pmh-repository.xml"


class EuropeanaProvider(ArchiveProvider):
    provider = "europeana"
    allowed_hosts = ("api.europeana.eu",)

    def discover(self, query: ArchiveQuery) -> list[ArchiveCandidate]:
        api_key = os.environ.get("GENEA_EUROPEANA_API_KEY")
        if not api_key:
            raise RuntimeError("Ustaw GENEA_EUROPEANA_API_KEY, aby użyć adaptera Europeana")
        text = query.search_text().strip()
        if not text:
            raise ValueError("Europeana wymaga niepustej kwerendy")
        rows = max(1, min(int(query.options.get("rows") or 50), 100))
        endpoint = query.endpoint or "https://api.europeana.eu/record/v2/search.json"
        parameters = urllib.parse.urlencode({"wskey": api_key, "query": text, "rows": rows})
        response = self.client.get(f"{endpoint}?{parameters}", allowed_hosts=self.allowed_hosts)
        payload = _json_response(response)
        candidates = []
        for item in payload.get("items") or []:
            title_value = item.get("title") or item.get("dcTitle") or [item.get("id")]
            title = title_value[0] if isinstance(title_value, list) else str(title_value)
            links = item.get("edmIsShownAt") or item.get("edmIsShownBy") or []
            url = links[0] if isinstance(links, list) and links else item.get("guid")
            if not url:
                continue
            candidates.append(_candidate(
                self.provider, "aggregated-record", title, url,
                locator=item.get("id"), rights=item.get("rights"), data_provider=item.get("dataProvider"),
            ))
        return candidates


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._anchor_text).strip()))
            self._href = None
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._href is not None:
            self._anchor_text.append(data)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


def _html_cell(value: Any, base_url: str) -> tuple[str, list[str]]:
    parser = _TextParser()
    parser.feed(str(value or ""))
    return " ".join(parser.parts), [urllib.parse.urljoin(base_url, item) for item in parser.links]


class PublicHtmlProvider(ArchiveProvider):
    resource_markers: tuple[tuple[str, str], ...] = ()

    def start_urls(self, query: ArchiveQuery) -> tuple[str, ...]:
        urls = query.start_urls or ((query.resource_url,) if query.resource_url else ())
        if not urls:
            raise ValueError(f"{self.provider} wymaga start_urls albo resource_url")
        return urls

    def discover(self, query: ArchiveQuery) -> list[ArchiveCandidate]:
        max_candidates = max(1, min(int(query.options.get("max_candidates") or 200), 1000))
        found: dict[str, ArchiveCandidate] = {}
        for start_url in self.start_urls(query)[:10]:
            response = self.client.get(start_url, accept="text/html", allowed_hosts=self.allowed_hosts)
            parser = _LinkParser()
            parser.feed(response.body.decode("utf-8", errors="replace"))
            for href, text in parser.links:
                url = urllib.parse.urljoin(response.url, href)
                try:
                    host = _host(url)
                except ValueError:
                    continue
                if not _host_allowed(host, self.allowed_hosts):
                    continue
                kind = next((kind for marker, kind in self.resource_markers if marker in url), None)
                if not kind:
                    continue
                found.setdefault(url, _candidate(
                    self.provider, kind, text or url, url,
                    discovered_from=response.url,
                ))
                if len(found) >= max_candidates:
                    break
        return list(found.values())


class GenealodzyProvider(PublicHtmlProvider):
    provider = "genealodzy"
    allowed_hosts = ("genealodzy.pl",)
    resource_markers = (
        ("metryki.genealodzy.pl/", "scan-or-unit"),
        ("skanoteka.genealodzy.pl/", "scan-or-unit"),
        ("geneteka.genealodzy.pl/index.php?op=gt", "index-result"),
        ("geneteka1.genealodzy.pl/index.php?op=gt", "index-result"),
    )

    def start_urls(self, query: ArchiveQuery) -> tuple[str, ...]:
        if query.start_urls or query.resource_url:
            return super().start_urls(query)
        surname = (query.surname or query.query).strip()
        if not surname:
            raise ValueError("genealodzy wymaga nazwiska/query albo start_urls")
        parameters: dict[str, str] = {
            "op": "gt",
            "lang": "pol",
            "search_lastname": surname,
        }
        if query.given_name:
            parameters["search_name"] = query.given_name
        if query.from_year is not None:
            parameters["from_date"] = str(query.from_year)
        if query.to_year is not None:
            parameters["to_date"] = str(query.to_year)
        event_codes = {"birth": "B", "baptism": "B", "marriage": "S", "death": "D"}
        mapped = {event_codes[item] for item in query.event_types if item in event_codes}
        if len(mapped) == 1:
            parameters["bdm"] = next(iter(mapped))
        if query.options.get("region"):
            parameters["w"] = str(query.options["region"])
        if query.options.get("repository_id"):
            parameters["rid"] = str(query.options["repository_id"])
        return ("https://geneteka.genealodzy.pl/index.php?" + urllib.parse.urlencode(parameters),)

    def discover(self, query: ArchiveQuery) -> list[ArchiveCandidate]:
        if query.start_urls or query.resource_url or query.options.get("html_only"):
            return super().discover(query)
        search_url = self.start_urls(query)[0]
        search_parameters = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(search_url).query))
        event_code = search_parameters.get("bdm") or "B"
        search_parameters.setdefault("rid", str(query.options.get("repository_id") or event_code))
        requested_rows = query.options.get("rows", query.options.get("max_candidates", 25))
        rows = max(1, min(int(requested_rows), 50))
        parameters: list[tuple[str, str]] = list(search_parameters.items())
        parameters.extend((
            ("draw", "1"), ("start", "0"), ("length", str(rows)),
            ("order[0][column]", "0"), ("order[0][dir]", "asc"),
            ("search[value]", ""), ("search[regex]", "false"),
        ))
        for index in range(10):
            parameters.extend((
                (f"columns[{index}][data]", str(index)),
                (f"columns[{index}][name]", ""),
                (f"columns[{index}][searchable]", "true"),
                (f"columns[{index}][orderable]", "false" if index == 9 else "true"),
                (f"columns[{index}][search][value]", ""),
                (f"columns[{index}][search][regex]", "false"),
            ))
        endpoint = "https://geneteka.genealodzy.pl/api/getAct.php?" + urllib.parse.urlencode(parameters)
        response = self.client.get(endpoint, accept="application/json", allowed_hosts=self.allowed_hosts)
        payload = _json_response(response)
        candidates: list[ArchiveCandidate] = []
        for ordinal, row in enumerate(payload.get("data") or [], 1):
            if not isinstance(row, list):
                continue
            texts: list[str] = []
            links: list[str] = []
            for cell in row:
                text, cell_links = _html_cell(cell, search_url)
                texts.append(text)
                links.extend(cell_links)
            preferred_markers = (
                "metryki.genealodzy.pl/", "skanoteka.genealodzy.pl/",
                "szukajwarchiwach.gov.pl/", "familysearch.org/",
            )
            preferred_link = next((link for link in links if any(marker in link for marker in preferred_markers)), None)
            fix_link = next((link for link in links if "geneteka.genealodzy.pl/fix.php" in link), None)
            record_id = None
            if fix_link:
                record_id = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(fix_link).query)).get("gid")
            source_url = preferred_link or search_url + f"#record-{record_id or ordinal}"
            title = " | ".join(item for item in texts[:9] if item)
            locator = "/".join(item for item in texts[:2] if item) or None
            candidates.append(_candidate(
                self.provider,
                "index-record",
                title,
                source_url,
                locator=locator,
                search_url=search_url,
                fields=texts,
                links=links,
                records_total=payload.get("recordsTotal"),
                records_filtered=payload.get("recordsFiltered"),
                index_record_id=record_id,
                interface="undocumented-public-data-endpoint",
            ))
        if not candidates and int(payload.get("recordsFiltered") or 0) > 0:
            candidates.append(_candidate(
                self.provider,
                "index-query-summary",
                f"Geneteka: {payload.get('recordsFiltered')} rekordów; szczegóły odpowiedzi puste",
                search_url,
                access="partial",
                records_total=payload.get("recordsTotal"),
                records_filtered=payload.get("recordsFiltered"),
                interface="undocumented-public-data-endpoint",
            ))
        return candidates


class SzukajWArchiwachProvider(PublicHtmlProvider):
    provider = "szukajwarchiwach"
    allowed_hosts = ("szukajwarchiwach.gov.pl",)
    resource_markers = (
        ("/zespol/-/zespol/", "fonds"),
        ("/jednostka/-/jednostka/", "unit"),
        ("/skan/-/skan/", "scan"),
    )


CAPABILITIES = (
    ProviderCapabilities("direct-http", "available", False, False, True, "none/provider-specific", "HTTP(S)", "Znany publiczny URL."),
    ProviderCapabilities("iiif", "available", False, True, True, "IIIF Auth when published", "official-api", "Manifesty Presentation 2/3 i obrazy."),
    ProviderCapabilities("oai-pmh", "available", True, False, False, "usually none", "official-protocol", "Deterministyczny harvest metadanych."),
    ProviderCapabilities("dlibra", "available", True, False, True, "provider-specific", "official-protocol", "OAI-PMH plus odsyłacze do obiektów dLibra."),
    ProviderCapabilities("europeana", "available", True, False, False, "api-key", "official-api", "Agregator metadanych i linków do dostawców."),
    ProviderCapabilities(
        "genealodzy",
        "available-conservative",
        True,
        False,
        True,
        "public/provider-specific",
        "undocumented-public-data-endpoint/html",
        "Ograniczony publiczny endpoint i fallback HTML; bez masowego crawlu.",
    ),
    ProviderCapabilities("szukajwarchiwach", "available-conservative", True, False, True, "public/session-fallback", "public-html", "Publiczne opisy; obrazy mogą wymagać legalnej sesji."),
    ProviderCapabilities("familysearch", "nice-to-have", True, False, False, "OAuth/app-approval", "official-api; adapter-planned", "Poza zakresem bieżącej iteracji."),
)


PROVIDER_TYPES = {
    "direct-http": DirectHttpProvider,
    "iiif": IiifProvider,
    "oai-pmh": OaiPmhProvider,
    "dlibra": DlibraProvider,
    "europeana": EuropeanaProvider,
    "genealodzy": GenealodzyProvider,
    "szukajwarchiwach": SzukajWArchiwachProvider,
}


def list_archive_providers(root: Path | None = None) -> list[dict[str, Any]]:
    profiles = load_archive_provider_profiles(root) if root is not None else {}
    return [
        {
            **asdict(item),
            **({"profile": profiles[item.provider].summary()} if item.provider in profiles else {}),
        }
        for item in CAPABILITIES
    ]


def archive_provider(
    name: str,
    client: HttpClient | None = None,
    *,
    root: Path | None = None,
) -> ArchiveProvider:
    provider_type = PROVIDER_TYPES.get(name)
    if provider_type is None:
        status = next((item for item in CAPABILITIES if item.provider == name), None)
        if status and status.status == "nice-to-have":
            raise NotImplementedError(f"Adapter {name} ma status nice-to-have")
        raise KeyError(f"Nieznany adapter archiwum: {name}")
    profile = archive_provider_profile(root, name) if root is not None else None
    if client is None and profile is not None:
        cache = None
        if profile.cache_enabled:
            cache = ArchiveHttpCache(Path(root) / "research" / "cache" / "archive-http" / name)
        client = HttpClient(
            cache=cache,
            cache_ttl_seconds=profile.cache_ttl_seconds,
            honor_validators=profile.honor_validators,
        )
    provider = provider_type(client)
    if profile and provider.allowed_hosts is not None:
        missing = sorted(set(provider.allowed_hosts) - set(profile.allowed_hosts))
        if missing:
            raise ValueError(f"Profil {name} nie obejmuje hostów adaptera: {', '.join(missing)}")
    return provider


def discover_archive(
    provider_name: str,
    specification: dict[str, Any],
    client: HttpClient | None = None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    query = ArchiveQuery.from_dict(specification)
    provider = archive_provider(provider_name, client, root=root)
    candidates = provider.discover(query)
    safe_specification = dict(specification)
    if isinstance(safe_specification.get("options"), dict):
        safe_specification["options"] = {
            key: value for key, value in safe_specification["options"].items()
            if "key" not in key.casefold() and "token" not in key.casefold() and "password" not in key.casefold()
        }
    profile = archive_provider_profile(root, provider_name) if root is not None else None
    return {
        "schema_version": 1,
        "provider": provider_name,
        "query": safe_specification,
        "candidate_count": len(candidates),
        "candidates": [asdict(item) for item in candidates],
        "generated_at": utc_now(),
        "transport": {
            "profileVersion": profile.profile_version if profile else None,
            "cacheEvents": list(getattr(provider.client, "cache_events", [])),
        },
        "warning": "Brak wyniku nie jest dowodem negatywnym bez udokumentowanej kompletności pokrycia.",
    }


def archive_manifest(
    provider_name: str,
    resource_url: str,
    client: HttpClient | None = None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    return archive_provider(provider_name, client, root=root).manifest(resource_url)
