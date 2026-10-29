from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROFILE_SCHEMA_VERSION = 1
HEADLESS_STATUSES = {"supported", "partial", "blocked", "session-required"}
INTERFACES = {"official-api", "public-data-endpoint", "public-html", "browser-only"}
SECRET_MARKERS = {"authorization", "cookie", "password", "secret", "token"}


@dataclass(frozen=True)
class ArchiveProviderProfile:
    provider: str
    profile_version: int
    authentication: str
    allowed_hosts: tuple[str, ...]
    cache_enabled: bool
    cache_ttl_seconds: int
    honor_validators: bool
    requests_per_minute: int
    surfaces: tuple[dict[str, Any], ...]
    source_path: Path

    def summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "profileVersion": self.profile_version,
            "authentication": self.authentication,
            "allowedHosts": list(self.allowed_hosts),
            "cache": {
                "enabled": self.cache_enabled,
                "ttlSeconds": self.cache_ttl_seconds,
                "honorValidators": self.honor_validators,
            },
            "requestsPerMinute": self.requests_per_minute,
            "surfaces": [
                {
                    "id": surface["id"],
                    "interface": surface["interface"],
                    "headlessStatus": surface["ui"]["headlessStatus"],
                    "lastVerifiedAt": surface["ui"].get("lastVerifiedAt"),
                }
                for surface in self.surfaces
            ],
        }


def _profile_directory(root: Path) -> Path:
    return Path(root).resolve() / "config" / "archive-providers"


def _validate_url(url: object, allowed_hosts: tuple[str, ...], label: str) -> str:
    if not isinstance(url, str):
        raise ValueError(f"{label} musi być adresem URL")
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise ValueError(f"{label} musi używać pełnego adresu HTTPS")
    if not any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts):
        raise ValueError(f"{label} prowadzi do hosta spoza profilu: {host}")
    return url


