"""Browser tab management + CDP attach mode — mocked Playwright objects."""
from __future__ import annotations

import aether.effectors.browser as browser


class FakePage:
    def __init__(self, title="T", url="http://x", closed=False):
        self._title, self._url, self._closed = title, url, closed
        self.brought_front = False

    def is_closed(self):
        return self._closed

    def title(self):
        return self._title

    @property
    def url(self):
        return self._url

    def bring_to_front(self):
        self.brought_front = True

    def goto(self, url, **kw):
        self._url = url


class FakeContext:
    def __init__(self, pages):
        self.pages = pages

    def new_page(self):
        p = FakePage(title="new")
        self.pages.append(p)
        return p


class FakeBrowser:
    def __init__(self, pages):
        self.contexts = [FakeContext(pages)]


def _session_with(pages):
    browser.close_session()
    s = browser.get_session()
    s._browser = FakeBrowser(pages)
    s._page = pages[0] if pages else None
    return s


def teardown_function():
    browser.close_session()


def test_pages_filters_closed():
    p1, p2 = FakePage(), FakePage(closed=True)
    s = _session_with([p1, p2])
    assert s.pages() == [p1]


def test_activate_tab_switches_and_brings_front():
    p0, p1 = FakePage(title="zero"), FakePage(title="one")
    s = _session_with([p0, p1])
    s.activate_tab(1)
    assert s._page is p1
    assert p1.brought_front


def test_activate_tab_out_of_range():
    s = _session_with([FakePage()])
    try:
        s.activate_tab(5)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "out of range" in str(e)


def test_new_tab_appends_and_navigates():
    p0 = FakePage()
    s = _session_with([p0])
    page = s.new_tab("http://new")
    assert page.url == "http://new"
    assert s._page is page


def test_list_tabs_marks_current():
    p0, p1 = FakePage(title="a", url="http://a"), FakePage(title="b", url="http://b")
    s = _session_with([p0, p1])
    s._page = p1
    out = browser.list_tabs()
    assert "[0]" in out and "[1]" in out
    assert "(current)" in out.splitlines()[1]  # p1 is current


def test_cdp_mode_configures_session():
    browser.close_session()
    s = browser.get_session(attach_mode="cdp", cdp_url="http://127.0.0.1:9222")
    assert s.attach_mode == "cdp"
    assert s.cdp_url == "http://127.0.0.1:9222"
