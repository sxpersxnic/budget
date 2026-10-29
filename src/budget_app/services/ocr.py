"""The local OCR engine for receipts (F04): RapidOCR run by ONNX Runtime, when both are installed. Nothing is sent.

The engine is an optional dependency (`budget-app[ocr]`, in the installers): RapidOCR's wheel carries its PP-OCR
models (text detection, orientation, recognition for Latin scripts and more), so no model is ever downloaded; ONNX
Runtime has wheels for macOS on Apple silicon, Windows and Linux, but none for Intel Macs, where the receipt sheet
falls back to typing the amount, date and payee. `available()` tells without loading anything; `recognizer()`
loads the engine once (about ten seconds the first time) and hands it out as a function from a picture to the
text boxes it found, which `domain.receipts` turns into a receipt. Tests inject a recognizer of their own and
never run the engine.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from collections.abc import Callable, Sequence
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from budget_app.domain.receipts import TextBox

if TYPE_CHECKING:
    from PIL.Image import Image

log = logging.getLogger(__name__)

Recognizer = Callable[["Image"], list[TextBox]]

PACKAGES = ("rapidocr", "onnxruntime")
# the models RapidOCR's default configuration uses; they come with its wheel
MODEL_FILES = ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx", "ch_ppocr_mobile_v2.0_cls_mobile.onnx")


class _State:
    """The loaded engine, or the memory that loading it failed (so it isn't tried again at every receipt)."""

    engine: Recognizer | None = None
    failed = False
    lock = threading.Lock()


_state = _State()


def models_dir() -> Path | None:
    """RapidOCR's model folder, when the package is installed."""
    spec = importlib.util.find_spec("rapidocr")
    return Path(spec.origin).parent / "models" if spec is not None and spec.origin else None


@cache
def available() -> bool:
    """Whether the engine, its runtime and its model files are installed; nothing is imported or loaded."""
    if any(importlib.util.find_spec(name) is None for name in PACKAGES):
        return False
    models = models_dir()
    return models is not None and all((models / f).is_file() for f in MODEL_FILES)


def recognizer() -> Recognizer | None:
    """The engine as a function picture -> text boxes, loaded on first use; None when it is not installed or
    could not be loaded (logged once)."""
    if not available() or _state.failed:
        return None
    with _state.lock:
        if _state.engine is None and not _state.failed:
            try:
                _state.engine = _load()
            except Exception:
                _state.failed = True
                log.warning("The OCR engine could not be loaded; receipts are typed by hand", exc_info=True)
    return _state.engine


def _load() -> Recognizer:
    import numpy as np  # noqa: PLC0415 - the engine is loaded when the first receipt is read, not at startup
    from rapidocr import RapidOCR  # noqa: PLC0415

    engine = RapidOCR(params={"Global.log_level": "warning"})  # its own INFO lines would flood the log

    def read(image: Image) -> list[TextBox]:
        result: Any = engine(np.asarray(image.convert("RGB")))
        return boxes(result.boxes, result.txts)

    return read


def boxes(polygons: Sequence[Any] | None, texts: Sequence[str] | None) -> list[TextBox]:
    """RapidOCR's output, four corner points and a text per box, as `TextBox`es (top-left corner, height)."""
    if polygons is None or texts is None:
        return []
    out = []
    for poly, text in zip(polygons, texts, strict=False):
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        out.append(TextBox(str(text), min(xs), min(ys), max(ys) - min(ys)))
    return out
