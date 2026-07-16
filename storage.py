# storage.py
import logging
import sqlite3
import threading

from config import config
from models import Evaluation

logger = logging.getLogger("storage")


class Storage:
    """Кэш обработанных пар (vacancy_id, resume_id) + история оценок. SQLite."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or config.db_path
        # SQLite-соединение из нескольких потоков (ThreadPoolExecutor в анализаторе
        # его не трогает, но подстрахуемся) — check_same_thread=False + Lock.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()
        logger.info("Storage инициализирован: %s", self.db_path)

    def _init_tables(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS processed (
                    vacancy_id TEXT NOT NULL,
                    resume_id  TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (vacancy_id, resume_id)
                );

                CREATE TABLE IF NOT EXISTS evaluations (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    vacancy_id  TEXT NOT NULL,
                    resume_id   TEXT NOT NULL,
                    resume_url  TEXT,
                    score       INTEGER,
                    verdict     TEXT,
                    red_flags   TEXT,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            self._conn.commit()

    def is_processed(self, vacancy_id: str, resume_id: str) -> bool:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT 1 FROM processed WHERE vacancy_id = ? AND resume_id = ? LIMIT 1",
                    (vacancy_id, resume_id),
                ).fetchone()
            return row is not None
        except Exception as e:
            # При сбое БД безопаснее считать пару необработанной (лучше повтор, чем потеря).
            logger.error("is_processed(%s, %s) ошибка: %s", vacancy_id, resume_id, e)
            return False

    def mark_processed(self, vacancy_id: str, resume_id: str) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR IGNORE INTO processed (vacancy_id, resume_id) VALUES (?, ?)",
                    (vacancy_id, resume_id),
                )
                self._conn.commit()
        except Exception as e:
            logger.error("mark_processed(%s, %s) ошибка: %s", vacancy_id, resume_id, e)

    def save_evaluation(self, vacancy_id: str, evaluation: Evaluation) -> None:
        """История оценок (раздел 10, обратная связь). Вызывается опционально."""
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO evaluations
                        (vacancy_id, resume_id, resume_url, score, verdict, red_flags)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        vacancy_id,
                        evaluation.resume.id,
                        evaluation.resume.url,
                        evaluation.score,
                        evaluation.verdict,
                        evaluation.red_flags,
                    ),
                )
                self._conn.commit()
        except Exception as e:
            logger.error(
                "save_evaluation(%s, %s) ошибка: %s",
                vacancy_id, getattr(evaluation.resume, "id", "?"), e,
            )

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except Exception as e:
            logger.error("Ошибка закрытия Storage: %s", e)