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
    """Singleton-ish browser session reused across tool calls in one agent run.

    attach_mode:
      headless — launch our own headless Chromium (default, unchanged).
      cdp      — attach to the user's real Chrome via --remote-debugging-port;
                 tabs are the user's actual tabs. close() only disconnects.
    """

    headless: bool = True
    attach_mode: str = "headless"  # headless | cdp
    cdp_url: str = "http://127.0.0.1:9222"
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
        if self._page is not None and not self._page.is_closed():
            return self._page
        if self._browser is None:
            self._pw = sync_playwright().start()
            if self.attach_mode == "cdp":
                try:
                    self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
                except Exception as e:
                    self._pw.stop()
                    self._pw = None
                    raise RuntimeError(
                        f"Could not attach to Chrome at {self.cdp_url} ({e}). "
                        "Use the browser_attach tool to relaunch Chrome with "
                        "remote debugging, or set browser.attach_mode: headless."
                    ) from e
            else:
                self._browser = self._pw.chromium.launch(headless=self.headless)
        self._page = self._pick_or_create_page()
        return self._page

    def _context(self) -> Any:
        ctxs = self._browser.contexts
        return ctxs[0] if ctxs else self._browser.new_context()

    def _pick_or_create_page(self) -> Any:
        pages = self.pages()
        if pages:
            return pages[-1]  # most recently opened
        return self._context().new_page()

    def pages(self) -> list:
        if self._browser is None:
            return []
        out = []
        for ctx in self._browser.contexts:
            out.extend(p for p in ctx.pages if not p.is_closed())
        return out

    def activate_tab(self, tab_id: int) -> Any:
        pages = self.pages()
        if not 0 <= tab_id < len(pages):
            raise ValueError(f"tab_id {tab_id} out of range (0..{len(pages) - 1})")
        self._page = pages[tab_id]
        try:
            self._page.bring_to_front()
        except Exception:
            pass
        return self._page

    def new_tab(self, url: str | None = None) -> Any:
        self.ensure_page()
        page = self._context().new_page()
        if url:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._page = page
        return page

    def close(self) -> None:
        # In cdp mode, browser.close() only disconnects — the user's Chrome
        # keeps running (Playwright semantics for connect_over_cdp).
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


def get_session(headless: bool = True, attach_mode: str | None = None,
                cdp_url: str | None = None) -> BrowserSession:
    global _session
    if _session is None:
        _session = BrowserSession(
            headless=headless,
            attach_mode=attach_mode or "headless",
            cdp_url=cdp_url or "http://127.0.0.1:9222",
        )
    return _session


def close_session() -> None:
    global _session
    if _session is not None:
        _session.close()
        _session = None


def _page_for(tab_id: int | None = None):
    s = get_session()
    s.ensure_page()
    if tab_id is not None:
        return s.activate_tab(int(tab_id))
    return s._page


def navigate(url: str, headless: bool = True, tab_id: int | None = None) -> str:
    get_session(headless=headless)
    page = _page_for(tab_id)
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    title = page.title()
    return f"Navigated to {url} (title: {title})"


def click(selector: str, tab_id: int | None = None) -> str:
    page = _page_for(tab_id)
    page.click(selector, timeout=10000)
    return f"Clicked {selector}"


def fill(selector: str, text: str, tab_id: int | None = None) -> str:
    page = _page_for(tab_id)
    page.fill(selector, text, timeout=10000)
    return f"Filled {selector} with {len(text)} chars"


def get_text(selector: str, tab_id: int | None = None) -> str:
    page = _page_for(tab_id)
    el = page.query_selector(selector)
    if el is None:
        return f"Element not found: {selector}"
    text = el.inner_text()
    return f"Text from {selector}:\n{text[:2000]}"


def list_tabs() -> str:
    s = get_session()
    s.ensure_page()
    pages = s.pages()
    if not pages:
        return "No open tabs."
    lines = []
    for i, p in enumerate(pages):
        cur = " (current)" if p is s._page else ""
        try:
            lines.append(f"[{i}] {p.title()[:60]} — {p.url[:80]}{cur}")
        except Exception:
            lines.append(f"[{i}] (unavailable){cur}")
    return "\n".join(lines)


def activate_tab(tab_id: int) -> str:
    s = get_session()
    s.ensure_page()
    page = s.activate_tab(int(tab_id))
    return f"Active tab: [{tab_id}] {page.title()[:60]} — {page.url[:80]}"


def new_tab(url: str | None = None) -> str:
    s = get_session()
    page = s.new_tab(url)
    where = f" at {url}" if url else ""
    return f"Opened new tab{where} (title: {page.title()[:60]})"


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