def _reject_secret_fields(value: object, path: str = "profile") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            folded = str(key).casefold()
            if any(marker in folded for marker in SECRET_MARKERS):
                raise ValueError(f"Profil nie może zawierać pola sekretu: {path}.{key}")
            _reject_secret_fields(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_secret_fields(nested, f"{path}[{index}]")


def parse_archive_profile(payload: dict[str, Any], source_path: Path) -> ArchiveProviderProfile:
    allowed = {
        "schemaVersion", "provider", "profileVersion", "authentication",
        "allowedHosts", "cache", "rateLimit", "surfaces",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"Nieznane pola profilu {source_path.name}: {', '.join(unknown)}")
    _reject_secret_fields(payload)
    if payload.get("schemaVersion") != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"Nieobsługiwana wersja profilu {source_path.name}")
    provider = payload.get("provider")
    if not isinstance(provider, str) or not provider or provider != source_path.stem:
        raise ValueError(f"Provider musi odpowiadać nazwie pliku {source_path.name}")
    profile_version = payload.get("profileVersion")
    if not isinstance(profile_version, int) or profile_version < 1:
        raise ValueError("profileVersion musi być dodatnią liczbą całkowitą")
    authentication = payload.get("authentication")
    if not isinstance(authentication, str) or not authentication:
        raise ValueError("authentication musi być niepustym tekstem")
    hosts = payload.get("allowedHosts")
    if not isinstance(hosts, list) or not hosts or not all(isinstance(item, str) and item for item in hosts):
        raise ValueError("allowedHosts musi być niepustą listą hostów")
    allowed_hosts = tuple(sorted({item.casefold() for item in hosts}))

    cache = payload.get("cache")
    if not isinstance(cache, dict) or set(cache) != {"enabled", "ttlSeconds", "honorValidators"}:
        raise ValueError("cache wymaga pól enabled, ttlSeconds i honorValidators")
    cache_enabled = cache["enabled"]
    ttl = cache["ttlSeconds"]
    honor = cache["honorValidators"]
    if not isinstance(cache_enabled, bool) or not isinstance(honor, bool):
        raise ValueError("Flagi cache muszą być logiczne")
    if not isinstance(ttl, int) or ttl < 0 or ttl > 31_536_000:
        raise ValueError("cache.ttlSeconds musi mieścić się w zakresie 0..31536000")
    if cache_enabled and authentication not in {"none", "public", "public-provider-specific"}:
        raise ValueError("Trwały cache jest dozwolony wyłącznie dla publicznego profilu")

    rate = payload.get("rateLimit")
    if not isinstance(rate, dict) or set(rate) != {"requestsPerMinute"}:
        raise ValueError("rateLimit wymaga pola requestsPerMinute")
    requests_per_minute = rate["requestsPerMinute"]
    if not isinstance(requests_per_minute, int) or not 1 <= requests_per_minute <= 120:
        raise ValueError("requestsPerMinute musi mieścić się w zakresie 1..120")

    raw_surfaces = payload.get("surfaces")
    if not isinstance(raw_surfaces, list) or not raw_surfaces:
        raise ValueError("surfaces musi być niepustą listą")
    surfaces: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in raw_surfaces:
        if not isinstance(raw, dict) or set(raw) != {"id", "interface", "entryUrls", "api", "ui"}:
            raise ValueError("Każda powierzchnia wymaga id, interface, entryUrls, api i ui")
        surface_id = raw["id"]
        if not isinstance(surface_id, str) or not surface_id or surface_id in seen_ids:
            raise ValueError("Id powierzchni musi być niepuste i unikalne")
        seen_ids.add(surface_id)
        if raw["interface"] not in INTERFACES:
            raise ValueError(f"Nieobsługiwany interface powierzchni {surface_id}")
        entry_urls = raw["entryUrls"]
        if not isinstance(entry_urls, list) or not entry_urls:
            raise ValueError(f"Powierzchnia {surface_id} wymaga entryUrls")
        for index, url in enumerate(entry_urls):
            _validate_url(url, allowed_hosts, f"{surface_id}.entryUrls[{index}]")
        api = raw["api"]
        if api is not None:
            if not isinstance(api, dict) or set(api) != {"method", "endpoint", "response", "pagination"}:
                raise ValueError(f"Nieprawidłowy kontrakt API powierzchni {surface_id}")
            if api["method"] != "GET":
                raise ValueError("Profile publiczne obsługują obecnie tylko bezpieczny GET")
            _validate_url(api["endpoint"], allowed_hosts, f"{surface_id}.api.endpoint")
        ui = raw["ui"]
        if not isinstance(ui, dict) or set(ui) != {
            "headlessStatus", "lastVerifiedAt", "semanticAnchors", "successConditions", "fallback",
        }:
            raise ValueError(f"Nieprawidłowa receptura UI powierzchni {surface_id}")
        if ui["headlessStatus"] not in HEADLESS_STATUSES:
            raise ValueError(f"Nieprawidłowy headlessStatus powierzchni {surface_id}")
        anchors = ui["semanticAnchors"]
        if not isinstance(anchors, list) or not all(
            isinstance(item, dict) and set(item) == {"role", "name"}
            and isinstance(item["role"], str) and isinstance(item["name"], str)
            for item in anchors
        ):
            raise ValueError(f"semanticAnchors powierzchni {surface_id} są nieprawidłowe")
        conditions = ui["successConditions"]
        if not isinstance(conditions, list) or not all(isinstance(item, str) for item in conditions):
            raise ValueError(f"successConditions powierzchni {surface_id} są nieprawidłowe")
        if not isinstance(ui["fallback"], str) or not ui["fallback"]:
            raise ValueError(f"Powierzchnia {surface_id} wymaga fallbacku")
        surfaces.append(raw)

    return ArchiveProviderProfile(
        provider=provider,
        profile_version=profile_version,
        authentication=authentication,
        allowed_hosts=allowed_hosts,
        cache_enabled=cache_enabled,
        cache_ttl_seconds=ttl,
        honor_validators=honor,
        requests_per_minute=requests_per_minute,
        surfaces=tuple(surfaces),
        source_path=source_path,
    )


def load_archive_provider_profiles(root: Path) -> dict[str, ArchiveProviderProfile]:
    directory = _profile_directory(root)
    if not directory.is_dir():
        return {}
    profiles: dict[str, ArchiveProviderProfile] = {}
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Profil {path.name} nie jest obiektem JSON")
        profile = parse_archive_profile(payload, path)
        profiles[profile.provider] = profile
    return profiles


def archive_provider_profile(root: Path, provider: str) -> ArchiveProviderProfile | None:
    return load_archive_provider_profiles(root).get(provider)


__all__ = [
    "ArchiveProviderProfile",
    "archive_provider_profile",
    "load_archive_provider_profiles",
    "parse_archive_profile",
]
