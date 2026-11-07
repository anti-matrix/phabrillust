# -*- coding: utf-8 -*-
import io
import os
import re
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog
from unittest.mock import patch

from phabrillust.engine.image_state import ImageState
from phabrillust.engine.pipeline import (
    DATABEND_EFFECTS,
    GLITCH_EFFECTS,
    NOISE_EFFECTS,
    apply_pipeline,
)
from phabrillust.engine.source_generator import solid
from phabrillust.engine.watermark import WATERMARK_TEXT, embed_watermark, read_watermark
from phabrillust.gui.main_window import MainWindow
from phabrillust.utils.export import default_export_name
from phabrillust.utils.rng import get_rng


def test_preview_no_crash_and_downscale(app: QApplication) -> None:
    stderr_capture = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = stderr_capture
    try:
        state = ImageState()
        state.set_source(solid(256, 256, (80, 120, 60)))
        w = MainWindow(state)
        w.show()
        w._run_preview()
        w._thread_pool.waitForDone(5000)
        for _ in range(50):
            app.processEvents()
        err = stderr_capture.getvalue()
        assert "AttributeError" not in err, err
        assert "deleteLater" not in err, err
    finally:
        sys.stderr = old_stderr
    print("[OK] preview no crash")


def test_preview_downscaled_before_effects() -> None:
    from phabrillust.effects.glitch import ChannelShift

    orig_apply = ChannelShift.apply
    seen_sizes = []

    def wrapped(self, image, params, rng):
        seen_sizes.append(image.size)
        return orig_apply(self, image, params, rng)

    ChannelShift.apply = wrapped
    try:
        source = Image.new("RGB", (3000, 2000), (100, 100, 100))
        apply_pipeline(
            source,
            [(ChannelShift(), {"red_offset": 5})],
            123,
            preview=True,
            max_preview_side=400,
        )
        assert seen_sizes, "effect was not called"
        assert max(seen_sizes[0]) <= 400, f"effect saw full-res size {seen_sizes[0]}"
    finally:
        ChannelShift.apply = orig_apply
    print("[OK] preview downscaled before effects")


def test_preview_worker_no_leak(app: QApplication) -> None:
    import gc
    from phabrillust.gui.worker import PreviewWorker

    state = ImageState()
    state.set_source(solid(256, 256, (80, 120, 60)))
    w = MainWindow(state)
    w.show()
    for _ in range(5):
        w._run_preview()
        w._thread_pool.waitForDone(5000)
        for _ in range(20):
            app.processEvents()
    del w
    gc.collect()
    leaked = [o for o in gc.get_objects() if isinstance(o, PreviewWorker)]
    # PySide6 with setAutoDelete(False) may retain the QRunnable wrappers in
    # C++ memory, but the closure cycle and source reference must be released.
    retained_sources = [w for w in leaked if getattr(w, "source", None) is not None]
    assert len(retained_sources) == 0, (
        f"{len(retained_sources)} leaked PreviewWorker(s) still hold a source image"
    )
    print(f"[OK] preview worker released (no retained source images; {len(leaked)} C++-held wrappers)")


def test_export_unchanged() -> None:
    def old_pipeline(source, enabled, seed):
        by_category = {"glitch": [], "noise": [], "databend": []}
        for effect, params in enabled:
            by_category.setdefault(effect.category, []).append((effect, params))
        ordered = by_category["glitch"] + by_category["noise"] + by_category["databend"]
        image = source.convert("RGB")
        for effect, params in ordered:
            rng = get_rng(seed, salt=effect.id)
            image = effect.apply(image, params, rng).convert("RGB")
        return image, []

    source = Image.new("RGB", (64, 64), (128, 64, 32))
    enabled = [(e, {}) for e in GLITCH_EFFECTS + NOISE_EFFECTS + DATABEND_EFFECTS]
    new, _ = apply_pipeline(source, enabled, 42, preview=False)
    old, _ = old_pipeline(source, enabled, 42)
    assert np.array_equal(np.array(new), np.array(old))
    print("[OK] export pixel output unchanged")


