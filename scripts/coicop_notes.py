"""Turn the UN's COICOP 2018 structure file into the app's notes (F40): `resources/coicop_notes.json`.

Source: *Classification of Individual Consumption According to Purpose (COICOP) 2018*, United Nations
Statistics Division, Statistical Papers Series M No. 99. Its "Final structure (Excel) [En]" on
https://unstats.un.org/unsd/classifications/Econ holds, per code, the columns `code`, `title`, `intro`,
`includes`, `alsoIncludes` and `excludes`. The app ships the last three for every code of
`resources/coicop2018.csv`, in English; the translations are the app's own (`coicop_notes_<lang>.json`).

Usage (the downloaded workbook is data from the internet: read it with an isolated interpreter):

    curl -LO https://unstats.un.org/unsd/classifications/Econ/Download/COICOP_2018_English_structure.xlsx
    .venv/bin/python -I scripts/coicop_notes.py COICOP_2018_English_structure.xlsx [--retrieved 2026-10-21]

The script writes `coicop_notes.json` and reports the app's codes without notes and the source's codes the
app doesn't have (level 5, see F39). A translation file that is missing, or still marked `_untranslated`,
is (re)written as a copy of the English text with that mark; translated files are left alone, and codes
they lack are listed.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date
from pathlib import Path

import openpyxl

RESOURCES = Path(__file__).resolve().parents[1] / "src" / "budget_app" / "resources"
COICOP_FILE = RESOURCES / "coicop2018.csv"
NOTES_FILE = RESOURCES / "coicop_notes.json"
TRANSLATIONS = ("de", "fr", "it")
URL = "https://unstats.un.org/unsd/classifications/Econ/Download/COICOP_2018_English_structure.xlsx"
PUBLICATION = (
    "Classification of Individual Consumption According to Purpose (COICOP) 2018, "
    "United Nations Statistics Division, Statistical Papers Series M No. 99"
)
FIELDS = {"includes": "includes", "alsoIncludes": "also_includes", "excludes": "excludes"}


def clean(text: object) -> str:
    """One note as the app stores it: one item per line, without the source's bullets and line-end codes."""
    if text is None:
        return ""
    text = str(text).replace("_x000D_", "").replace("\r", "").replace("\xa0", " ")
    items = []
    for line in text.split("\n"):
        item = re.sub(r"^\*\s*", "", re.sub(r"\s+", " ", line).strip())
        if item:
            items.append(item)
    return "\n".join(items)


def read_source(path: Path) -> dict[str, dict[str, str]]:
    sheet = openpyxl.load_workbook(path, read_only=True).worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = [str(c) for c in next(rows)]
    missing = {"code", *FIELDS} - set(header)
    if missing:
        raise SystemExit(f"{path}: columns {sorted(missing)} not found in {header}")
    notes = {}
    for row in rows:
        record = dict(zip(header, row, strict=False))
        if record["code"] is None:
            continue
        notes[str(record["code"]).strip()] = {ours: clean(record[theirs]) for theirs, ours in FIELDS.items()}
    return notes


def app_codes() -> list[str]:
    with COICOP_FILE.open(newline="", encoding="utf-8") as f:
        return [r["code"] for r in csv.DictReader(f)]


def dump(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def ordered(meta: dict, notes: dict[str, dict[str, str]]) -> dict:
    """Metadata keys (`_…`) first, then the codes in sorted order."""
    return meta | {code: notes[code] for code in sorted(notes)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workbook", type=Path, help="COICOP_2018_English_structure.xlsx")
    parser.add_argument("--retrieved", default=date.today().isoformat(), help="date the workbook was downloaded")
    args = parser.parse_args()

    source = read_source(args.workbook)
    codes = app_codes()
    empty = dict.fromkeys(FIELDS.values(), "")
    notes = {code: source.get(code, empty) for code in codes}
    meta = {"_source": {"publication": PUBLICATION, "url": URL, "retrieved": args.retrieved}}
    dump(NOTES_FILE, ordered(meta, notes))

    absent = [c for c in codes if c not in source]
    blank = [c for c in codes if c in source and not any(source[c].values())]
    extra = sorted(set(source) - set(codes))
    print(f"{NOTES_FILE.name}: {len(codes)} codes, {sum(1 for n in notes.values() if any(n.values()))} with notes")
    print(f"not in the source ({len(absent)}): {' '.join(absent) or '-'}")
    print(f"in the source without includes, also includes or excludes ({len(blank)}): {' '.join(blank) or '-'}")
    deeper = [c for c in extra if c.count(".") >= 4]
    print(f"in the source, not in the app ({len(extra)}, {len(deeper)} of them level 5): {' '.join(extra) or '-'}")

    for lang in TRANSLATIONS:
        path = RESOURCES / f"coicop_notes_{lang}.json"
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"_untranslated": True}
        if current.get("_untranslated"):
            dump(path, ordered({"_untranslated": True}, notes))
            print(f"{path.name}: English copy, marked untranslated")
            continue
        lacking = [c for c in codes if c not in current]
        print(f"{path.name}: translated; codes missing ({len(lacking)}): {' '.join(lacking) or '-'}")


if __name__ == "__main__":
    main()
