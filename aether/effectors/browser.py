"""Browser automation via Playwright (§6.4).

Gracefully degrades when playwright is not installed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_PLAYWRIGHT_OK = False
try:
    from playwright.sync_api import sync_playwright, Browser, Page, Playwright
    _PLAYWRIGHT_OK = True
except ImportError:
    Browser = Page = Playwright = Any  # type: ignore[misc, assignment]


@dataclass
class BrowserSession:
    """Singleton-ish browser session reused across tool calls in one agent run."""

    headless: bool = True
    _pw: Any = field(default=None, repr=False)
    _browser: Any = field(default=None, repr=False)
    _page: Any = field(default=None, repr=False)

    def available(self) -> bool:
        return _PLAYWRIGHT_OK

    def ensure_page(self) -> Any:
        if not _PLAYWRIGHT_OK:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && "
                "playwright install chromium"
            )
        if self._page is not None:
            return self._page
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._page = self._browser.new_page()
        return self._page

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._page = self._browser = self._pw = None


# Module-level session for the current agent run
_session: BrowserSession | None = None


def get_session(headless: bool = True) -> BrowserSession:
    global _session
    if _session is None:
        _session = BrowserSession(headless=headless)
    return _session


def close_session() -> None:
    global _session
    if _session is not None:
        _session.close()
        _session = None


def navigate(url: str, headless: bool = True) -> str:
    page = get_session(headless=headless).ensure_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    title = page.title()
    return f"Navigated to {url} (title: {title})"


def click(selector: str) -> str:
    page = get_session().ensure_page()
    page.click(selector, timeout=10000)
    return f"Clicked {selector}"


def fill(selector: str, text: str) -> str:
    page = get_session().ensure_page()
    page.fill(selector, text, timeout=10000)
    return f"Filled {selector} with {len(text)} chars"


def get_text(selector: str) -> str:
    page = get_session().ensure_page()
    el = page.query_selector(selector)
    if el is None:
        return f"Element not found: {selector}"
    text = el.inner_text()
    return f"Text from {selector}:\n{text[:2000]}"


def screenshot(path: str | None = None) -> str:
    from pathlib import Path
    import tempfile
    import time

    page = get_session().ensure_page()
    if path is None:
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = str(Path(tempfile.gettempdir()) / f"aether-browser-{ts}.png")
    page.screenshot(path=path, full_page=False)
    return f"Browser screenshot saved to {path}"
