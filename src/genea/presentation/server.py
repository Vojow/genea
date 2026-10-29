from __future__ import annotations

import os
import json
import ipaddress
import re
import subprocess
import threading
import time
import webbrowser
from contextlib import ExitStack, closing, contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..private_web import MAX_BODY_SIZE, PrivateApiApplication, PrivateResponse, PrivateSessionManager
from ..workspace import dump_json, sha256_file
from .media import LOCAL_ARTIFACT_PREFIX, local_originals
from .payload import FRONTEND_SCHEMA_VERSION, build_frontend_payload, open_readonly_connection
from .privacy import PrivacyContext
from .volume_indexes import project_volume_index_page, served_volume_index_renders
from .reading_verification import serve_reading_verification
from .research_telemetry import TelemetryError, project_research_telemetry
from .ui_store import publish as publish_ui_payload
from .backup_policy import maintain_backups


_EXPORT_LOCK = threading.Lock()


class _LimitedFileReader:
    def __init__(self, file: Any, length: int):
        self.file = file
        self.remaining = length

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        requested = self.remaining if size < 0 else min(size, self.remaining)
        data = self.file.read(requested)
        self.remaining -= len(data)
        return data

    def close(self) -> None:
        self.file.close()


def served_originals(root: Path) -> dict[str, dict[str, Any]]:
    with closing(open_readonly_connection(root)) as connection:
        privacy = PrivacyContext.load(connection)
        restricted_sources = privacy.restricted_ids("source_item")
        allowed_source_ids = {
            row["id"]
            for row in connection.execute("SELECT id FROM source_items")
            if row["id"] not in restricted_sources
        }
        _, served = local_originals(root, connection, allowed_source_ids)
        return served


