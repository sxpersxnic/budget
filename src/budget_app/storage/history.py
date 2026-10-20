"""Change history and undo (F28).

Every change to a table (`CsvTable.add_many`, `update_many`, `delete`) is recorded with the
record before and after, as the strings written to the CSV file. Changes made together form one
*action*: a service or page opens one with `with store.history.action(N_("…"))`, and actions
opened inside it join it, so saving a transaction with its items, or an import with its balances,
is one step to undo.

Undo reverts an action's changes in reverse order. It first checks that every record still looks
the way the action left it; if something changed it since, nothing is undone.

The history is kept in `history.jsonl` in the data folder (one action per line, the newest
`KEEP` actions). It is not part of backups: it describes changes, not records.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import threading
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

KEEP = 500
ADD, UPDATE, DELETE = "add", "update", "delete"


class UndoConflict(RuntimeError):
    """A record was changed after the action that is to be undone."""


class Table(Protocol):
    """What undo needs from a table (CsvTable provides it)."""

    def raw(self, record_id: str) -> dict[str, str] | None: ...

    def put_raw(self, rows: list[dict[str, str]]) -> None: ...

    def delete(self, ids: list[str]) -> int: ...


@dataclass
class Change:
    table: str
    record_id: str
    op: str  # ADD | UPDATE | DELETE
    before: dict[str, str] | None = None
    after: dict[str, str] | None = None


@dataclass
class Action:
    label: str  # an English text marked with N_(), translated when shown
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    changes: list[Change] = field(default_factory=list)
    undone: bool = False
    undo_of: str = ""  # set on the action that undid another one
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def tables(self) -> set[str]:
        return {c.table for c in self.changes}


_open: contextvars.ContextVar[Action | None] = contextvars.ContextVar("budget_history_action", default=None)


class History:
    def __init__(self, path: Path, keep: int = KEEP):
        self.path = path
        self.keep = keep
        self._lock = threading.RLock()
        self._actions: list[Action] = []  # oldest first
        # each action as written, so a save serializes only new or changed actions; one large import
        # would otherwise make every later save slow
        self._lines: dict[str, str] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    act = _action_from(json.loads(line))
                    self._actions.append(act)
                    self._lines[act.id] = line

    # ------------------------------------------------------------------ recording
    @contextlib.contextmanager
    def action(self, label: str) -> Iterator[Action]:
        """Group the changes made inside into one action; inside another action, join that one."""
        current = _open.get()
        if current is not None:
            yield current
            return
        act = Action(label)
        token = _open.set(act)
        try:
            yield act
        finally:
            _open.reset(token)
            if act.changes:
                self._append(act)

    def record(self, table: str, changes: list[Change]) -> None:
        """Called by the tables after a write."""
        if not changes:
            return
        for c in changes:
            c.table = table
        current = _open.get()
        if current is not None:
            current.changes.extend(changes)
        else:  # a change outside any action is an action of its own
            self._append(Action("", changes=list(changes)))

    def _append(self, act: Action) -> None:
        with self._lock:
            self._actions.append(act)
            for dropped in self._actions[: -self.keep]:
                self._lines.pop(dropped.id, None)
            del self._actions[: -self.keep]
            self._save()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for act in self._actions:
                if act.id not in self._lines:
                    self._lines[act.id] = json.dumps(asdict(act), ensure_ascii=False)
                f.write(self._lines[act.id] + "\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self.path)

    # ------------------------------------------------------------------ reading
    def actions(self, limit: int | None = None) -> list[Action]:
        """Newest first."""
        with self._lock:
            out = list(reversed(self._actions))
        return out[:limit] if limit else out

    def get(self, action_id: str) -> Action | None:
        return next((a for a in self._actions if a.id == action_id), None)

    # ------------------------------------------------------------------ undo
    def undo(self, action_id: str, tables: Mapping[str, Table], label: str) -> Action:
        """Revert an action. Raises KeyError for an unknown action, UndoConflict if it can't be undone."""
        act = self.get(action_id)
        if act is None:
            raise KeyError(action_id)
        if act.undone:
            raise UndoConflict("already undone")
        missing = act.tables - set(tables)
        if missing:
            raise UndoConflict(f"unknown table {sorted(missing)[0]}")
        # every record must still be as the action left it
        final: dict[tuple[str, str], dict[str, str] | None] = {}
        for c in act.changes:
            final[(c.table, c.record_id)] = c.after  # the last change to a record counts
        for (table, rid), expected in final.items():
            if tables[table].raw(rid) != expected:
                raise UndoConflict(f"{table} {rid} changed since")
        with self.action(label) as undoing:
            for c in reversed(act.changes):
                target = tables[c.table]
                if c.before is None:
                    target.delete([c.record_id])
                else:
                    target.put_raw([c.before])
            undoing.undo_of = act.id
        with self._lock:
            act.undone = True
            self._lines.pop(act.id, None)  # written again with its new state
            self._save()
        return undoing


def _action_from(data: dict[str, Any]) -> Action:
    changes = [Change(**c) for c in data.pop("changes", [])]
    return Action(changes=changes, **data)