def test_value_noise_lattice_clamped_and_returns_rgb() -> None:
    from phabrillust.effects.noise import ValueNoise

    effect = ValueNoise()
    source = Image.new("RGB", (120, 80), (128, 128, 128))

    seen_shapes = []

    def capturing_zoom(layer, zoom, order=1):
        seen_shapes.append(layer.shape)
        return original_zoom(layer, zoom, order=order)

    import scipy.ndimage as ndi

    original_zoom = ndi.zoom
    ndi.zoom = capturing_zoom
    try:
        rng = get_rng(12345, salt=effect.id)
        result = effect.apply(
            source,
            {"scale": 1, "octaves": 8, "persistence": 0.5, "per_channel": True},
            rng,
        )
    finally:
        ndi.zoom = original_zoom

    assert all(sh[0] <= 80 for sh in seen_shapes), f"layer h exceeded image: {seen_shapes}"
    assert all(sh[1] <= 120 for sh in seen_shapes), f"layer w exceeded image: {seen_shapes}"

    assert isinstance(result, Image.Image)
    assert result.mode == "RGB"
    assert result.size == source.size

    print("[OK] value noise lattice clamped and returns RGB")


def test_export_nonblocking(app: QApplication) -> None:
    state = ImageState()
    state.set_source(solid(500, 500, (128, 128, 128)))
    w = MainWindow(state)
    w.show()
    for panel in w._panels:
        if hasattr(panel, "setChecked"):
            panel.setChecked(True)
        elif hasattr(panel, "enable_checkbox"):
            panel.enable_checkbox.setChecked(True)

    temp_path = tempfile.mktemp(suffix=".png")
    ticks = [0]
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.__setitem__(0, ticks[0] + 1))
    timer.start(10)
    try:
        with patch.object(
            QFileDialog, "getSaveFileName", return_value=(temp_path, "PNG (*.png)")
        ):
            w._export()
        deadline = time.time() + 30
        while w._export_worker is not None and time.time() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert w._export_worker is None, "export worker did not finish"
        assert os.path.exists(temp_path), "export did not create a file"
        assert ticks[0] > 5, f"event loop frozen during export (ticks={ticks[0]})"
    finally:
        timer.stop()
        if os.path.exists(temp_path):
            os.remove(temp_path)
    print("[OK] export non-blocking")