class FrontendRequestHandler(SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        artifact_files: dict[str, dict[str, Any]],
        volume_index_files: dict[str, dict[str, Any]],
        data_file: Path,
        project_root: Path | None = None,
        private_api: PrivateApiApplication | None = None,
        trusted_proxy_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (),
        require_trusted_proxy: bool = False,
        **kwargs: Any,
    ):
        self.artifact_files = artifact_files
        self.volume_index_files = volume_index_files
        self.data_file = data_file
        self.private_api = private_api
        self.project_root = project_root
        self.trusted_proxy_networks = trusted_proxy_networks
        self.require_trusted_proxy = require_trusted_proxy
        super().__init__(*args, **kwargs)

    def end_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "worker-src 'self' blob:; connect-src 'self'; font-src 'self' data:",
        )
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def _client_is_trusted_proxy(self) -> bool:
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return any(address in network for network in self.trusted_proxy_networks)

    def _send_private_response(self, response):  # type: ignore[no-untyped-def]
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in response.headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(response.body)

    def _handle_private(self, method: str) -> bool:
        if not urlsplit(self.path).path.startswith("/api/private/"):
            return False
        private_api = getattr(self, "private_api", None)
        if private_api is None:
            if method == "GET" and urlsplit(self.path).path == "/api/private/status":
                self._send_private_response(
                    PrivateResponse(
                        200,
                        b'{"enabled":false,"authenticated":false,"strict":false,"expiresIn":0,"locked":false}',
                    )
                )
                return True
            self.send_error(404)
            return True
        proxy_trusted = self._client_is_trusted_proxy()
        if self.require_trusted_proxy and not proxy_trusted:
            self.send_error(403, "Private API requires a trusted reverse proxy")
            return True
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError:
            self.send_error(400)
            return True
        if length < 0 or length > MAX_BODY_SIZE:
            self.send_error(413)
            return True
        body = self.rfile.read(length) if length else b""
        safe_headers = {name: value for name, value in self.headers.items()}
        if not proxy_trusted:
            safe_headers.pop("X-Forwarded-Proto", None)
            safe_headers.pop("X-Forwarded-For", None)
            safe_headers.pop("Forwarded", None)
        response = private_api.handle(
            method,
            self.path,
            safe_headers,
            body,
            self.client_address[0],
        )
        self._send_private_response(response)
        return True

    def do_GET(self) -> None:
        if self._handle_private("GET"):
            return
        if self._handle_presentation_api():
            return
        super().do_GET()

    def _handle_presentation_api(self) -> bool:
        split = urlsplit(self.path)
        if split.path == "/api/presentation/research-telemetry":
            return self._handle_research_telemetry(split.query)
        prefix = "/api/presentation/volume-index/"
        if not split.path.startswith(prefix) or not split.path.endswith("/candidates"):
            return False
        root = getattr(self, "project_root", None)
        if root is None:
            self.send_error(404)
            return True
        index_id = unquote(split.path.removeprefix(prefix).removesuffix("/candidates").rstrip("/"))
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", index_id):
            self.send_error(404)
            return True
        query = parse_qs(split.query)
        try:
            cursor = int(query.get("cursor", ["0"])[0])
            limit = int(query.get("limit", ["50"])[0])
            body = json.dumps(
                project_volume_index_page(root, index_id, cursor=cursor, limit=limit),
                ensure_ascii=False,
            ).encode("utf-8")
        except (ValueError, LookupError, FileNotFoundError):
            self.send_error(404)
            return True
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)
        return True

    def _handle_research_telemetry(self, query_string: str) -> bool:
        root = getattr(self, "project_root", None)
        if root is None:
            self._send_private_response(PrivateResponse(404, b'{"error":"not_found"}'))
            return True
        query = parse_qs(query_string, keep_blank_values=True)
        allowed_keys = {"batchId", "scopeKind", "scopeId"}
        if (not set(query).issubset(allowed_keys)
                or any(len(values) != 1 or not values[0] for values in query.values())
                or ("scopeKind" in query) != ("scopeId" in query)):
            self._send_private_response(PrivateResponse(400, b'{"error":"invalid_filters"}'))
            return True
        try:
            # The existing controlled model supplies the only label/ID allowlist.
            # Telemetry is never appended to it or materialized in SQLite.
            with self.data_file.open("r", encoding="utf-8") as handle:
                presentation = json.load(handle)
            if not isinstance(presentation, dict):
                raise ValueError("invalid presentation model")
            payload = project_research_telemetry(
                root, presentation,
                batch_id=query.get("batchId", [None])[0],
                scope_kind=query.get("scopeKind", [None])[0],
                scope_id=query.get("scopeId", [None])[0],
            )
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TelemetryError, OSError, ValueError):
            self._send_private_response(PrivateResponse(400, b'{"error":"telemetry_unavailable"}'))
            return True
        self._send_private_response(PrivateResponse(200, body))
        return True

    def do_POST(self) -> None:
        if self._handle_private("POST"):
            return
        self.send_error(405)

    def copyfile(self, source: Any, outputfile: Any) -> None:
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            # MapLibre anuluje nieaktualne żądania Range podczas szybkiego zoomu.
            return

    def _send_local_file(
        self,
        path: Path,
        *,
        media_type: str,
        cache_control: str,
        extra_headers: dict[str, str] | None = None,
    ):  # type: ignore[no-untyped-def]
        try:
            file = path.open("rb")
        except OSError:
            self.send_error(404)
            return None
        stat = os.fstat(file.fileno())
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        return file

    def _send_range_file(
        self,
        path: Path,
        *,
        media_type: str,
        cache_control: str,
    ):  # type: ignore[no-untyped-def]
        try:
            file = path.open("rb")
        except OSError:
            self.send_error(404)
            return None
        size = os.fstat(file.fileno()).st_size
        range_header = self.headers.get("Range")
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header or "")
        if not match:
            file.close()
            return self._send_local_file(
                path, media_type=media_type, cache_control=cache_control
            )
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else size - 1
        if start >= size or end < start:
            file.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        end = min(end, size - 1)
        file.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        return _LimitedFileReader(file, end - start + 1)

    def send_head(self):  # type: ignore[no-untyped-def]
        request_path = unquote(urlsplit(self.path).path)
        if request_path in {"", "/", "/index.html"}:
            return self._send_local_file(
                Path(self.directory) / "index.html",
                media_type="text/html; charset=utf-8",
                cache_control="private, no-store",
            )
        if request_path == "/data/research.json":
            return self._send_local_file(
                self.data_file,
                media_type="application/json; charset=utf-8",
                cache_control="private, no-store",
            )
        if request_path == "/sw.js":
            return self._send_local_file(
                Path(self.directory) / "sw.js",
                media_type="text/javascript; charset=utf-8",
                cache_control="private, no-cache, max-age=0",
                extra_headers={"Service-Worker-Allowed": "/"},
            )
        if request_path == "/manifest.webmanifest":
            return self._send_local_file(
                Path(self.directory) / "manifest.webmanifest",
                media_type="application/manifest+json; charset=utf-8",
                cache_control="private, no-cache, max-age=0",
            )
        if request_path == "/map/genea-basemap.pmtiles":
            return self._send_range_file(
                Path(self.directory) / "map" / "genea-basemap.pmtiles",
                media_type="application/vnd.pmtiles",
                cache_control="private, no-store",
            )
        if request_path.startswith("/local-volume-index/"):
            parts = request_path.removeprefix("/local-volume-index/").split("/")
            if len(parts) != 2 or not parts[0] or not parts[1].isdigit():
                self.send_error(404)
                return None
            item = self.volume_index_files.get(f"{parts[0]}:{int(parts[1])}")
            if not item:
                self.send_error(404)
                return None
            return self._send_local_file(
                item["path"],
                media_type=item["mediaType"],
                cache_control="private, no-store",
            )
        if self._is_spa_route(request_path):
            return self._send_local_file(
                Path(self.directory) / "index.html",
                media_type="text/html; charset=utf-8",
                cache_control="private, no-store",
            )
        if not request_path.startswith(LOCAL_ARTIFACT_PREFIX):
            return super().send_head()
        artifact_id = request_path[len(LOCAL_ARTIFACT_PREFIX) :]
        if not artifact_id or "/" in artifact_id:
            self.send_error(404)
            return None
        artifact = self.artifact_files.get(artifact_id)
        if not artifact:
            self.send_error(404)
            return None
        return self._send_local_file(
            artifact["path"],
            media_type=artifact["mediaType"],
            cache_control="private, no-store",
        )

    @staticmethod
    def _is_spa_route(request_path: str) -> bool:
        """Allow fallback only for known client-side route families."""

        route_roots = {
            "/catalog",
            "/tree",
            "/map",
            "/graph",
            "/sources",
            "/research",
            "/people",
            "/lines",
            "/places",
            "/events",
            "/households",
            "/clusters",
        }
        first = "/" + request_path.lstrip("/").split("/", 1)[0]
        return first in route_roots and "." not in request_path.rsplit("/", 1)[-1]


