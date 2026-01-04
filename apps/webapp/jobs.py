from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY,
                  created_at TEXT NOT NULL,
                  started_at TEXT,
                  finished_at TEXT,
                  status TEXT NOT NULL,
                  argv_json TEXT NOT NULL,
                  outputs_json TEXT NOT NULL,
                  returncode INTEGER,
                  stdout TEXT,
                  stderr TEXT
                )
                """
            )

    def create_job(self, *, argv: List[str], outputs: Dict[str, str]) -> str:
        job_id = uuid.uuid4().hex
        with self._connect() as con:
            con.execute(
                "INSERT INTO jobs (id, created_at, status, argv_json, outputs_json) VALUES (?, ?, ?, ?, ?)",
                (job_id, _utcnow(), "queued", json.dumps(argv), json.dumps(outputs)),
            )
        return job_id

    def set_started(self, job_id: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE jobs SET status=?, started_at=? WHERE id=?",
                ("running", _utcnow(), job_id),
            )

    def set_finished(
        self,
        *,
        job_id: str,
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        status = "succeeded" if returncode == 0 else "failed"
        with self._connect() as con:
            con.execute(
                "UPDATE jobs SET status=?, finished_at=?, returncode=?, stdout=?, stderr=? WHERE id=?",
                (status, _utcnow(), int(returncode), stdout, stderr, job_id),
            )

    def set_failed(self, *, job_id: str, stderr: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE jobs SET status=?, finished_at=?, returncode=?, stderr=? WHERE id=?",
                ("failed", _utcnow(), 1, stderr, job_id),
            )

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["argv"] = json.loads(d.pop("argv_json"))
        d["outputs"] = json.loads(d.pop("outputs_json"))
        return d

    def list_jobs(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, created_at, started_at, finished_at, status, returncode FROM jobs ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

