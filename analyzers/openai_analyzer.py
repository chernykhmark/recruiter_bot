# analyzers/openai_analyzer.py
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from openai import OpenAI

from analyzers.base import BaseAnalyzer
from config import config
from models import Vacancy, Resume, Evaluation

logger = logging.getLogger(__name__)


# =====================================================================
# ПРОМПТЫ — главный рычаг качества. Правь здесь.
# =====================================================================

# --- Этап 1: быстрый отсев мусора (батч) ---
FILTER_SYSTEM_PROMPT = """Ты — рекрутер-скринер. Твоя задача — быстро отсеять \
резюме, которые ЯВНО не подходят под вакансию: другая профессия, полное \
отсутствие ключевого опыта, случайный/пустой отклик.

Оценивай по СУТИ, а не по совпадению слов. Если есть хоть малейшие сомнения — \
помечай кандидата как relevant (пусть его оценит следующий этап). Отсеивай \
только очевидный мусор.

Верни строго JSON-объект вида:
{"results": [{"id": "<id резюме>", "verdict": "relevant" | "trash"}]}
Никакого текста вне JSON."""

FILTER_USER_TEMPLATE = """ВАКАНСИЯ
Заголовок: {title}
Описание:
{description}

РЕЗЮМЕ (несколько, оцени каждое):
{resumes_block}
"""

# --- Этап 2: детальная оценка (по одному) ---
EVAL_SYSTEM_PROMPT = """Ты — опытный рекрутер. Оцени, насколько кандидат \
подходит под вакансию, по СУТИ соответствия, а не по совпадению ключевых слов.

Важно:
- "Красивое" резюме с модными словами не всегда лучше. Скромное резюме с \
релевантным реальным опытом может подходить сильнее.
- Смотри на реальный опыт, зоны ответственности, достижения, соответствие \
уровня задач вакансии.
- Будь честен в минусах: отмечай реальные риски (нехватка опыта, смена сферы, \
пробелы, несоответствие уровню).

Шкала score (0–100):
- 80–100: сильное соответствие, звонить в первую очередь
- 60–79: хорошее соответствие, стоит рассмотреть
- 40–59: спорно, есть заметные пробелы
- 0–39: слабо подходит

Верни строго JSON-объект:
{"score": <int 0-100>, "verdict": "<1-2 предложения: почему подходит>", \
"red_flags": "<минусы/риски; пустая строка если нет>"}
Никакого текста вне JSON."""

EVAL_USER_TEMPLATE = """ВАКАНСИЯ
Заголовок: {title}
Описание:
{description}

РЕЗЮМЕ КАНДИДАТА:
{resume_text}
"""


