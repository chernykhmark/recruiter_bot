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
    # Используется только клиентами OpenAI и Telegram. Chrome/Selenium этот
    # параметр не получает и продолжает подключаться напрямую.
    openai_proxy_url: str = _get("OPENAI_PROXY_URL")
    telegram_proxy_url: str = _get("TELEGRAM_PROXY_URL")
    shadowsocks_enabled: bool = _get("SHADOWSOCKS_ENABLED", "false").lower() == "true"
    shadowsocks_binary: str = _get(
        "SHADOWSOCKS_BINARY", "./tools/shadowsocks/sslocal"
    )
    shadowsocks_telegram_binary: str = _get(
        "SHADOWSOCKS_TELEGRAM_BINARY", "/opt/homebrew/bin/ss-local"
    )
    shadowsocks_server: str = _get("SHADOWSOCKS_SERVER")
    shadowsocks_server_port: int = int(_get("SHADOWSOCKS_SERVER_PORT", "0"))
    shadowsocks_password: str = _get("SHADOWSOCKS_PASSWORD")
    shadowsocks_method: str = _get(
        "SHADOWSOCKS_METHOD", "chacha20-ietf-poly1305"
    )
    shadowsocks_local_port: int = int(_get("SHADOWSOCKS_LOCAL_PORT", "1090"))

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
    parser_delay_sec: float = float(_get("PARSER_DELAY_SEC", "1.0"))
    chrome_session_path: str = _get("CHROME_SESSION_PATH", "./chrome_session")
    chrome_version_main: int = int(_get("CHROME_VERSION_MAIN", "150"))
    chrome_manual_login: bool = _get("CHROME_MANUAL_LOGIN", "false").lower() == "true"
    vacancies_url: str = _get("VACANCIES_URL", "https://hh.ru/employer/vacancies")
    max_response_pages: int = int(_get("MAX_RESPONSE_PAGES", "100"))
    font_path: str = _get("PDF_FONT_PATH", "./fonts/DejaVuSans.ttf")

    # --- Хранилище ---
    db_path: str = _get("DB_PATH", "./recruiter_bot.db")

    # --- Telegram ---
    telegram_max_retries: int = int(_get("TELEGRAM_MAX_RETRIES", "3"))
    telegram_retry_backoff_sec: float = float(_get("TELEGRAM_RETRY_BACKOFF_SEC", "2.0"))
    telegram_connect_timeout_sec: float = float(
        _get("TELEGRAM_CONNECT_TIMEOUT_SEC", "30.0")
    )
    telegram_timeout_sec: float = float(_get("TELEGRAM_TIMEOUT_SEC", "180.0"))


config = Config()
