"""Receipt scanning (F04): the parser, the engine boundary (never the engine itself), the pictures and the prefill."""

import importlib.util
import io
import logging
import sys
import types
from datetime import date

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from budget_app.domain import attachments as files
from budget_app.domain import receipts as r
from budget_app.domain.receipts import ReceiptLine, TextBox
from budget_app.services import ocr
from budget_app.services import payees as payee_svc
from budget_app.services import receipts as svc
from budget_app.services.context import ValidationError

TODAY = date(2026, 10, 28)
RECEIPT = [
    "Coop Pronto Zürich HB",
    "Bahnhofplatz 15, 8001 Zürich",
    "Tel. 044 123 45 67",
    "Kassenbon Nr. 4711",
    "28.10.202618:42",
    "Bio Milch 1L  1.95",
    "Zopf 500g  4.50",
    "Äpfel Gala 1kg  3.80",
    "Mineralwasser  1.20",
    "TOTAL CHF  11.45",
    "Bezahlt TWINT  11.45",
    "MWST 2.6%  0.29",
    "Vielen Dank für Ihren Einkauf",
]


def picture(lines: list[str], size: tuple[int, int] = (400, 300), fmt: str = "PNG") -> bytes:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((10, 10 + 20 * i), line, fill="black")
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def pdf(lines: list[str], pages: int = 1) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for _ in range(pages):
        c.setFont("Helvetica", 11)
        for i, line in enumerate(lines):
            c.drawString(50, 800 - 16 * i, line)
        c.showPage()
    c.save()
    return buf.getvalue()


def fake_recognizer(rows: list[str]):
    """An engine that 'reads' the given rows from any picture: one box per row, in order."""

    def read(image):
        assert image.mode in ("RGB", "L")
        return [TextBox(text, 10.0, 10.0 + 40 * i, 30.0) for i, text in enumerate(rows)]

    return read


# ------------------------------------------------------------------ rows from boxes
def test_boxes_on_one_line_become_one_row_left_to_right():
    boxes = [
        TextBox("11.45", x=256, y=513, height=30),
        TextBox("TOTAL CHF", x=28, y=514, height=30),
        TextBox("Zopf", x=25, y=352, height=30),
        TextBox("4.50", x=240, y=353, height=30),
        TextBox("  ", x=0, y=0, height=30),  # empty text is dropped
    ]
    assert r.rows(boxes) == ["Zopf  4.50", "TOTAL CHF  11.45"]


def test_boxes_further_apart_than_half_a_height_are_separate_rows():
    boxes = [TextBox("a", y=0, height=20), TextBox("b", y=11, height=20), TextBox("c", y=9, height=20)]
    assert r.rows(boxes) == ["a  c", "b"]
    assert r.rows([]) == []


# ------------------------------------------------------------------ amounts, dates, currency
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("TOTAL CHF 11.45", [11.45]),
        ("1'234.50 und 1 234,50", [1234.50, 1234.50]),
        ("Rabatt 2.00-  -3.00", [-2.0, -3.0]),
        ("28.10.2026 18:42 Nr. 4711 1.2", []),  # a date, a time, integers and one decimal are not amounts
        ("Art. 0.95 x 3  2.85", [0.95, 2.85]),
    ],
)
def test_amounts(text, expected):
    assert r.amounts(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("28.10.2026", date(2026, 10, 28)),
        ("28.10.202618:42", date(2026, 10, 28)),  # glued to the time, as engines read it
        ("15/10/26 12:03", date(2026, 10, 15)),
        ("2026-10-01", date(2026, 10, 1)),
        ("Datum 1.9.2026", date(2026, 9, 1)),
        ("31.02.2026 and 30.09.2026", date(2026, 9, 30)),  # the first valid date
        ("29.10.2026", None),  # tomorrow
        ("28.10.1999", None),  # before 2000
        ("Nr. 4711", None),
    ],
)
def test_find_date(text, expected):
    assert r.find_date(text, TODAY) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("TOTAL CHF 11.45", "CHF"), ("Total Fr. 11.45", "CHF"), ("SFr. 3.00", "CHF"), ("Total EUR 3,95", "EUR")],
)
def test_find_currency(text, expected):
    assert r.find_currency(text) == expected


def test_no_currency_word_means_none():
    assert r.find_currency("Total 11.45 · Franken") == ""  # "chf" inside another word doesn't count either
    assert r.find_currency("Pochfrucht 2.00") == ""


