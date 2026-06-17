from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import IncidentEvent


class EventStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    track_id INTEGER,
                    label TEXT,
                    zone TEXT,
                    clip_path TEXT,
                    frame_path TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    event_ids TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS object_label_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    base_label TEXT NOT NULL,
                    bbox_norm TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_track_id INTEGER,
                    match_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
                CREATE INDEX IF NOT EXISTS idx_object_label_base ON object_label_profiles(base_label);
                """
            )

    def insert_event(self, event: IncidentEvent) -> int:
        payload = event.as_dict()
        with self._lock, self._connect() as con:
            cur = con.execute(
                """
                INSERT INTO events (
                    timestamp, event_type, severity, summary, track_id, label, zone,
                    clip_path, frame_path, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["timestamp"],
                    event.event_type,
                    event.severity,
                    event.summary,
                    event.track_id,
                    event.label,
                    event.zone,
                    event.clip_path,
                    event.frame_path,
                    json.dumps(event.metadata),
                ),
            )
            return int(cur.lastrowid)

    def list_events(self, limit: int = 100, event_type: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM events"
        params: list[Any] = []
        if event_type:
            query += " WHERE event_type = ?"
            params.append(event_type)
        query += " ORDER BY timestamp DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def recent_events(self, limit: int = 25) -> list[dict[str, Any]]:
        return list(reversed(self.list_events(limit=limit)))

    def prune_events(self, keep: int) -> list[dict[str, Any]]:
        with self._lock, self._connect() as con:
            rows = con.execute(
                """
                SELECT * FROM events
                WHERE id NOT IN (
                    SELECT id FROM events
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                )
                ORDER BY timestamp ASC, id ASC
                """,
                (keep,),
            ).fetchall()
            if rows:
                con.executemany("DELETE FROM events WHERE id = ?", [(row["id"],) for row in rows])
        return [self._row_to_dict(row) for row in rows]

    def insert_report(self, title: str, body: str, event_ids: list[int], created_at: str) -> int:
        with self._lock, self._connect() as con:
            cur = con.execute(
                """
                INSERT INTO reports (created_at, title, body, event_ids)
                VALUES (?, ?, ?, ?)
                """,
                (created_at, title, body, json.dumps(event_ids)),
            )
            return int(cur.lastrowid)

    def list_reports(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM reports ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        reports: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["event_ids"] = json.loads(item["event_ids"])
            reports.append(item)
        return reports

    def prune_reports(self, keep: int) -> int:
        with self._lock, self._connect() as con:
            rows = con.execute(
                """
                SELECT id FROM reports
                WHERE id NOT IN (
                    SELECT id FROM reports
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                )
                """,
                (keep,),
            ).fetchall()
            if rows:
                con.executemany("DELETE FROM reports WHERE id = ?", [(row["id"],) for row in rows])
        return len(rows)

    def list_object_label_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT * FROM object_label_profiles
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
        return [self._object_label_row_to_dict(row) for row in rows]

    def create_object_label_profile(
        self,
        name: str,
        base_label: str,
        bbox_norm: tuple[float, float, float, float],
        track_id: int,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as con:
            cur = con.execute(
                """
                INSERT INTO object_label_profiles (
                    name, base_label, bbox_norm, created_at, updated_at, last_track_id, match_count
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (name, base_label, json.dumps(list(bbox_norm)), now, now, track_id),
            )
            row = con.execute(
                "SELECT * FROM object_label_profiles WHERE id = ?",
                (int(cur.lastrowid),),
            ).fetchone()
        return self._object_label_row_to_dict(row)

    def update_object_label_profile(
        self,
        profile_id: int,
        name: str,
        base_label: str,
        bbox_norm: tuple[float, float, float, float],
        track_id: int,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as con:
            con.execute(
                """
                UPDATE object_label_profiles
                SET name = ?,
                    base_label = ?,
                    bbox_norm = ?,
                    updated_at = ?,
                    last_track_id = ?,
                    match_count = match_count + 1
                WHERE id = ?
                """,
                (name, base_label, json.dumps(list(bbox_norm)), _utc_now(), track_id, profile_id),
            )
            row = con.execute(
                "SELECT * FROM object_label_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        return self._object_label_row_to_dict(row) if row else None

    def delete_object_label_profile(self, profile_id: int) -> bool:
        with self._lock, self._connect() as con:
            cur = con.execute("DELETE FROM object_label_profiles WHERE id = ?", (profile_id,))
            return cur.rowcount > 0

    def clear_object_label_profiles(self) -> int:
        with self._lock, self._connect() as con:
            cur = con.execute("DELETE FROM object_label_profiles")
            return cur.rowcount

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.get("metadata") or "{}")
        return item

    @staticmethod
    def _object_label_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["bbox_norm"] = json.loads(item["bbox_norm"])
        return item


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