def export_frontend(root: Path, destination: Path | None = None) -> dict[str, Any]:
    destination = destination or root / "frontend" / "data" / "research.json"
    started = time.perf_counter()
    with _EXPORT_LOCK:
        payload = build_frontend_payload(root)
        if destination == root / ".genea" / "ui-store" / "current.json":
            publish_ui_payload(root, payload)
        else:
            dump_json(destination, payload)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "path": str(destination),
        "schema_version": FRONTEND_SCHEMA_VERSION,
        "generated_at": payload["meta"]["generatedAt"],
        "payload_sha256": sha256_file(destination),
        "payload_bytes": destination.stat().st_size,
        "duration_ms": elapsed_ms,
        "counts": payload["meta"]["counts"],
        "excluded_objects": payload["meta"]["privacy"]["excludedObjects"],
    }


def build_frontend_assets(root: Path) -> Path:
    """Build the Vue application without placing generated assets in Git."""

    try:
        subprocess.run(
            ["npm", "run", "build:ui"],
            cwd=root,
            check=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "Brak npm; zainstaluj Node.js i uruchom `npm install`."
        ) from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "Build Vue nie powiódł się; uruchom `npm run build:ui` i popraw błędy."
        ) from error
    destination = root / "frontend" / "dist"
    if not (destination / "index.html").is_file():
        raise RuntimeError("Build Vue nie utworzył frontend/dist/index.html")
    return destination


def build_reading_verification_assets(root: Path) -> Path:
    """Build only the isolated blind-review entrypoint and its static assets."""

    try:
        subprocess.run(
            ["npm", "run", "build:ui:blind"],
            cwd=root,
            check=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "Brak npm; zainstaluj Node.js i uruchom `npm install`."
        ) from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "Build blind review nie powiódł się; uruchom `npm run build:ui:blind` i popraw błędy."
        ) from error
    destination = root / "output" / "blind-review"
    if not (destination / "blind-review" / "index.html").is_file():
        raise RuntimeError(
            "Build blind review nie utworzył output/blind-review/blind-review/index.html"
        )
    return destination


def frontend_browser_url(port: int, generated_at: str) -> str:
    """Return a fresh navigation URL without exposing payload data."""

    build_token = quote(generated_at, safe="")
    return f"http://127.0.0.1:{port}/?build={build_token}"