# ------------------------------------------------------------------ the receipt
def test_a_swiss_till_receipt():
    receipt = r.parse(RECEIPT, TODAY)
    assert receipt.merchant == "Coop Pronto Zürich HB"
    assert receipt.date == TODAY
    assert (receipt.total, receipt.currency) == (11.45, "CHF")
    assert receipt.lines == [
        ReceiptLine("Bio Milch 1L", 1.95),
        ReceiptLine("Zopf 500g", 4.5),
        ReceiptLine("Äpfel Gala 1kg", 3.8),
        ReceiptLine("Mineralwasser", 1.2),
    ]
    assert receipt.rows == RECEIPT and not receipt.empty


def test_a_french_receipt_in_euros_with_a_subtotal_and_vat():
    rows = [
        "Migros Genève Cornavin",
        "Rue du Mont-Blanc 2",
        "15/10/26 12:03",
        "Pain 2,50",
        "Lait 1,45",
        "Sous-total 3,95",
        "TVA 2.6% 0,10",
        "Total EUR 3,95",
        "Carte 3,95",
    ]
    receipt = r.parse(rows, TODAY)
    assert (receipt.merchant, receipt.date, receipt.total, receipt.currency) == (
        "Migros Genève Cornavin",
        date(2026, 10, 15),
        3.95,
        "EUR",
    )
    assert receipt.lines == [ReceiptLine("Pain", 2.5), ReceiptLine("Lait", 1.45)]


@pytest.mark.parametrize(
    "total_row",
    [
        "Totale 4.00",
        "Summe 4.00",
        "Montant 4.00",
        "Importo 4.00",
        "Amount due 4.00",
        "Zu zahlen CHF 4.00",
        "Total  4.00",
    ],
)
def test_the_total_in_every_language(total_row):
    receipt = r.parse(["Bar Sport", "Caffè 1.80", "Cornetto 2.20", total_row, "Resto 6.00"], TODAY)
    assert receipt.total == 4.0
    assert receipt.lines == [ReceiptLine("Caffè", 1.8), ReceiptLine("Cornetto", 2.2)]


def test_the_last_total_row_counts_and_subtotals_do_not():
    rows = ["Shop", "Apfel 1.00", "Brot 2.00", "Zwischensumme 3.00", "Rabatt 0.50-", "Total 2.50", "Total bezahlt 2.50"]
    receipt = r.parse(rows, TODAY)
    assert receipt.total == 2.5
    assert receipt.lines == [ReceiptLine("Apfel", 1.0), ReceiptLine("Brot", 2.0), ReceiptLine("Rabatt", -0.5)]


def test_without_a_total_row_the_largest_amount_is_the_total():
    receipt = r.parse(["lorem ipsum", "12.50", "3.20"], TODAY)
    assert (receipt.total, receipt.lines, receipt.merchant) == (12.5, [], "lorem ipsum")


def test_lines_that_do_not_add_up_are_left_out():
    receipt = r.parse(["Shop", "A 1.00", "B 2.00", "Total 4.00"], TODAY)
    assert receipt.total == 4.0 and receipt.lines == []


def test_quantities_are_dropped_from_line_names_and_odd_rows_skipped():
    rows = ["Shop", "2 x Bier 8.00", "1.5 kg Äpfel 4.50", "0.50", "x 1.00", "Total 14.00"]
    receipt = r.parse(rows, TODAY)
    assert receipt.lines == [] or receipt.lines == [ReceiptLine("Bier", 8.0), ReceiptLine("Äpfel", 4.5)]
    rows = ["Shop", "2 x Bier 8.00", "1.5 kg Äpfel 4.50", "Total 12.50"]
    assert r.parse(rows, TODAY).lines == [ReceiptLine("Bier", 8.0), ReceiptLine("Äpfel", 4.5)]


def test_the_merchant_is_the_first_row_of_words_that_is_not_bookkeeping():
    rows = ["Kassenbon 4711", "Tel. 044 123 45 67", "28.10.2026", "12.00", "Bäckerei Müller", "Total 12.00"]
    assert r.parse(rows, TODAY).merchant == "Bäckerei Müller"
    assert r.parse(["Total 12.00", "Bäckerei Müller"], TODAY).merchant == ""  # nothing before the total


def test_the_default_currency_is_the_callers():
    assert r.parse(["Shop", "Total 1.00"], TODAY, "EUR").currency == "EUR"
    assert r.parse(["Shop", "Total 1.00"], TODAY).currency == "CHF"


def test_an_empty_receipt():
    receipt = r.parse(["", "  "], TODAY)
    assert receipt.empty and receipt.rows == [] and receipt.total is None and receipt.date is None


# ------------------------------------------------------------------ the engine boundary
@pytest.fixture
def engine_state(monkeypatch):
    monkeypatch.setattr(ocr._state, "engine", None)
    monkeypatch.setattr(ocr._state, "failed", False)
    return ocr._state


