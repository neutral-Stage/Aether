"""Screenshots carry display geometry, so pixel coordinates map to click points."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from aether.perception import ocr, screen
from aether.perception.ocr import TextRegion
from aether.perception.screen import Capture, DisplayInfo

RETINA_MAIN = DisplayInfo(1, 1, 0.0, 0.0, 1512.0, 982.0, 2.0, True)
SECOND = DisplayInfo(2, 2, 1512.0, -200.0, 1920.0, 1080.0, 1.0, False)


def _png(path: Path, w: int, h: int) -> str:
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    path.write_bytes(png)
    return str(path)


@pytest.mark.unit
class TestCaptureMath:
    def test_retina_pixels_are_half_points(self) -> None:
        cap = Capture("/x.png", RETINA_MAIN, 3024, 1964)
        assert cap.to_points(3024, 1964) == pytest.approx((1512, 982))
        assert cap.to_points(200, 100) == pytest.approx((100, 50))

    def test_resized_capture_maps_back(self) -> None:
        cap = Capture("/x.png", RETINA_MAIN, 1600, 1039)
        gx, gy = cap.to_points(800, 519.5)
        assert gx == pytest.approx(756, abs=0.5)
        assert gy == pytest.approx(491, abs=0.5)

    def test_secondary_display_offset(self) -> None:
        cap = Capture("/y.png", SECOND, 1920, 1080)
        assert cap.to_points(0, 0) == pytest.approx((1512, -200))
        assert cap.to_pixels(1512 + 960, -200 + 540) == pytest.approx((960, 540))

    def test_normalized_to_points(self) -> None:
        cap = Capture("/x.png", RETINA_MAIN, 3024, 1964)
        assert cap.normalized_to_points(0.5, 0.5) == pytest.approx((756, 491))

    def test_label_mentions_size_display_and_cursor(self) -> None:
        cap = Capture("/y.png", SECOND, 1920, 1080, display_count=2,
                      cursor_on_display=True, source="native")
        label = cap.label()
        assert "1920x1080 pixels" in label
        assert "display 2 of 2" in label
        assert "cursor is on this display" in label
        assert "own windows are hidden" in label

    def test_contains(self) -> None:
        assert SECOND.contains(1600, 0)
        assert not SECOND.contains(100, 100)
        assert screen.display_for_point(1600, 0, [RETINA_MAIN, SECOND]) is SECOND
        assert screen.display_for_point(-50, -50, [RETINA_MAIN, SECOND]) is RETINA_MAIN


@pytest.mark.unit
class TestCaptureBackends:
    def test_png_size(self, tmp_path: Path) -> None:
        assert screen.png_size(_png(tmp_path / "a.png", 7, 3)) == (7, 3)
        (tmp_path / "b.png").write_bytes(b"nope")
        assert screen.png_size(str(tmp_path / "b.png")) == (0, 0)

    def test_native_capture_registers_geometry(self, monkeypatch, tmp_path) -> None:  # noqa: ANN001
        img = _png(tmp_path / "n.png", 1600, 1039)
        monkeypatch.setattr(screen, "list_displays", lambda: [RETINA_MAIN, SECOND])
        monkeypatch.setattr(screen, "cursor_position", lambda: (100.0, 100.0))
        monkeypatch.setattr(screen, "_capture_native", lambda disp, max_edge: {
            "path": img, "width": 1600, "height": 1039, "scale": 2.0,
            "frame": [0, 0, 1512, 982]})
        cap = screen.capture(1, max_edge=1600)
        assert cap.source == "native"
        assert cap.cursor_on_display
        assert cap.display_count == 2
        assert screen.capture_info(img) == cap
        assert "1600x1039" in screen.image_label(img)

    def test_cli_fallback_targets_display_and_resizes(self, monkeypatch, tmp_path) -> None:  # noqa: ANN001
        calls: list[list[str]] = []
        out = tmp_path / "c.png"

        def fake_run(args, **kw):  # noqa: ANN001, ANN003
            calls.append(list(args))
            if args[0] == "screencapture":
                _png(Path(args[-1]), 1920, 1080)
            elif args[0] == "sips":
                _png(Path(args[-1]), 1600, 900)
        monkeypatch.setattr(screen, "list_displays", lambda: [RETINA_MAIN, SECOND])
        monkeypatch.setattr(screen, "cursor_position", lambda: None)
        monkeypatch.setattr(screen, "_capture_native", lambda disp, max_edge: None)
        monkeypatch.setattr(screen.subprocess, "run", fake_run)
        cap = screen.capture(2, max_edge=1600, path=str(out))
        assert calls[0][:4] == ["screencapture", "-x", "-D", "2"]
        assert calls[1][:3] == ["sips", "-Z", "1600"]
        assert (cap.pixel_width, cap.pixel_height) == (1600, 900)
        assert cap.to_points(0, 0) == pytest.approx((1512, -200))
        assert cap.source == "screencapture"

    def test_resized_copy_keeps_geometry(self, monkeypatch, tmp_path) -> None:  # noqa: ANN001
        src = _png(tmp_path / "big.png", 3024, 1964)
        screen._register(Capture(src, RETINA_MAIN, 3024, 1964))  # noqa: SLF001

        def fake_run(args, **kw):  # noqa: ANN001, ANN003
            _png(Path(args[-1]), 1600, 1039)
        monkeypatch.setattr(screen.subprocess, "run", fake_run)
        small = screen.resized_copy(src, 1600)
        assert small != src
        info = screen.capture_info(small)
        assert (info.pixel_width, info.pixel_height) == (1600, 1039)
        assert info.to_points(1600, 1039) == pytest.approx((1512, 982))
        assert screen.resized_copy(small, 2000) == small  # already small enough


@pytest.mark.unit
class TestOcrPoints:
    def test_ocr_regions_become_click_points(self, tmp_path) -> None:  # noqa: ANN001
        path = str(tmp_path / "shot.png")
        screen._register(Capture(path, SECOND, 1920, 1080))  # noqa: SLF001
        region = TextRegion(text="Send", confidence=0.9, x=0.5, y=0.5, w=0.1, h=0.05)
        pts = ocr.regions_to_points([region], path)
        assert pts is not None
        assert (pts[0].x, pts[0].y) == pytest.approx((1512 + 960, -200 + 540))
        assert (pts[0].w, pts[0].h) == pytest.approx((192, 54))
        text = ocr._format_regions([region], 1920, 1080, image_path=path)  # noqa: SLF001
        assert "screen points" in text and "pt)" in text

    def test_foreign_image_keeps_pixels(self) -> None:
        region = TextRegion(text="x", confidence=1.0, x=0.1, y=0.1, w=0.1, h=0.1)
        assert ocr.regions_to_points([region], "/not/registered.png") is None


@pytest.mark.unit
def test_grounding_settings_env_override(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
    assert screen.grounding_settings()["max_image_edge"] == 1600
    (tmp_path / "grounding_calibration.json").write_text(
        '{"max_image_edge": 1280, "coord_space": "norm1000"}')
    s = screen.grounding_settings()
    assert (s["max_image_edge"], s["coord_space"]) == (1280, "norm1000")
    monkeypatch.setenv("AETHER_IMAGE_MAX_EDGE", "1024")
    assert screen.grounding_settings()["max_image_edge"] == 1024
