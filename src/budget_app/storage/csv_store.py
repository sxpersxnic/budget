"""A tiny typed CSV repository for dataclass records."""

from __future__ import annotations

import copy
import csv
import dataclasses
import os
import threading
import typing
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Protocol

from budget_app.storage.history import ADD, DELETE, UPDATE, Change, History


class Record(Protocol):
    """A dataclass record with a string id."""

    __dataclass_fields__: ClassVar[dict[str, Any]]
    id: str


def _to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return repr(round(value, 6))
    return str(value)


def _from_str(text: str, typ):
    if typ is bool:
        return text.strip().lower() in ("1", "true", "yes")
    if typ is float:
        return float(text) if text != "" else 0.0
    if typ is int:
        return int(float(text)) if text != "" else 0
    if typ is date:
        return date.fromisoformat(text)
    return text


class CorruptDataError(RuntimeError):
    """A data file contains a value that cannot be parsed."""


class CsvTable[T: Record]:
    """Loads all records into memory; every mutation rewrites the file atomically.

    `all()` returns the live records and must be treated as read-only; `get()` returns a copy
    that can be modified and passed back to `update()`.

    Columns this version does not know (written by a newer version) are kept and written back
    unchanged, so opening the data with an older version never drops them.
    """

    def __init__(self, path: Path, cls: type[T], history: History | None = None):
        self.path = path
        self.cls = cls
        self.history = history  # records every change, for undo (F28)
        self.name = path.stem
        self._fields = [f.name for f in dataclasses.fields(cls)]
        self._types = typing.get_type_hints(cls)
        self._lock = threading.RLock()
        self._rows: dict[str, T] = {}
        self._extra_fields: list[str] = []
        self._extras: dict[str, dict[str, str]] = {}  # record id -> values of the unknown columns
        self._load()

    # ------------------------------------------------------------------ rows as strings (history, undo)
    def _raw_of(self, obj: T) -> dict[str, str]:
        raw = {k: _to_str(getattr(obj, k)) for k in self._fields}
        raw.update(self._extras.get(obj.id, {}))
        return raw

    def raw(self, record_id: str) -> dict[str, str] | None:
        """A record as the strings written to the file (unknown columns included), or None."""
        with self._lock:
            obj = self._rows.get(record_id)
            return self._raw_of(obj) if obj is not None else None

    def put_raw(self, rows: list[dict[str, str]]) -> None:
        """Insert or replace records given as strings (used by undo)."""
        with self._lock:
            changes = []
            for raw in rows:
                obj = self.cls(**{k: _from_str(raw[k], self._types[k]) for k in self._fields if k in raw})
                before = self.raw(obj.id)
                self._rows[obj.id] = obj
                extra = {k: v for k, v in raw.items() if k not in self._fields}
                if extra:
                    self._extras[obj.id] = extra
                    self._extra_fields += [k for k in extra if k not in self._extra_fields]
                changes.append(Change(self.name, obj.id, ADD if before is None else UPDATE, before, self.raw(obj.id)))
            if rows:
                self._save()
        self._record(changes)

    def _record(self, changes: list[Change]) -> None:
        if self.history is not None:
            self.history.record(self.name, changes)

    def _load(self) -> None:
        if not self.path.exists():
            self._save()
            return
        with self.path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            self._extra_fields = [c for c in reader.fieldnames or [] if c and c not in self._fields]
            for raw in reader:
                try:
                    kwargs = {k: _from_str(raw[k], self._types[k]) for k in self._fields if k in raw}
                    obj = self.cls(**kwargs)
                except (TypeError, ValueError) as exc:
                    raise CorruptDataError(f"{self.path} line {reader.line_num}: {exc}") from exc
                self._rows[obj.id] = obj
                if self._extra_fields:
                    self._extras[obj.id] = {c: raw.get(c) or "" for c in self._extra_fields}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".csv.tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(self._fields + self._extra_fields)
            for obj in self._rows.values():
                extra = self._extras.get(obj.id, {})
                writer.writerow(
                    [_to_str(getattr(obj, k)) for k in self._fields] + [extra.get(c, "") for c in self._extra_fields]
                )
            f.flush()
            os.fsync(f.fileno())  # never replace the old file with a partially written one
        tmp.replace(self.path)

    # ------------------------------------------------------------------ API
    def all(self) -> list[T]:
        with self._lock:
            return list(self._rows.values())

    def get(self, id_: str) -> T | None:
        obj = self._rows.get(id_)
        return copy.copy(obj) if obj is not None else None

    def add(self, obj: T) -> T:
        return self.add_many([obj])[0]

    def add_many(self, objs: Iterable[T]) -> list[T]:
        objs = list(objs)
        with self._lock:
            changes = []
            for obj in objs:
                before = self.raw(obj.id)
                self._rows[obj.id] = obj
                changes.append(Change(self.name, obj.id, ADD if before is None else UPDATE, before, self._raw_of(obj)))
            self._save()
        self._record(changes)
        return objs

    def update(self, obj: T) -> T:
        return self.update_many([obj])[0]

    def update_many(self, objs: Iterable[T]) -> list[T]:
        objs = list(objs)
        with self._lock:
            missing = [o.id for o in objs if o.id not in self._rows]
            if missing:
                raise KeyError(missing[0])
            changes = []
            for obj in objs:
                before = self.raw(obj.id)
                self._rows[obj.id] = obj
                after = self._raw_of(obj)
                if after != before:  # an unchanged record isn't worth an entry
                    changes.append(Change(self.name, obj.id, UPDATE, before, after))
            if objs:
                self._save()
        self._record(changes)
        return objs

    def delete(self, ids: Iterable[str]) -> int:
        with self._lock:
            ids = [i for i in ids if i in self._rows]
            changes = [Change(self.name, i, DELETE, self.raw(i), None) for i in ids]
            for i in ids:
                del self._rows[i]
                self._extras.pop(i, None)
            if ids:
                self._save()
        self._record(changes)
        return len(ids)

    def where(self, **conditions) -> list[T]:
        return [r for r in self.all() if all(getattr(r, k) == v for k, v in conditions.items())]