def test_boxes_from_the_engines_polygons():
    polygons = [
        [(28.0, 514.0), (130.0, 514.0), (130.0, 544.0), (28.0, 544.0)],
        [(256, 513), (300, 513), (300, 543), (256, 543)],
    ]
    assert ocr.boxes(polygons, ["TOTAL CHF", "11.45"]) == [
        TextBox("TOTAL CHF", 28.0, 514.0, 30.0),
        TextBox("11.45", 256.0, 513.0, 30.0),
    ]
    assert ocr.boxes(None, None) == [] and ocr.boxes(polygons, None) == []


def test_available_needs_both_packages_and_the_model_files(monkeypatch, tmp_path):
    check = ocr.available.__wrapped__  # the cached answer is the installation's; test the check itself
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "onnxruntime" else real(name))
    assert check() is False
    monkeypatch.setattr(importlib.util, "find_spec", real)
    monkeypatch.setattr(ocr, "models_dir", lambda: tmp_path)
    assert check() is False  # the package without its models (a broken bundle) would download them: not available
    for name in ocr.MODEL_FILES:
        (tmp_path / name).write_bytes(b"onnx")
    assert check() is (
        importlib.util.find_spec("rapidocr") is not None and importlib.util.find_spec("onnxruntime") is not None
    )
    monkeypatch.setattr(ocr, "models_dir", lambda: None)
    assert check() is False


def test_recognizer_loads_the_engine_once(monkeypatch, engine_state):
    loads = []

    def load():
        loads.append(1)
        return fake_recognizer(["x"])

    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "_load", load)
    first = ocr.recognizer()
    assert first is ocr.recognizer() and loads == [1]
    assert first(Image.new("RGB", (10, 10))) == [TextBox("x", 10.0, 10.0, 30.0)]


def test_recognizer_is_none_without_the_engine_or_when_loading_fails(monkeypatch, engine_state, caplog):
    monkeypatch.setattr(ocr, "available", lambda: False)
    assert ocr.recognizer() is None
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "_load", lambda: (_ for _ in ()).throw(RuntimeError("no runtime")))
    with caplog.at_level(logging.WARNING):
        assert ocr.recognizer() is None
        assert ocr.recognizer() is None  # not tried again
    assert sum("could not be loaded" in rec.getMessage() for rec in caplog.records) == 1
    assert engine_state.failed


def test_load_wraps_rapidocr(monkeypatch):
    """RapidOCR is stood in for by a fake module: the wrapper hands it an RGB array and converts the result."""
    seen = {}

    class FakeOutput:
        boxes = ([(1, 2), (5, 2), (5, 8), (1, 8)],)
        txts = ("Total 1.00",)

    class FakeRapidOCR:
        def __init__(self, params):
            seen["params"] = params

        def __call__(self, array):
            seen["shape"] = array.shape
            return FakeOutput()

    monkeypatch.setitem(sys.modules, "rapidocr", types.SimpleNamespace(RapidOCR=FakeRapidOCR))
    read = ocr._load()
    assert read(Image.new("L", (4, 3))) == [TextBox("Total 1.00", 1.0, 2.0, 6.0)]
    assert seen == {"params": {"Global.log_level": "warning"}, "shape": (3, 4, 3)}


# ------------------------------------------------------------------ preparing the file
def test_a_photo_is_one_picture_with_a_preview():
    prepared = svc.prepare("IMG_0042.JPG", picture(RECEIPT, fmt="JPEG"))
    assert prepared.name == "IMG_0042.JPG" and len(prepared.images) == 1 and prepared.text_rows == []
    assert prepared.images[0].mode == "RGB" and prepared.preview.size == (400, 300)


def test_the_preview_is_scaled_down_to_1200_pixels():
    prepared = svc.prepare("wide.png", picture(["x"], size=(2400, 600)))
    assert prepared.images[0].size == (2400, 600) and prepared.preview.size == (1200, 300)


def test_a_pdf_with_text_needs_no_engine_and_is_limited_to_three_pages():
    prepared = svc.prepare("e-receipt.pdf", pdf([*RECEIPT[:6], "TOTAL CHF 11.45"], pages=5))
    assert len(prepared.images) == 3 and prepared.images[0].mode == "RGB"
    assert prepared.text_rows[:2] == ["Coop Pronto Zürich HB", "Bahnhofplatz 15, 8001 Zürich"]
    engine_must_not_run = fake_recognizer(["WRONG 9.99"])
    receipt = svc.read(prepared, engine_must_not_run, TODAY)
    assert (receipt.merchant, receipt.total, receipt.date) == ("Coop Pronto Zürich HB", 11.45, TODAY)


