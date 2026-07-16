# notifiers/telegram.py
import html
import logging
import time

import requests

from config import config
from models import Vacancy, Evaluation
from notifiers.base import BaseNotifier

logger = logging.getLogger(__name__)

# Лимит Telegram — 4096 символов. Берём с запасом.
TELEGRAM_MSG_LIMIT = 4096
SAFE_LIMIT = 3900


class TelegramNotifier(BaseNotifier):
    """Отправка дайджеста по вакансии в Telegram (Bot API, HTML-разметка)."""

    def __init__(
        self,
        token: str = None,
        chat_id: str = None,
        max_retries: int = None,
        backoff_sec: float = None,
        timeout_sec: float = None,
    ):
        self.token = token or config.telegram_token
        self.chat_id = chat_id or config.telegram_chat_id
        self.max_retries = max_retries or config.telegram_max_retries
        self.backoff_sec = backoff_sec or config.telegram_retry_backoff_sec
        self.timeout_sec = timeout_sec or config.telegram_timeout_sec
        self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    # --- Публичный интерфейс ---

    def send(self, vacancy: Vacancy, top: list[Evaluation]) -> None:
        try:
            text = self._build_digest(vacancy, top)
            chunks = self._split_message(text)
            for i, chunk in enumerate(chunks, start=1):
                self._send_chunk(chunk)
                logger.info(
                    "Telegram: отправлена часть %d/%d по вакансии '%s'",
                    i, len(chunks), vacancy.title,
                )
        except Exception as e:
            # Согласно разделу 8: сбой уведомления не должен ронять весь процесс.
            logger.error(
                "Telegram: не удалось отправить дайджест по вакансии '%s': %s",
                vacancy.title, e,
            )

    # --- Формирование сообщения ---

    def _build_digest(self, vacancy: Vacancy, top: list[Evaluation]) -> str:
        v_title = html.escape(vacancy.title or "Без названия")
        header = (
            f"📋 <b>{v_title}</b>\n"
            f'🔗 <a href="{html.escape(vacancy.url)}">вакансия</a>\n'
        )

        if not top:
            return header + "\n❌ Подходящих кандидатов не найдено."

        header += f"👥 Топ подходящих: <b>{len(top)}</b>\n"

        blocks = [header]
        for idx, ev in enumerate(top, start=1):
            blocks.append(self._build_candidate_block(idx, ev))

        return "\n".join(blocks)

    def _build_candidate_block(self, idx: int, ev: Evaluation) -> str:
        score_emoji = self._score_emoji(ev.score)
        verdict = html.escape(ev.verdict.strip()) if ev.verdict else "—"
        red_flags = html.escape(ev.red_flags.strip()) if ev.red_flags else ""

        lines = [
            f"\n<b>#{idx}</b> — {score_emoji} <b>{ev.score}/100</b>",
            f"✅ {verdict}",
        ]
        if red_flags:
            lines.append(f"⚠️ {red_flags}")
        lines.append(f'🔗 <a href="{html.escape(ev.resume.url)}">резюме</a>')

        return "\n".join(lines)

    @staticmethod
    def _score_emoji(score: int) -> str:
        if score >= 80:
            return "🟢"
        if score >= 60:
            return "🟡"
        return "🔴"

    # --- Разбивка на части ---

    def _split_message(self, text: str) -> list[str]:
        """Режем по строкам, не превышая лимит. Не рвём отдельные строки."""
        if len(text) <= SAFE_LIMIT:
            return [text]

        chunks: list[str] = []
        current = ""
        for line in text.split("\n"):
            # Одна строка длиннее лимита — режем жёстко (крайне маловероятно).
            if len(line) > SAFE_LIMIT:
                if current:
                    chunks.append(current)
                    current = ""
                for i in range(0, len(line), SAFE_LIMIT):
                    chunks.append(line[i:i + SAFE_LIMIT])
                continue

            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > SAFE_LIMIT:
                chunks.append(current)
                current = line
            else:
                current = candidate

        if current:
            chunks.append(current)
        return chunks

    # --- Низкоуровневая отправка с retry ---

    def _send_chunk(self, text: str) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    self.api_url, json=payload, timeout=self.timeout_sec
                )
                if resp.status_code == 200:
                    return
                # 429 / 5xx — есть смысл повторить.
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                logger.warning(
                    "Telegram: попытка %d/%d неуспешна (%s)",
                    attempt, self.max_retries, last_error,
                )
            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(
                    "Telegram: попытка %d/%d — сетевая ошибка: %s",
                    attempt, self.max_retries, e,
                )

            if attempt < self.max_retries:
                time.sleep(self.backoff_sec * attempt)

        raise RuntimeError(f"Не удалось отправить сообщение в Telegram: {last_error}")