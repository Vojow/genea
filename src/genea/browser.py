from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .archive import add_source
from .workspace import safe_filename, utc_now


def playwright_cli() -> Path | str:
    explicit = os.environ.get("PWCLI")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    installed = shutil.which("playwright-cli")
    if installed:
        return installed
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    wrapper = codex_home / "skills" / "playwright" / "scripts" / "playwright_cli.sh"
    if wrapper.is_file():
        return wrapper
    raise RuntimeError(
        "Nie znaleziono playwright-cli ani skryptu umiejętności Playwright. "
        "Zainstaluj @playwright/cli lub ustaw PWCLI."
    )


def _run(session: str, *args: str, capture: bool = False) -> subprocess.CompletedProcess:
    command = [str(playwright_cli()), f"-s={session}", *args]
    return subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=capture,
    )


def open_browser(root: Path, url: str, session: str = "genea") -> None:
    profile = root / "research" / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    _run(
        session,
        "open",
        url,
        "--headed",
        "--persistent",
        "--profile",
        str(profile),
    )


def capture_browser(
    root: Path,
    *,
    source_url: str,
    title: str | None = None,
    service: str | None = None,
    session: str = "genea",
    full_page: bool = False,
    rights_note: str | None = None,
) -> dict:
    temporary = root / "research" / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().replace(":", "-")
    filename = safe_filename(title or "browser-capture") + f"-{stamp}.png"
    screenshot = temporary / filename
    snapshot = _run(session, "snapshot", capture=True).stdout
    screenshot_args = ["screenshot", "--filename", str(screenshot), "--hires"]
    if full_page:
        screenshot_args.append("--full-page")
    _run(session, *screenshot_args)
    record = add_source(
        root,
        str(screenshot),
        title=title,
        service=service,
        source_url=source_url,
        rights_note=rights_note,
        kind="browser-capture",
        metadata={"playwright_session": session, "full_page": full_page},
    )
    capture_dir = root / "research" / "archive" / "browser" / record["id"]
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "snapshot.txt").write_text(snapshot, encoding="utf-8")
    screenshot.unlink(missing_ok=True)
    return record