class OpenRouterAnalyzer(BaseAnalyzer):
    """Двухэтапная оценка резюме через DeepSeek в OpenRouter."""

    def __init__(self) -> None:
        client_kwargs = {
            "api_key": config.openrouter_api_key,
            "base_url": "https://openrouter.ai/api/v1",
            "default_headers": {"X-OpenRouter-Title": "recruiter_bot"},
            "timeout": config.llm_request_timeout_sec,
            "max_retries": 0,
        }
        if not config.openrouter_api_key:
            raise RuntimeError(
                "Не задан OPENROUTER_API_KEY. Создайте ключ в OpenRouter и добавьте "
                "OPENROUTER_API_KEY=sk-or-v1-... в .env."
            )
        if config.llm_proxy_url:
            client_kwargs["http_client"] = httpx.Client(
                proxy=config.llm_proxy_url,
                trust_env=False,
                timeout=httpx.Timeout(
                    config.llm_request_timeout_sec,
                    connect=min(15.0, config.llm_request_timeout_sec),
                ),
            )
        self.client = OpenAI(**client_kwargs)
        self.model = config.llm_model

    # ------------------------------------------------------------------
    # Публичный метод
    # ------------------------------------------------------------------
    def rank(
        self,
        vacancy: Vacancy,
        resumes: list[Resume],
        top_n: int,
    ) -> list[Evaluation]:
        if not resumes:
            return []

        # Этап 1 — фильтр (опционально).
        if config.analyzer_filter_enabled:
            survivors = self._filter_stage(vacancy, resumes)
            logger.info(
                "Фильтр: %d/%d резюме прошли на детальную оценку",
                len(survivors), len(resumes),
            )
        else:
            logger.info("Фильтр отключён — все %d резюме идут на этап 2", len(resumes))
            survivors = resumes

        if not survivors:
            return []

        # Этап 2 — детальная оценка (параллельно).
        evaluations = self._eval_stage(vacancy, survivors)

        evaluations.sort(key=lambda e: e.score, reverse=True)
        return evaluations[:top_n]

    # ------------------------------------------------------------------
    # Этап 1: фильтр мусора батчами
    # ------------------------------------------------------------------
    def _filter_stage(self, vacancy: Vacancy, resumes: list[Resume]) -> list[Resume]:
        by_id = {r.id: r for r in resumes}
        survivors: list[Resume] = []
        batch_size = max(1, config.filter_batch_size)

        for i in range(0, len(resumes), batch_size):
            batch = resumes[i:i + batch_size]
            try:
                verdicts = self._filter_batch(vacancy, batch)
            except Exception as exc:
                # Сбой батча не должен ронять процесс: пропускаем всех дальше
                # (лучше лишний раз оценить, чем потерять кандидата).
                logger.error(
                    "Сбой фильтра батча (резюме %s): %s — пропускаю всех дальше",
                    [r.id for r in batch], exc,
                )
                survivors.extend(batch)
                continue

            for rid, verdict in verdicts.items():
                resume = by_id.get(rid)
                if resume is None:
                    continue
                if verdict == "trash":
                    logger.debug("Отсеян мусор: резюме %s", rid)
                else:
                    survivors.append(resume)

            # Кандидатов, о которых модель не упомянула — на всякий случай пропускаем.
            answered = set(verdicts.keys())
            for r in batch:
                if r.id not in answered:
                    logger.warning("Фильтр не вернул вердикт по %s — пропускаю дальше", r.id)
                    survivors.append(r)

        return survivors

    def _filter_batch(self, vacancy: Vacancy, batch: list[Resume]) -> dict[str, str]:
        limit = config.filter_resume_chars
        blocks = []
        for r in batch:
            text = (r.text or "")[:limit]
            blocks.append(f"[id: {r.id}]\n{text}")
        resumes_block = "\n\n---\n\n".join(blocks)

        user = FILTER_USER_TEMPLATE.format(
            title=vacancy.title,
            description=vacancy.description,
            resumes_block=resumes_block,
        )
        content = self._chat_json(FILTER_SYSTEM_PROMPT, user)
        data = json.loads(content)

        result: dict[str, str] = {}
        for item in data.get("results", []):
            rid = str(item.get("id", "")).strip()
            verdict = str(item.get("verdict", "")).strip().lower()
            if rid:
                result[rid] = "trash" if verdict == "trash" else "relevant"
        return result

    # ------------------------------------------------------------------
    # Этап 2: детальная оценка (параллельно)
    # ------------------------------------------------------------------
    def _eval_stage(self, vacancy: Vacancy, resumes: list[Resume]) -> list[Evaluation]:
        evaluations: list[Evaluation] = []
        workers = max(1, config.analyzer_workers)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._eval_one, vacancy, r): r
                for r in resumes
            }
            for fut in as_completed(futures):
                resume = futures[fut]
                try:
                    evaluation = fut.result()
                    if evaluation is not None:
                        evaluations.append(evaluation)
                except Exception as exc:
                    logger.error("Сбой оценки резюме %s: %s — пропускаю", resume.id, exc)

        return evaluations

    def _eval_one(self, vacancy: Vacancy, resume: Resume) -> Evaluation | None:
        user = EVAL_USER_TEMPLATE.format(
            title=vacancy.title,
            description=vacancy.description,
            resume_text=resume.text or "",
        )
        content = self._chat_json(EVAL_SYSTEM_PROMPT, user)
        data = json.loads(content)

        try:
            score = int(data.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(100, score))

        return Evaluation(
            resume=resume,
            score=score,
            verdict=str(data.get("verdict", "")).strip(),
            red_flags=str(data.get("red_flags", "")).strip(),
        )

    # ------------------------------------------------------------------
    # Низкоуровневый вызов OpenRouter с retry
    # ------------------------------------------------------------------
    def _chat_json(self, system_prompt: str, user_prompt: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(1, config.llm_max_retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    max_tokens=config.llm_max_tokens,
                    response_format={"type": "json_object"},
                    # Для классификации и короткого JSON reasoning не нужен:
                    # он увеличивает задержку и расход токенов на длинных резюме.
                    extra_body={"reasoning": {"enabled": False}},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                return resp.choices[0].message.content
            except Exception as exc:
                last_exc = exc
                wait = config.llm_retry_backoff_sec * attempt
                logger.warning(
                    "OpenRouter сбой (попытка %d/%d): %s — жду %.1fs",
                    attempt, config.llm_max_retries, exc, wait,
                )
                time.sleep(wait)
        raise RuntimeError(
            f"OpenRouter не ответил после {config.llm_max_retries} попыток: {last_exc}"
        )
