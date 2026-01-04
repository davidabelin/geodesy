from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .jobs import JobStore


class JobRunner:
    def __init__(self, *, job_store: JobStore, cwd: Path):
        self.job_store = job_store
        self.cwd = Path(cwd)
        self.pool = ThreadPoolExecutor(max_workers=2)

    def submit(self, job_id: str) -> None:
        self.pool.submit(self._run_job, job_id)

    def _run_job(self, job_id: str) -> None:
        job = self.job_store.get_job(job_id)
        if not job:
            return
        argv = job["argv"]
        cmd = [sys.executable] + argv
        self.job_store.set_started(job_id)
        try:
            p = subprocess.run(
                cmd,
                cwd=str(self.cwd),
                capture_output=True,
                text=True,
            )
            self.job_store.set_finished(
                job_id=job_id,
                returncode=int(p.returncode),
                stdout=p.stdout or "",
                stderr=p.stderr or "",
            )
        except Exception as e:
            self.job_store.set_failed(job_id=job_id, stderr=str(e))

