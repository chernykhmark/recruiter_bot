# logging_setup.py
import logging
import sys

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Шумные сторонние логгеры — приглушаем до WARNING.
_NOISY_LOGGERS = (
    "urllib3",
    "selenium",
    "undetected_chromedriver",
    "openai",
    "httpx",
    "httpcore",
)


def setup_logging(level: int = logging.INFO, log_file: str = "recruiter_bot.log") -> None:
    """Единая настройка логирования для всех модулей. Вызывать один раз в main."""
    root = logging.getLogger()
    root.setLevel(level)

    # Защита от повторной настройки (например, при перезапуске в тестах).
    if root.handlers:
        return

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except Exception as e:  # noqa: BLE001
        root.warning("Не удалось создать файловый лог '%s': %s", log_file, e)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)