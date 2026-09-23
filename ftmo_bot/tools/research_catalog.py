"""Durable registry for reproducible Parquet research runs."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any


class ResearchCatalog:
    """Store run/job provenance separately from market-data sync metadata."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.root / 'research.sqlite')
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS research_runs (
                run_id TEXT PRIMARY KEY,
                created_at_utc TEXT NOT NULL,
                completed_at_utc TEXT,
                status TEXT NOT NULL,
                configuration_json TEXT NOT NULL,
                git_commit TEXT NOT NULL,
                manifest_path TEXT NOT NULL,
                summary_json TEXT
            );
            CREATE TABLE IF NOT EXISTS research_jobs (
                run_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                strategy TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                status TEXT NOT NULL,
                data_fingerprint TEXT,
                data_snapshot_json TEXT,
                report_path TEXT,
                metrics_json TEXT,
                error_type TEXT,
                error_message TEXT,
                PRIMARY KEY (run_id, job_id),
                FOREIGN KEY (run_id) REFERENCES research_runs(run_id)
            );
            CREATE INDEX IF NOT EXISTS idx_research_jobs_lookup
                ON research_jobs(strategy, symbol, timeframe, status);
        ''')
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    def start_run(self, run_id: str, created_at_utc: str, configuration: dict,
                  git_commit: str, manifest_path: Path) -> None:
        self.connection.execute('''
            INSERT INTO research_runs(run_id, created_at_utc, status, configuration_json,
                                      git_commit, manifest_path)
            VALUES (?, ?, 'running', ?, ?, ?)
        ''', (run_id, created_at_utc, self._json(configuration), git_commit, str(manifest_path)))
        self.connection.commit()

    def start_job(self, run_id: str, job_id: str, strategy: str, symbol: str,
                  timeframe: str, snapshot: dict) -> None:
        self.connection.execute('''
            INSERT OR REPLACE INTO research_jobs(
                run_id, job_id, strategy, symbol, timeframe, status,
                data_fingerprint, data_snapshot_json
            ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
        ''', (run_id, job_id, strategy, symbol, timeframe, snapshot['fingerprint'], self._json(snapshot)))
        self.connection.commit()

    def complete_job(self, run_id: str, job_id: str, report_path: Path, metrics: dict) -> None:
        self.connection.execute('''
            UPDATE research_jobs SET status='completed', report_path=?, metrics_json=?,
                error_type=NULL, error_message=NULL WHERE run_id=? AND job_id=?
        ''', (str(report_path), self._json(metrics), run_id, job_id))
        self.connection.commit()

    def fail_job(self, run_id: str, job_id: str, error: Exception) -> None:
        self.connection.execute('''
            UPDATE research_jobs SET status='failed', error_type=?, error_message=?
            WHERE run_id=? AND job_id=?
        ''', (type(error).__name__, str(error), run_id, job_id))
        self.connection.commit()

    def finish_run(self, run_id: str, completed_at_utc: str, status: str, summary: dict) -> None:
        self.connection.execute('''
            UPDATE research_runs SET completed_at_utc=?, status=?, summary_json=? WHERE run_id=?
        ''', (completed_at_utc, status, self._json(summary), run_id))
        self.connection.commit()

