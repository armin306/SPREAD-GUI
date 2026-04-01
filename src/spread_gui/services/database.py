"""
SQLite-backed project/crystal database.

Schema
------
projects  (id, name, created_at)
crystals  (id, project_id, name, visit, data_path, proc_path,
           settings TEXT,          -- JSON with all remaining form fields
           created_at, updated_at)

The *settings* JSON uses exactly the same key/value pairs as settings.ini
so that ProcessingTab._apply_form_state() can consume either source
without any conversion.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Simple data containers
# ---------------------------------------------------------------------------

class Project:
    __slots__ = ("id", "name", "created_at")

    def __init__(self, id_: int, name: str, created_at: str) -> None:
        self.id = id_
        self.name = name
        self.created_at = created_at

    def __repr__(self) -> str:  # pragma: no cover
        return f"Project(id={self.id}, name={self.name!r})"


class Crystal:
    __slots__ = ("id", "project_id", "name", "visit", "data_path", "proc_path",
                 "created_at", "updated_at")

    def __init__(self, id_: int, project_id: int, name: str,
                 visit: str, data_path: str, proc_path: str,
                 created_at: str, updated_at: str) -> None:
        self.id         = id_
        self.project_id = project_id
        self.name       = name
        self.visit      = visit
        self.data_path  = data_path
        self.proc_path  = proc_path
        self.created_at = created_at
        self.updated_at = updated_at

    def __repr__(self) -> str:  # pragma: no cover
        return f"Crystal(id={self.id}, name={self.name!r})"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS crystals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    visit      TEXT    NOT NULL DEFAULT '',
    data_path  TEXT    NOT NULL DEFAULT '',
    proc_path  TEXT    NOT NULL DEFAULT '',
    settings   TEXT    NOT NULL DEFAULT '{}',
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    UNIQUE(project_id, name)
);
"""


class ProjectDB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._init()

    # ---- internal ----

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._path))
        con.execute("PRAGMA foreign_keys = ON")
        con.row_factory = sqlite3.Row
        return con

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init(self) -> None:
        with self._tx() as con:
            con.executescript(_SCHEMA)

    # ---- projects ----

    def get_projects(self) -> list[Project]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, name, created_at FROM projects ORDER BY name"
            ).fetchall()
        return [Project(r["id"], r["name"], r["created_at"]) for r in rows]

    def create_project(self, name: str) -> Project:
        now = _now()
        with self._tx() as con:
            cur = con.execute(
                "INSERT INTO projects (name, created_at) VALUES (?, ?)", (name, now)
            )
            return Project(cur.lastrowid, name, now)

    def rename_project(self, project_id: int, new_name: str) -> None:
        with self._tx() as con:
            con.execute("UPDATE projects SET name=? WHERE id=?", (new_name, project_id))

    def delete_project(self, project_id: int) -> None:
        with self._tx() as con:
            con.execute("DELETE FROM projects WHERE id=?", (project_id,))

    # ---- crystals ----

    def get_crystals(self, project_id: int) -> list[Crystal]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, project_id, name, visit, data_path, proc_path, "
                "created_at, updated_at FROM crystals WHERE project_id=? ORDER BY name",
                (project_id,),
            ).fetchall()
        return [Crystal(r["id"], r["project_id"], r["name"],
                        r["visit"], r["data_path"], r["proc_path"],
                        r["created_at"], r["updated_at"]) for r in rows]

    def create_crystal(
        self,
        project_id: int,
        name: str,
        visit: str = "",
        data_path: str = "",
        proc_path: str = "",
        initial_settings: Optional[dict] = None,
    ) -> Crystal:
        now = _now()
        # Build initial settings: start with provided dict, then override the
        # three path fields so they are always consistent.
        s: dict = dict(initial_settings) if initial_settings else {}
        s["crystal"]   = name
        s["visit"]     = visit
        s["data_path"] = data_path
        s["proc_path"] = proc_path
        settings_json = json.dumps(s)
        with self._tx() as con:
            cur = con.execute(
                "INSERT INTO crystals "
                "(project_id, name, visit, data_path, proc_path, settings, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, name, visit, data_path, proc_path, settings_json, now, now),
            )
        return Crystal(cur.lastrowid, project_id, name, visit, data_path, proc_path, now, now)

    def delete_crystal(self, crystal_id: int) -> None:
        with self._tx() as con:
            con.execute("DELETE FROM crystals WHERE id=?", (crystal_id,))

    def update_crystal(self, crystal_id: int, settings: dict) -> None:
        """Persist the full form-state dict for *crystal_id*."""
        now = _now()
        visit     = settings.get("visit",     "")
        data_path = settings.get("data_path", "")
        proc_path = settings.get("proc_path", "")
        with self._tx() as con:
            con.execute(
                "UPDATE crystals SET visit=?, data_path=?, proc_path=?, "
                "settings=?, updated_at=? WHERE id=?",
                (visit, data_path, proc_path, json.dumps(settings), now, crystal_id),
            )

    def get_crystal_settings(self, crystal_id: int) -> dict:
        """Return the stored form-state dict for *crystal_id*."""
        with self._connect() as con:
            row = con.execute(
                "SELECT settings FROM crystals WHERE id=?", (crystal_id,)
            ).fetchone()
        if row is None:
            return {}
        return json.loads(row["settings"])

    def patch_crystal_settings(self, crystal_id: int, patch: dict) -> None:
        """Merge *patch* into the existing settings JSON for *crystal_id*."""
        existing = self.get_crystal_settings(crystal_id)
        existing.update(patch)
        self.update_crystal(crystal_id, existing)

    def get_crystal_info(self, crystal_id: int) -> tuple[str, str]:
        """Return (project_name, crystal_name) for the given crystal id."""
        with self._connect() as con:
            row = con.execute(
                "SELECT p.name AS pname, c.name AS cname "
                "FROM crystals c JOIN projects p ON c.project_id = p.id "
                "WHERE c.id=?",
                (crystal_id,),
            ).fetchone()
        if row is None:
            return ("", "")
        return (row["pname"], row["cname"])