def test_a_pdf_without_amounts_in_its_text_goes_to_the_engine():
    prepared = svc.prepare("scan.pdf", pdf(["Scanned receipt, no text layer worth the name"]))
    assert prepared.text_rows == []
    assert svc.read(prepared, fake_recognizer(["Kiosk", "Total 2.00"]), TODAY).total == 2.0


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("contract.docx", b"PK\x03\x04" + b"\x00" * 26, "is not a PDF or a photo"),
        ("notes.txt", b"Total 12.00", "is not a PDF or a photo"),
        ("setup.exe", b"MZ\x90", "is not a PDF or a photo"),
        ("photo.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16, "is a HEIC photo"),
        ("broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "can’t be read as a photo"),
        ("broken.pdf", b"%PDF-1.4 nothing", "can’t be read as a PDF"),
    ],
)
def test_files_that_cannot_be_read_are_refused(name, data, message):
    with pytest.raises(ValidationError, match=message):
        svc.prepare(name, data)


def test_a_file_over_the_limit_is_refused(monkeypatch):
    data = picture(["x"])
    monkeypatch.setattr(files, "MAX_BYTES", len(data) - 1)
    with pytest.raises(ValidationError, match="larger than 20 MB"):
        svc.prepare("big.png", data)


# ------------------------------------------------------------------ reading
def test_read_uses_the_injected_engine_and_the_default_currency():
    prepared = svc.prepare("bon.png", picture(["x"]))
    receipt = svc.read(prepared, fake_recognizer(["Bar Sport", "Caffè 1.80", "Totale 1.80"]), TODAY, "EUR")
    assert (receipt.merchant, receipt.total, receipt.currency, receipt.lines) == (
        "Bar Sport",
        1.8,
        "EUR",
        [ReceiptLine("Caffè", 1.8)],
    )


def test_read_without_any_engine_is_none(monkeypatch):
    monkeypatch.setattr(ocr, "recognizer", lambda: None)
    assert svc.read(svc.prepare("bon.png", picture(["x"])), None, TODAY) is None


def test_read_falls_back_to_the_installed_engine(monkeypatch):
    monkeypatch.setattr(ocr, "recognizer", lambda: fake_recognizer(["Kiosk", "Total 2.00"]))
    assert svc.read(svc.prepare("bon.png", picture(["x"])), None, TODAY).total == 2.0


def test_an_engine_failure_is_a_message_not_a_crash(caplog):
    def broken(image):
        raise RuntimeError("model blew up")

    with caplog.at_level(logging.WARNING), pytest.raises(ValidationError, match=r"“bon\.png” couldn’t be read"):
        svc.read(svc.prepare("bon.png", picture(["x"])), broken, TODAY)
    assert any("Reading the receipt bon.png failed" in rec.getMessage() for rec in caplog.records)


# ------------------------------------------------------------------ the form's starting values
def test_prefill_without_a_receipt_is_today_in_the_display_currency(ctx):
    ctx.store.settings.display_currency = "EUR"
    inp, items = svc.prefill(ctx, None, TODAY)
    assert (inp.type, inp.date, inp.amount, inp.currency, inp.payee, items) == (
        "expenditure",
        TODAY,
        None,
        "EUR",
        "",
        [],
    )


def test_prefill_from_a_receipt_with_a_known_payee_and_lines(ctx):
    payee_svc.ensure(ctx, "Coop Pronto Zürich HB")
    receipt = r.parse(RECEIPT, TODAY)
    inp, items = svc.prefill(ctx, receipt, TODAY)
    assert (inp.date, inp.amount, inp.currency, inp.payee) == (TODAY, 11.45, "CHF", "Coop Pronto Zürich HB")
    assert [(i.name, i.quantity, i.unit_price) for i in items] == [
        ("Bio Milch 1L", 1.0, 1.95),
        ("Zopf 500g", 1.0, 4.5),
        ("Äpfel Gala 1kg", 1.0, 3.8),
        ("Mineralwasser", 1.0, 1.2),
    ]


def test_prefill_cleans_a_new_merchant_and_keeps_todays_date_without_one(ctx):
    receipt = r.parse(["BAR SPORT SA", "Totale 4.00"], TODAY)
    inp, items = svc.prefill(ctx, receipt, TODAY)
    assert inp.payee == "Bar Sport SA"  # normalized like a bank text (F09)
    assert (inp.date, inp.amount, items) == (TODAY, 4.0, [])


def test_prefill_leaves_a_missing_or_negative_total_empty(ctx):
    inp, _ = svc.prefill(ctx, r.parse(["Shop", "Rabatt 2.00-"], TODAY), TODAY)
    assert inp.amount is None
