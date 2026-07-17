# notifiers/telegram.py
import io
import logging
import os
import socket as _socket
import time
from typing import List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import connection as urllib3_connection

from config import config
from models import Vacancy, Evaluation
from notifiers.base import BaseNotifier

logger = logging.getLogger(__name__)

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
except ModuleNotFoundError as exc:
    # PDF — последний, необязательный этап. Его зависимость не должна мешать
    # запуску сборщика и Chrome.
    A4 = mm = colors = canvas = pdfmetrics = TTFont = None
    _REPORTLAB_IMPORT_ERROR = exc
else:
    _REPORTLAB_IMPORT_ERROR = None


def _allowed_gai_family():
    return _socket.AF_INET  # только IPv4


class _IPv4Adapter(HTTPAdapter):
    """Форсирует IPv4 для этой сессии (Telegram через IPv6 виснет на сервере)."""

    def init_poolmanager(self, *args, **kwargs):
        urllib3_connection.allowed_gai_family = _allowed_gai_family
        super().init_poolmanager(*args, **kwargs)


class TelegramNotifier(BaseNotifier):
    """Отправка PDF-отчёта по вакансии в Telegram (Bot API, sendDocument)."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        max_retries: int | None = None,
        backoff_sec: float | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        self.token = token or config.telegram_token
        self.chat_id = chat_id or config.telegram_chat_id
        self.max_retries = max_retries or config.telegram_max_retries
        self.backoff_sec = backoff_sec or config.telegram_retry_backoff_sec
        self.timeout_sec = timeout_sec or config.telegram_timeout_sec
        self.connect_timeout_sec = config.telegram_connect_timeout_sec
        self.send_doc_url = f"https://api.telegram.org/bot{self.token}/sendDocument"

        self.http = requests.Session()
        # Не подхватываем системные HTTP(S)_PROXY: маршрут задаётся только
        # конфигурацией этого клиента и никак не влияет на Selenium.
        self.http.trust_env = False
        if config.telegram_proxy_url:
            self.http.proxies.update(
                {
                    "http": config.telegram_proxy_url,
                    "https": config.telegram_proxy_url,
                }
            )
        # # Форс IPv4 для этой сессии.
        # self.http.mount("https://", _IPv4Adapter())
        # self.http.mount("http://", _IPv4Adapter())

    # --- Публичный интерфейс ---

    def send(self, vacancy: Vacancy, top: List[Evaluation]) -> None:
        """Сгенерировать PDF по вакансии и отправить его в Telegram."""
        try:
            if not top:
                # Если кандидатов нет — отправляем короткое текстовое уведомление.
                self._send_text_no_candidates(vacancy)
                return

            pdf_bytes, filename = self._build_pdf(vacancy, top)
            confirmed = self._send_pdf(pdf_bytes, filename, vacancy)
            if confirmed:
                logger.info(
                    "Telegram: отправлен PDF-отчёт по вакансии '%s' (%s)",
                    vacancy.title,
                    filename,
                )
        except Exception as e:  # noqa: BLE001
            # Сбой уведомления не должен ронять весь процесс (раздел 8).
            logger.error(
                "Telegram: не удалось отправить отчёт по вакансии '%s': %s",
                vacancy.title,
                e,
            )

    # --- Формирование PDF-отчёта ---

    def _ensure_font(self) -> str:
        """Зарегистрировать кириллический шрифт. Вернуть имя шрифта."""
        font_name = "DejaVuSans"

        # Уже зарегистрирован в этом процессе?
        if font_name in pdfmetrics.getRegisteredFontNames():
            return font_name

        font_path = config.font_path
        if not os.path.exists(font_path):
            logger.error(
                "Шрифт не найден: %s. Русский текст в PDF не отобразится. "
                "Скачай DejaVuSans.ttf в ./fonts/.",
                font_path,
            )
            return "Helvetica"

        pdfmetrics.registerFont(TTFont(font_name, font_path))
        return font_name

    def _wrap_text(self, text: str, font: str, size: int, max_width: float) -> list[str]:
        """Перенос текста по ширине max_width (в поинтах). Возвращает строки."""
        if not text:
            return []
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            trial = f"{current} {word}".strip()
            if pdfmetrics.stringWidth(trial, font, size) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def _score_color(self, score: int):
        """Цвет строки по score: зелёный/жёлтый/красный."""
        if score >= 75:
            return colors.HexColor("#1a7f37")  # зелёный
        if score >= 50:
            return colors.HexColor("#b58900")  # жёлтый/оранжевый
        return colors.HexColor("#c0392b")  # красный

    def _build_pdf(self, vacancy: Vacancy, top: List[Evaluation]) -> tuple[bytes, str]:
        """Сгенерировать читаемый PDF-отчёт (кириллица, перенос, ссылки, цвета)."""
        if _REPORTLAB_IMPORT_ERROR is not None:
            raise RuntimeError(
                "Для PDF-отчётов не установлен reportlab. "
                "Установите зависимости из requirements.txt."
            ) from _REPORTLAB_IMPORT_ERROR

        font = self._ensure_font()
        font_bold = font  # DejaVuSans один начертанием; при желании подключи Bold

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        margin = 15 * mm
        x = margin
        y = height - margin
        content_width = width - 2 * margin

        def new_page_if_needed(min_space: float) -> None:
            nonlocal y
            if y < margin + min_space:
                c.showPage()
                y = height - margin

        # --- Заголовок ---
        c.setFont(font_bold, 14)
        title = f"Вакансия: {vacancy.title or 'Без названия'}"
        for line in self._wrap_text(title, font_bold, 14, content_width):
            c.drawString(x, y, line)
            y -= 7 * mm

        c.setFont(font, 9)
        c.setFillColor(colors.blue)
        vacancy_url = vacancy.url or ""
        if vacancy_url:
            c.drawString(x, y, "Открыть вакансию на hh.ru")
            c.linkURL(vacancy_url, (x, y - 1, x + 60 * mm, y + 9), relative=0)
        c.setFillColor(colors.black)
        y -= 6 * mm

        c.setFont(font, 9)
        c.drawString(x, y, f"Кандидатов в отчёте: {len(top)}")
        y -= 8 * mm

        c.setLineWidth(0.5)
        c.line(x, y, width - margin, y)
        y -= 8 * mm

        # --- Кандидаты (карточками, а не таблицей — читаемее для длинного текста) ---
        for idx, ev in enumerate(top, start=1):
            score = int(ev.score)
            verdict = (ev.verdict or "").strip()
            red_flags = (ev.red_flags or "").strip()
            resume_url = ev.resume.url or ""

            # Резервируем место, иначе новая страница
            new_page_if_needed(35 * mm)

            # Строка "N. Score"
            c.setFont(font_bold, 11)
            c.setFillColor(self._score_color(score))
            c.drawString(x, y, f"{idx}. Оценка: {score}/100")
            c.setFillColor(colors.black)
            y -= 6 * mm

            # Ссылка на резюме (кликабельная)
            if resume_url:
                c.setFont(font, 9)
                c.setFillColor(colors.blue)
                c.drawString(x, y, "Открыть резюме")
                c.linkURL(resume_url, (x, y - 1, x + 40 * mm, y + 9), relative=0)
                c.setFillColor(colors.black)
                y -= 6 * mm

            # Вердикт (с переносом)
            if verdict:
                c.setFont(font, 9)
                for line in self._wrap_text("Плюс: " + verdict, font, 9, content_width):
                    new_page_if_needed(10 * mm)
                    c.drawString(x, y, line)
                    y -= 4.5 * mm

            # Риски (с переносом)
            if red_flags:
                c.setFont(font, 9)
                c.setFillColor(colors.HexColor("#c0392b"))
                for line in self._wrap_text("Риск: " + red_flags, font, 9, content_width):
                    new_page_if_needed(10 * mm)
                    c.drawString(x, y, line)
                    y -= 4.5 * mm
                c.setFillColor(colors.black)

            # Разделитель между кандидатами
            y -= 3 * mm
            c.setStrokeColor(colors.HexColor("#dddddd"))
            c.line(x, y, width - margin, y)
            c.setStrokeColor(colors.black)
            y -= 5 * mm

        c.showPage()
        c.save()

        pdf_bytes = buffer.getvalue()
        buffer.close()

        safe_title = "".join(
            ch if ch.isalnum() else "_" for ch in (vacancy.title or "vacancy")
        )[:40]
        filename = f"recruiter_{safe_title}_top.pdf"
        return pdf_bytes, filename

    # --- Отправка PDF в Telegram ---

    def _send_pdf(self, pdf_bytes: bytes, filename: str, vacancy: Vacancy) -> bool:
        files = {
            "document": (filename, pdf_bytes, "application/pdf"),
        }
        data = {
            "chat_id": self.chat_id,
            "caption": f"Топ кандидатов по вакансии: {vacancy.title}",
            "parse_mode": "HTML",
            "disable_notification": False,
        }

        last_error: str | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.http.post(
                    self.send_doc_url,
                    data=data,
                    files=files,
                    timeout=(self.connect_timeout_sec, self.timeout_sec),
                )
                if resp.status_code == 200:
                    return True
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code < 500 and resp.status_code != 429:
                    raise RuntimeError(last_error)
                logger.warning(
                    "Telegram: попытка %d/%d отправки PDF неуспешна (%s)",
                    attempt,
                    self.max_retries,
                    last_error,
                )
            except requests.RequestException as e:  # noqa: BLE001
                last_error = str(e)
                # Запрос уже мог быть принят Telegram, а потерялся только ответ.
                # Повтор после ReadTimeout создаёт дубликаты документов.
                if isinstance(e, requests.exceptions.ReadTimeout):
                    logger.warning(
                        "Telegram: ответ на отправку PDF не получен вовремя. "
                        "Повтор отключён во избежание дубликатов; проверьте чат."
                    )
                    return False
                logger.warning(
                    "Telegram: попытка %d/%d — %s при отправке PDF: %s",
                    attempt,
                    self.max_retries,
                    type(e).__name__,
                    e,
                )

            if attempt < self.max_retries:
                time.sleep(self.backoff_sec * attempt)

        raise RuntimeError(f"Не удалось отправить PDF в Telegram: {last_error}")

    # --- Текстовое уведомление, если кандидатов нет ---

    def _send_text_no_candidates(self, vacancy: Vacancy) -> None:
        text = (
            f"Вакансия: {vacancy.title}\n"
            f"URL: {vacancy.url}\n\n"
            "Подходящих кандидатов не найдено."
        )
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        last_error: str | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.http.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json=payload,
                    timeout=(self.connect_timeout_sec, self.timeout_sec),
                )
                if resp.status_code == 200:
                    return
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                logger.warning(
                    "Telegram: попытка %d/%d отправки текста неуспешна (%s)",
                    attempt,
                    self.max_retries,
                    last_error,
                )
            except requests.RequestException as e:  # noqa: BLE001
                last_error = str(e)
                if isinstance(e, requests.exceptions.ReadTimeout):
                    logger.warning(
                        "Telegram: ответ на отправку текста не получен вовремя. "
                        "Повтор отключён во избежание дубликатов; проверьте чат."
                    )
                    return
                logger.warning(
                    "Telegram: попытка %d/%d — %s при отправке текста: %s",
                    attempt,
                    self.max_retries,
                    type(e).__name__,
                    e,
                )

            if attempt < self.max_retries:
                time.sleep(self.backoff_sec * attempt)

        logger.error(
            "Telegram: не удалось отправить текстовое уведомление по вакансии '%s': %s",
            vacancy.title,
            last_error,
        )
