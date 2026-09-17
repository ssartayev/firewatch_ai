"""
Слой хранения событий — SQLite (стандартный модуль sqlite3, без внешних ORM).

Таблица events хранит каждое пожароопасное событие + статус условий наряда
(огнетушитель / наблюдающий) + путь к снапшоту-доказательству.

Соединения открываются на каждую операцию (по одному на вызов) — это делает
модуль безопасным при обращении из разных потоков (фоновый пайплайн + FastAPI).
Включён WAL для нормальной параллельной работы чтения/записи.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "firewatch.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                    TEXT    NOT NULL,   -- ISO-8601 UTC, время события
    zone_id               TEXT    NOT NULL,
    event_type            TEXT    NOT NULL,   -- 'fire' | 'smoke'
    confidence            REAL    NOT NULL,
    extinguisher_present  INTEGER,            -- 1/0/NULL (NULL = проверка не настроена)
    observer_present      INTEGER,            -- 1/0/NULL
    snapshot_path         TEXT,               -- относительный путь к кадру-доказательству
    permit_number         TEXT,               -- номер наряда-допуска (вводится вручную)
    created_at            TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_zone ON events(zone_id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EventStore:
    """CRUD-обёртка над таблицей событий."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    def insert_event(
        self,
        *,
        zone_id: str,
        event_type: str,
        confidence: float,
        extinguisher_present: Optional[bool],
        observer_present: Optional[bool],
        snapshot_path: Optional[str],
        ts: Optional[str] = None,
        permit_number: Optional[str] = None,
    ) -> int:
        """Записать событие. Возвращает id новой строки."""
        ts = ts or _now_iso()

        def _b(v: Optional[bool]) -> Optional[int]:
            return None if v is None else int(bool(v))

        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO events
                   (ts, zone_id, event_type, confidence, extinguisher_present,
                    observer_present, snapshot_path, permit_number, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (ts, zone_id, event_type, float(confidence), _b(extinguisher_present),
                 _b(observer_present), snapshot_path, permit_number, _now_iso()),
            )
            return int(cur.lastrowid)

    def list_events(
        self,
        *,
        zone_id: Optional[str] = None,
        date_from: Optional[str] = None,   # 'YYYY-MM-DD'
        date_to: Optional[str] = None,     # 'YYYY-MM-DD'
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Список событий с фильтрами по зоне и дате (сначала свежие)."""
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if zone_id:
            sql += " AND zone_id = ?"
            params.append(zone_id)
        if date_from:
            sql += " AND ts >= ?"
            params.append(date_from)
        if date_to:
            # включительно по дате: до конца указанного дня
            sql += " AND ts <= ?"
            params.append(date_to + "T23:59:59")
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_event(self, event_id: int) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    def get_last_event(self) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def set_permit(self, event_id: int, permit_number: str) -> bool:
        """Привязать номер наряда к событию. True, если строка найдена."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE events SET permit_number = ? WHERE id = ?",
                (permit_number, event_id),
            )
            return cur.rowcount > 0