@contextmanager
def _startup_phase(label: str, *, heartbeat_seconds: float = 5.0):
    """Report waiting time, without claiming progress within a blocking operation."""
    started = time.monotonic()
    stopped = threading.Event()

    def report_waiting() -> None:
        while not stopped.wait(heartbeat_seconds):
            elapsed = time.monotonic() - started
            print(
                f"Genea UI: nadal trwa {label}; minęło {elapsed:.0f} s. "
                "Nadal przygotowuję uruchomienie; Ctrl+C przerywa.",
                flush=True,
            )

    print(f"Genea UI: {label} — start. Ctrl+C przerywa.", flush=True)
    heartbeat = threading.Thread(
        target=report_waiting, name="genea-startup-status", daemon=True
    )
    heartbeat.start()
    try:
        try:
            yield
        finally:
            stopped.set()
            heartbeat.join()
    except BaseException as error:
        status = "przerwano" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "błąd"
        print(
            f"Genea UI: {status} podczas etapu: {label}; "
            f"minęło {time.monotonic() - started:.0f} s.",
            flush=True,
        )
        raise
    else:
        print(
            f"Genea UI: zakończono etap: {label}; "
            f"minęło {time.monotonic() - started:.0f} s.",
            flush=True,
        )


def serve_frontend(
    root: Path,
    *,
    port: int = 8765,
    open_browser: bool = True,
    host: str = "127.0.0.1",
    prebuilt_assets: Path | None = None,
    private_api: bool = False,
    trusted_private_proxy: bool = False,
    trusted_proxy_cidrs: tuple[str, ...] = (),
) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("port musi mieścić się w zakresie 1–65535")
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("host musi być 127.0.0.1 albo 0.0.0.0")
    try:
        trusted_proxy_networks = tuple(
            ipaddress.ip_network(value, strict=False) for value in trusted_proxy_cidrs
        )
    except ValueError as error:
        raise ValueError(f"Nieprawidłowy trusted proxy CIDR: {error}") from error
    if private_api and host == "0.0.0.0" and not trusted_proxy_networks:
        raise ValueError(
            "Prywatne API na 0.0.0.0 wymaga co najmniej jednego --trusted-proxy-cidr"
        )
    with ExitStack() as stack:
        with _startup_phase("weryfikacja kopii i retencji (maksymalnie 7)"):
            maintain_backups(root)
        if prebuilt_assets is None:
            data_file = root / ".genea" / "ui-store" / "current.json"
            with _startup_phase("świeży eksport danych (może potrwać długo)"):
                export_result = export_frontend(root, data_file)
            with _startup_phase("budowanie aplikacji Vue"):
                frontend_directory = build_frontend_assets(root)
        else:
            with _startup_phase("przygotowanie gotowych zasobów"):
                frontend_directory = Path(prebuilt_assets).expanduser().resolve()
                if not (frontend_directory / "index.html").is_file():
                    raise FileNotFoundError(
                        f"Katalog prebuilt nie zawiera index.html: {frontend_directory}"
                    )
                data_file = root / ".genea" / "ui-store" / "current.json"
            with _startup_phase("świeży eksport danych (może potrwać długo)"):
                export_result = export_frontend(root, data_file)
        with _startup_phase("przygotowanie serwera"):
            private_application = None
            if private_api:
                private_application = PrivateApiApplication(
                    root,
                    PrivateSessionManager(root),
                    refresh_presentation=lambda: export_frontend(root, data_file),
                )
            handler = partial(
                FrontendRequestHandler,
                artifact_files=served_originals(root),
                volume_index_files=served_volume_index_renders(root),
                data_file=data_file,
                private_api=private_application,
                project_root=root,
                trusted_proxy_networks=trusted_proxy_networks,
                require_trusted_proxy=private_api and host == "0.0.0.0",
                directory=str(frontend_directory),
            )
            server = ThreadingHTTPServer((host, port), handler)
        display_host = "127.0.0.1" if host == "0.0.0.0" else host
        url = f"http://{display_host}:{port}/"
        if host == "0.0.0.0":
            print(
                "UWAGA: serwer nasłuchuje na wszystkich interfejsach; "
                "używaj wyłącznie za reverse proxy i kontrolą dostępu."
            )
        print(f"Genea UI: {url} (Ctrl+C kończy serwer)", flush=True)
        if open_browser:
            webbrowser.open(frontend_browser_url(port, export_result["generated_at"]))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
