# config.py
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass
class Config:
    # --- Секреты (из .env) ---
    openai_api_key: str = _get("OPENAI_API_KEY", "PLACEHOLDER_OPENAI_KEY")
    telegram_token: str = _get("TELEGRAM_TOKEN", "PLACEHOLDER_TG_TOKEN")
    telegram_chat_id: str = _get("TELEGRAM_CHAT_ID", "PLACEHOLDER_CHAT_ID")

    # --- Настройки LLM ---
    llm_model: str = _get("LLM_MODEL", "gpt-4o-mini")
    filter_batch_size: int = int(_get("FILTER_BATCH_SIZE", "8"))
    analyzer_workers: int = int(_get("ANALYZER_WORKERS", "5"))
    top_n: int = int(_get("TOP_N", "20"))

    # Этап 1 (батч-фильтр мусора). False = тестируем только этап 2.
    analyzer_filter_enabled: bool = _get("ANALYZER_FILTER_ENABLED", "false").lower() == "true"
    # Максимум символов резюме, уходящих в фильтр (экономия токенов).
    filter_resume_chars: int = int(_get("FILTER_RESUME_CHARS", "1200"))
    # Retry для запросов к LLM.
    llm_max_retries: int = int(_get("LLM_MAX_RETRIES", "3"))
    llm_retry_backoff_sec: float = float(_get("LLM_RETRY_BACKOFF_SEC", "2.0"))

    # --- Настройки парсера ---
    max_resumes_per_vacancy: int = int(_get("MAX_RESUMES_PER_VACANCY", "25"))
    parser_delay_sec: float = float(_get("PARSER_DELAY_SEC", "1.0"))
    chrome_session_path: str = _get("CHROME_SESSION_PATH", "./chrome_session")
    chrome_version_main: int = int(_get("CHROME_VERSION_MAIN", "150"))
    vacancies_url: str = _get("VACANCIES_URL", "https://hh.ru/employer/vacancies")

    # --- Хранилище ---
    db_path: str = _get("DB_PATH", "./recruiter_bot.db")

    # --- Telegram ---
    telegram_max_retries: int = int(_get("TELEGRAM_MAX_RETRIES", "3"))
    telegram_retry_backoff_sec: float = float(_get("TELEGRAM_RETRY_BACKOFF_SEC", "2.0"))
    telegram_timeout_sec: float = float(_get("TELEGRAM_TIMEOUT_SEC", "20.0"))


config = Config()