def test_export_signature() -> None:
    from phabrillust.engine.signature import (
        SIGNATURE,
        inject_jpeg_comment,
        jpeg_exif_bytes,
        pnginfo_with_signature,
    )
    from PIL.ExifTags import Base, IFD

    sig_bytes = SIGNATURE.encode("ascii")

    png_path = tempfile.mktemp(suffix=".png")
    try:
        img = Image.new("RGB", (8, 8), (64, 128, 32))
        img.save(
            png_path,
            "PNG",
            pnginfo=pnginfo_with_signature(),
            exif=jpeg_exif_bytes(),
        )
        data = open(png_path, "rb").read()
        assert sig_bytes in data, "PNG raw bytes missing signature"
        ihdr_pos = data.find(b"IHDR")
        idat_pos = data.find(b"IDAT")
        sig_pos = data.find(sig_bytes)
        assert 0 < ihdr_pos < sig_pos < idat_pos, "PNG signature chunk does not precede IDAT"
        info = Image.open(png_path).info
        assert info.get("Title") == SIGNATURE
        assert info.get("Author") == "phabrillust"
        assert info.get("Copyright") == SIGNATURE
        assert info.get("Comment") == SIGNATURE
        assert info.get("Software") == "phabrillust"
        creation_time = info.get("Creation Time")
        assert creation_time is not None
        assert re.fullmatch(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", creation_time)
        png_exif = Image.open(png_path).getexif()
        assert png_exif[Base.Artist] == "phabrillust"
        assert re.fullmatch(
            r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$", png_exif[Base.DateTime]
        )
        assert Base.GPSInfo not in png_exif, "PNG GPS info must not be present"
        xmp = info.get("XML:com.adobe.xmp")
        assert xmp is not None, "PNG missing XMP packet (Windows Properties reads it)"
        assert "<rdf:li>phabrillust</rdf:li>" in xmp
        assert SIGNATURE in xmp
        assert "<xmp:CreatorTool>phabrillust</xmp:CreatorTool>" in xmp
        assert re.search(
            r"<xmp:CreateDate>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}</xmp:CreateDate>", xmp
        ), "XMP date must carry a UTC offset"
        assert "GPS" not in xmp, "XMP must not carry GPS"
        xmp_pos = data.find(b"XML:com.adobe.xmp")
        assert 0 < xmp_pos < idat_pos, "XMP packet should precede IDAT (uncompressed, readable)"
    finally:
        if os.path.exists(png_path):
            os.remove(png_path)

    jpeg_path = tempfile.mktemp(suffix=".jpg")
    try:
        img = Image.new("RGB", (8, 8), (64, 128, 32))
        img.save(jpeg_path, "JPEG", quality=85, optimize=True, exif=jpeg_exif_bytes())
        inject_jpeg_comment(jpeg_path)
        data = open(jpeg_path, "rb").read()
        assert data[:2] == b'\xff\xd8', "JPEG missing SOI"
        assert data.count(b'\xff\xd8') == 1, "JPEG extra SOI"
        assert data[2:4] == b'\xff\xfe', "JPEG COM not after SOI"
        length = int.from_bytes(data[4:6], "big")
        assert length == 2 + len(SIGNATURE)
        assert data[6:6 + len(SIGNATURE)] == sig_bytes
        assert sig_bytes in data, "JPEG raw bytes missing signature"
        exif = Image.open(jpeg_path).getexif()
        assert exif[Base.ImageDescription] == SIGNATURE
        assert exif[Base.Artist] == "phabrillust"
        assert exif[Base.Copyright] == SIGNATURE
        assert exif[Base.Software] == "phabrillust"
        assert exif[Base.XPTitle] == SIGNATURE.encode("utf-16-le") + b'\0\0'
        assert exif[Base.XPComment] == SIGNATURE.encode("utf-16-le") + b'\0\0'
        assert exif[Base.XPAuthor] == "phabrillust".encode("utf-16-le") + b'\0\0'
        assert re.fullmatch(r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$", exif[Base.DateTime])
        exif_sub = exif.get_ifd(IFD.Exif)
        user_comment = exif_sub[Base.UserComment]
        assert user_comment.startswith(b'ASCII\0\0\0')
        assert user_comment.endswith(sig_bytes)
        assert exif_sub[Base.DateTimeOriginal] == exif[Base.DateTime]
        assert exif_sub[Base.DateTimeDigitized] == exif[Base.DateTime]
        assert Base.GPSInfo not in exif, "GPS info must not be present"
    finally:
        if os.path.exists(jpeg_path):
            os.remove(jpeg_path)

    print("[OK] export signature PNG and JPEG")


def test_export_worker_signature(app: QApplication) -> None:
    from phabrillust.engine.signature import SIGNATURE
    from phabrillust.gui.worker import ExportWorker
    from PIL.ExifTags import Base
    from PySide6.QtCore import QThreadPool

    # 256px: the watermark needs >= ~128px to survive JPEG (cells become 1px below that).
    source = Image.new("RGB", (256, 256), (64, 128, 32))
    effects = [(e, {}) for e in GLITCH_EFFECTS + NOISE_EFFECTS + DATABEND_EFFECTS]
    sig_bytes = SIGNATURE.encode("ascii")

    for suffix, is_jpeg in ((".png", False), (".jpg", True)):
        path = tempfile.mktemp(suffix=suffix)
        worker = ExportWorker(
            source=source,
            enabled_effects=effects,
            global_seed=42,
            path=path,
            is_jpeg=is_jpeg,
            quality=85,
        )
        state = {"finished": False, "error": None}
        worker.signals.finished.connect(
            lambda _w, _s=state: _s.__setitem__("finished", True)
        )
        worker.signals.error.connect(
            lambda msg, _s=state: _s.__setitem__("error", msg)
        )
        QThreadPool.globalInstance().start(worker)
        deadline = time.time() + 30
        while not state["finished"] and state["error"] is None and time.time() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert state["error"] is None, f"export worker error for {suffix}: {state['error']}"
        assert state["finished"], f"export worker did not finish for {suffix}"
        assert os.path.exists(path), f"export did not create {suffix} file"
        data = open(path, "rb").read()
        assert sig_bytes in data, f"{suffix} raw bytes missing signature"
        assert sig_bytes + b" (seed 42)" in data, f"{suffix} raw bytes missing seed"
        from phabrillust.verify import check

        assert check(path).seed == 42
        with Image.open(path) as img:
            assert img.size == (256, 256)
            if is_jpeg:
                exif = img.getexif()
                assert Base.GPSInfo not in exif, "JPEG GPS info must not be present"
                assert re.fullmatch(
                    r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$", exif[Base.DateTime]
                )
            else:
                info = img.info
                assert re.fullmatch(
                    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", info.get("Creation Time")
                )
                assert info.get("Software") == "phabrillust"
                assert info.get("Seed") == "42"
                assert info.get("Title") == SIGNATURE
                assert "XML:com.adobe.xmp" in info
            found = read_watermark(img).text
            assert found == WATERMARK_TEXT, f"{suffix} export missing watermark (got {found!r})"
        os.remove(path)

    print("[OK] export worker signature")


def _jpeg_roundtrip(image: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def test_watermark_survives_recompression() -> None:
    from phabrillust.engine.source_generator import linear_gradient

    source = linear_gradient(1200, 900, (10, 10, 40), (230, 60, 140), 30)
    enabled = [(e, {}) for e in (GLITCH_EFFECTS[0], GLITCH_EFFECTS[1], NOISE_EFFECTS[2], DATABEND_EFFECTS[0])]
    art, _ = apply_pipeline(source, enabled, 7, preview=False)

    unmarked = read_watermark(art)
    assert unmarked.text is None, f"false positive on unmarked art: {unmarked}"

    marked = embed_watermark(art)
    assert marked.size == art.size and marked.mode == "RGB"
    diff = np.abs(np.asarray(marked, np.int16) - np.asarray(art, np.int16))
    assert diff.max() <= 12, f"watermark too strong (max delta {diff.max()})"

    attacks = {
        "lossless": marked,
        "jpeg q50": _jpeg_roundtrip(marked, 50),
        "640w + jpeg q60": _jpeg_roundtrip(marked.resize((640, 480), Image.LANCZOS), 60),
        "re-upload twice": _jpeg_roundtrip(
            _jpeg_roundtrip(marked.resize((1080, 810), Image.LANCZOS), 80).resize((720, 540), Image.LANCZOS), 75
        ),
    }
    for name, attacked in attacks.items():
        result = read_watermark(attacked)
        assert result.text == WATERMARK_TEXT, f"watermark lost after {name}: {result}"

    cropped = marked.crop((60, 45, 1140, 855))
    assert read_watermark(cropped).text is None, "crop is a documented limit; reading should fail cleanly"

    for bad in ("x" * 21, "Done by #Phabrillust"):
        try:
            embed_watermark(art, bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass
    print("[OK] watermark survives recompression")


def test_verify_cli() -> None:
    from phabrillust.verify import main as verify_main

    tmp_dir = tempfile.mkdtemp()
    marked_path = os.path.join(tmp_dir, "marked.jpg")
    plain_path = os.path.join(tmp_dir, "plain.jpg")
    try:
        art = Image.effect_noise((600, 400), 40).convert("RGB")
        embed_watermark(art).save(marked_path, "JPEG", quality=80)
        art.save(plain_path, "JPEG", quality=80)
        out = io.StringIO()
        with patch.object(sys, "stdout", out):
            assert verify_main([marked_path]) == 0
            assert verify_main([plain_path]) == 1
            assert verify_main([marked_path, plain_path]) == 1
        assert repr(WATERMARK_TEXT) in out.getvalue()
        assert "not found" in out.getvalue()
    finally:
        for p in (marked_path, plain_path):
            if os.path.exists(p):
                os.remove(p)
        os.rmdir(tmp_dir)
    print("[OK] verify CLI")


def test_selection_masks() -> None:
    from phabrillust.engine.selection import Selection

    size = (200, 100)
    rect = Selection("rect", ((0.25, 0.2), (0.75, 0.8)))
    m = np.asarray(rect.mask(size))
    assert m[50, 100] == 255 and m[5, 5] == 0
    assert rect.bbox(size) == (50, 20, 151, 81)
    assert rect.describe(size) == "rect 100×60"

    ell = np.asarray(Selection("ellipse", ((0.0, 0.0), (1.0, 1.0))).mask(size))
    assert ell[50, 100] == 255 and ell[0, 0] == 0, "ellipse corners must be outside"

    lasso = Selection("lasso", ((0.1, 0.1), (0.9, 0.1), (0.5, 0.9)))
    lm = np.asarray(lasso.mask(size))
    assert lm[30, 100] == 255 and lm[85, 10] == 0

    inv = Selection("rect", ((0.25, 0.2), (0.75, 0.8)), inverted=True)
    im = np.asarray(inv.mask(size))
    assert im[50, 100] == 0 and im[5, 5] == 255
    assert inv.bbox(size) == (0, 0, 200, 100)

    soft = np.asarray(Selection("rect", ((0.25, 0.2), (0.75, 0.8)), feather=0.02).mask(size))
    assert 0 < soft[20, 100] < 255, "feathered edge must be partial"

    assert Selection("rect", ((0.5, 0.5), (0.5, 0.5))).bbox(size) is None
    for bad in (lambda: Selection("star", ((0, 0), (1, 1))), lambda: Selection("lasso", ((0, 0), (1, 1)))):
        try:
            bad()
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
    print("[OK] selection masks")


def test_pipeline_respects_selection() -> None:
    from phabrillust.engine.selection import Selection
    from phabrillust.engine.source_generator import linear_gradient

    source = linear_gradient(400, 300, (20, 20, 20), (240, 200, 160), 0)
    enabled = [(e, {}) for e in (GLITCH_EFFECTS[4], NOISE_EFFECTS[0], DATABEND_EFFECTS[5])]
    src = np.asarray(source)

    for sel in (
        Selection("rect", ((0.25, 0.25), (0.75, 0.75))),
        Selection("ellipse", ((0.1, 0.1), (0.6, 0.9))),
        Selection("lasso", ((0.1, 0.1), (0.9, 0.2), (0.5, 0.9))),
        Selection("rect", ((0.25, 0.25), (0.75, 0.75)), inverted=True),
    ):
        out, _ = apply_pipeline(source, enabled, 5, preview=False, selection=sel)
        assert out.size == source.size and out.mode == "RGB"
        res = np.asarray(out)
        mask = np.asarray(sel.mask(source.size)) == 255
        assert np.array_equal(res[~mask], src[~mask]), f"{sel.shape}: pixels outside the selection changed"
        assert not np.array_equal(res[mask], src[mask]), f"{sel.shape}: nothing changed inside the selection"

    empty = Selection("rect", ((0.5, 0.5), (0.5, 0.5)))
    out, _ = apply_pipeline(source, enabled, 5, preview=False, selection=empty)
    assert np.array_equal(np.asarray(out), src), "degenerate selection must leave the image untouched"

    full, _ = apply_pipeline(source, enabled, 5, preview=False)
    again, _ = apply_pipeline(source, enabled, 5, preview=False, selection=None)
    assert np.array_equal(np.asarray(full), np.asarray(again))
    print("[OK] pipeline respects selection")


def test_image_state_history_labels() -> None:
    state = ImageState()
    assert state.steps == [] and not state.can_undo
    state.set_source(solid(8, 8, (0, 0, 0)), label="New solid 8×8")
    state.push(solid(8, 8, (1, 1, 1)), "Pixel Sort")
    state.push(solid(8, 8, (2, 2, 2)), "Databend Drop · selection")
    assert state.steps == ["New solid 8×8", "Pixel Sort", "Databend Drop · selection"]
    state.reset()
    assert state.steps[-1] == "Reset to original" and len(state.steps) == 4
    state.undo()
    assert state.steps[-1] == "Databend Drop · selection"
    assert np.asarray(state.current)[0, 0, 0] == 2
    state.undo()
    state.undo()
    assert state.steps == ["New solid 8×8"] and not state.can_undo
    state.undo()  # no-op at the bottom of history
    assert state.steps == ["New solid 8×8"]
    print("[OK] image state history labels")


def _wait_for_jobs(app: QApplication, w: MainWindow, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while (w._busy or w._active_workers) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.005)
    w._thread_pool.waitForDone(5000)
    for _ in range(20):
        app.processEvents()


def test_ui_apply_undo_reset(app: QApplication) -> None:
    from phabrillust.engine.selection import Selection

    state = ImageState()
    state.set_source(solid(300, 200, (128, 128, 128)), label="New solid 300×200")
    w = MainWindow(state)
    w.show()
    assert not w.undo_btn.isEnabled() and not w.apply_btn.isEnabled()

    panel = next(p for p in w._panels if p.effect.id == "gaussian_noise")
    panel.setChecked(True)
    assert w.apply_btn.isEnabled()
    assert "1 on" in w._right_sections["noise"].title()

    w._on_selection_drawn(Selection("rect", ((0.0, 0.0), (0.5, 1.0))))
    w._apply_effects()
    _wait_for_jobs(app, w)
    assert state.steps == ["New solid 300×200", "Gaussian Noise · selection"], state.steps
    assert not panel.isChecked(), "applied effects should turn off"
    arr = np.asarray(state.current)
    assert arr[:, 200:].std() == 0 and arr[:, :100].std() > 0, "apply must respect the selection"
    assert w.history_list.count() == 2 and w.undo_btn.isEnabled()

    w._undo()
    assert state.steps == ["New solid 300×200"] and np.asarray(state.current).std() == 0
    w._reset()
    assert state.steps[-1] == "Reset to original"
    w._clear_selection()
    assert w.selection is None and not w.invert_checkbox.isEnabled()
    w.close()
    print("[OK] ui apply/undo/reset")


def test_effect_panels_no_duplicate_labels(app: QApplication) -> None:
    from PySide6.QtWidgets import QCheckBox, QLabel

    state = ImageState()
    state.set_source(solid(64, 64, (0, 0, 0)))
    w = MainWindow(state)
    for panel in w._panels:
        texts = [lbl.text() for lbl in panel.findChildren(QLabel)]
        texts += [cb.text() for cb in panel.findChildren(QCheckBox) if cb.text()]
        for param in panel.effect.params:
            assert texts.count(param.label) == 1, f"{panel.effect.name}: '{param.label}' shown {texts.count(param.label)}x"
        assert panel._body.isHidden(), f"{panel.effect.name}: params should be collapsed while off"
    pixel_sort = next(p for p in w._panels if p.effect.id == "pixel_sort")
    pixel_sort.setChecked(True)
    assert not pixel_sort._body.isHidden()
    w.close()
    print("[OK] effect panels: no duplicate labels, collapse when off")


def test_canvas_draws_selection(app: QApplication) -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    state = ImageState()
    state.set_source(solid(400, 300, (90, 90, 90)))
    w = MainWindow(state)
    w.resize(1400, 900)
    w.show()
    for _ in range(10):
        app.processEvents()
    r = w.canvas.image_rect()
    assert r is not None

    def drag(tool: str, pts: list[tuple[float, float]]) -> None:
        w._set_tool(tool)
        widget_pts = [QPoint(int(r.x() + fx * r.width()), int(r.y() + fy * r.height())) for fx, fy in pts]
        QTest.mousePress(w.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, widget_pts[0])
        for p in widget_pts[1:]:
            QTest.mouseMove(w.canvas, p)
        QTest.mouseRelease(w.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, widget_pts[-1])
        app.processEvents()

    drag("rect", [(0.2, 0.2), (0.5, 0.5), (0.8, 0.6)])
    sel = w.selection
    assert sel is not None and sel.shape == "rect"
    (x0, y0), (x1, y1) = sel.points
    assert abs(x0 - 0.2) < 0.02 and abs(y1 - 0.6) < 0.02, sel.points
    assert w.invert_checkbox.isEnabled() and "rect" in w.status_sel_label.text()

    w.invert_checkbox.setChecked(True)
    w.feather_slider.setValue(50)
    assert w.selection.inverted and w.selection.feather > 0

    drag("lasso", [(0.1, 0.1), (0.4, 0.15), (0.45, 0.5), (0.2, 0.6), (0.1, 0.3)])
    assert w.selection.shape == "lasso" and len(w.selection.points) >= 3

    drag("ellipse", [(0.5, 0.5), (0.5, 0.5)])  # click without drag clears
    assert w.selection is None
    w.close()
    print("[OK] canvas draws selection")


def test_default_export_name() -> None:
    assert default_export_name("my.photo", 42, ".png") == "my.photo_phabrillust_42.png"
    assert default_export_name("my photo", 0, ".jpg") == "my photo_phabrillust_0.jpg"
    assert default_export_name(None, 123, ".png") == "image_phabrillust_123.png"
    assert default_export_name(None, 0, ".jpg") == "image_phabrillust_0.jpg"
    try:
        default_export_name("x", 1, ".jpeg")
        raise AssertionError("expected ValueError for .jpeg extension")
    except ValueError:
        pass
    print("[OK] default export name")


def test_image_state_source_name() -> None:
    state = ImageState()
    assert state.source_name is None

    state.set_source(solid(8, 8, (0, 0, 0)))
    assert state.source_name is None

    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, "foo.bar.png")
    try:
        Image.new("RGB", (8, 8), (32, 64, 32)).save(tmp_path, "PNG")
        state.load(tmp_path)
        assert state.source_name == "foo.bar"
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        os.rmdir(tmp_dir)

    state.reset()
    assert state.source_name == "foo.bar"

    state.push(Image.new("RGB", (4, 4)))
    state.undo()
    assert state.source_name == "foo.bar"

    state.set_source(solid(8, 8, (0, 0, 0)))
    assert state.source_name is None

    print("[OK] image state source name")


def test_verify_tab(app: QApplication) -> None:
    tmp_dir = tempfile.mkdtemp()
    marked_path = os.path.join(tmp_dir, "marked.jpg")
    plain_path = os.path.join(tmp_dir, "plain.jpg")
    broken_path = os.path.join(tmp_dir, "broken.png")
    w = MainWindow(ImageState())
    w.show()
    try:
        art = Image.effect_noise((600, 400), 40).convert("RGB")
        embed_watermark(art).save(marked_path, "JPEG", quality=75)
        art.save(plain_path, "JPEG", quality=75)
        with open(broken_path, "wb") as fp:
            fp.write(b"not an image")

        w.tabs.setCurrentIndex(1)
        assert not w._editing
        # A full-res job disables the editor only; Verify stays usable.
        w._set_busy("Exporting…")
        assert not w._edit_page.isEnabled() and w.verify_panel.isEnabledTo(w)
        w._set_busy(None)

        panel = w.verify_panel
        panel.check_paths([marked_path, plain_path, broken_path])
        deadline = time.time() + 30
        while panel._pending and time.time() < deadline:
            app.processEvents()
            time.sleep(0.005)
        for _ in range(20):
            app.processEvents()
        assert not panel._pending

        rows = {panel.checked_list.item(i).toolTip(): panel.checked_list.item(i) for i in range(3)}
        assert rows[marked_path].text().startswith("●")
        assert rows[plain_path].text().startswith("✕")
        assert rows[broken_path].text().startswith("!")

        panel.checked_list.setCurrentItem(rows[marked_path])
        assert panel.verdict_label.objectName() == "verdict-yes"
        assert "Made with Phabrillust" in panel.verdict_label.text()
        assert WATERMARK_TEXT in panel._details["watermark"].text()
        assert panel._details["size"].text() == "600×400"
        assert panel._details["seed"].text() == "not recorded"
        panel.checked_list.setCurrentItem(rows[plain_path])
        assert panel.verdict_label.objectName() == "verdict-no"
        panel.checked_list.setCurrentItem(rows[broken_path])
        assert panel.verdict_label.objectName() == "verdict-error"
    finally:
        w.close()
        for p in (marked_path, plain_path, broken_path):
            if os.path.exists(p):
                os.remove(p)
        os.rmdir(tmp_dir)
    print("[OK] verify tab")


def main() -> None:
    app = QApplication(sys.argv)
    test_preview_no_crash_and_downscale(app)
    test_preview_downscaled_before_effects()
    test_preview_worker_no_leak(app)
    test_export_unchanged()
    test_value_noise_lattice_clamped_and_returns_rgb()
    test_export_nonblocking(app)
    test_export_signature()
    test_export_worker_signature(app)
    test_watermark_survives_recompression()
    test_verify_cli()
    test_selection_masks()
    test_pipeline_respects_selection()
    test_image_state_history_labels()
    test_ui_apply_undo_reset(app)
    test_effect_panels_no_duplicate_labels(app)
    test_canvas_draws_selection(app)
    test_default_export_name()
    test_image_state_source_name()
    test_verify_tab(app)
    print("ALL VERIFICATION PASSED")


if __name__ == "__main__":
    main()
