# collectors/hh_selenium.py
import logging
import os
import re
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By

from collectors.base import BaseCollector
from config import config
from models import Vacancy, Resume

log = logging.getLogger(__name__)


# Порядок и заголовки секций резюме. Порядок фиксирован для стабильности
# восприятия текста LLM. main_info идёт первым и без заголовка.
RESUME_SECTIONS = [
    ("resume-block-contacts", "КОНТАКТЫ"),
    ("resume-specializations", "СПЕЦИАЛИЗАЦИИ"),
    ("resume-experience-block", "ОПЫТ РАБОТЫ"),
    ("skills-table", "КЛЮЧЕВЫЕ НАВЫКИ"),
    ("resume-languages-block", "ЯЗЫКИ"),
    ("resume-education-block", "ОБРАЗОВАНИЕ"),
    ("resume-additional-info-block", "ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ"),
    ("resume-comments", "КОММЕНТАРИИ"),
]

# Все data-qa блоки, которые парсим со страницы резюме.
RESUME_QA_BLOCKS = [qa for qa, _ in RESUME_SECTIONS]


class HHSeleniumCollector(BaseCollector):
    """Сбор вакансий и откликов с hh.ru через undetected_chromedriver."""

    def __init__(self):
        self.driver = self._build_driver()
        # Кэш: id вакансии -> url страницы откликов (заполняется в get_vacancies).
        self._responses_urls: dict[str, str] = {}
        self._delay = config.parser_delay_sec

    # ------------------------------------------------------------------ #
    # Инфраструктура                                                     #
    # ------------------------------------------------------------------ #
        # collectors/hh_selenium.py

    @staticmethod
    def _clear_stale_profile_locks(session_path: Path) -> None:
        """Удалить блокировки профиля, оставшиеся после аварии Chrome.

        Если процесс из SingletonLock ещё жив, профиль не трогаем: два Chrome
        не должны одновременно писать в одну chrome_session.
        """
        lock_path = session_path / "SingletonLock"
        if not lock_path.is_symlink():
            return

        try:
            lock_target = os.readlink(lock_path)
            host, pid_text = lock_target.rsplit("-", 1)
            pid = int(pid_text)
        except (OSError, ValueError):
            log.warning("Не удалось проверить блокировку Chrome: %s", lock_path)
            return

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
                path = session_path / name
                if path.is_symlink():
                    path.unlink()
            log.info("Удалена устаревшая блокировка chrome_session (PID %d).", pid)
        except PermissionError as exc:
            raise RuntimeError(
                f"Не удалось проверить процесс Chrome PID {pid}."
            ) from exc
        else:
            lock_host = host.removesuffix(".local").casefold()
            current_host = socket.gethostname().removesuffix(".local").casefold()
            if lock_host != current_host:
                raise RuntimeError(
                    f"Профиль Chrome заблокирован другим компьютером: {lock_target}"
                )
            raise RuntimeError(
                "chrome_session уже используется другим Chrome "
                f"(PID {pid}). Закройте его перед запуском бота."
            )

        # collectors/hh_selenium.py

    def _build_driver(self):
        session_path = Path(config.chrome_session_path).expanduser()
        if not session_path.is_absolute():
            session_path = Path(__file__).resolve().parent.parent / session_path
        session_path.mkdir(parents=True, exist_ok=True)
        session_path = session_path.resolve()
        self._clear_stale_profile_locks(session_path)

        fresh_profile = not (session_path / "Default" / "Preferences").exists()
        options = uc.ChromeOptions()
        driver = uc.Chrome(
            options=options,
            user_data_dir=str(session_path),
            version_main=config.chrome_version_main,
        )

        driver.get(config.vacancies_url)
        # После удаления chrome_session необходимо дать пользователю войти в
        # аккаунт. Для уже созданного профиля пауза включается только через env.
        if fresh_profile or config.chrome_manual_login:
            if not sys.stdin.isatty():
                log.warning(
                    "Создана новая chrome_session, но запуск неинтерактивный. "
                    "Войдите в hh.ru в открывшемся Chrome и запустите бот повторно."
                )
                return driver
            input(
                "\n>>> Залогинься в открытом окне Chrome (введи код подтверждения),\n"
                ">>> затем вернись сюда и нажми Enter для продолжения...\n"
            )
        return driver

    def close(self):
        """Закрыть драйвер. Вызывается оркестратором в finally."""
        try:
            self.driver.quit()
            log.info("Драйвер закрыт.")
        except Exception:
            log.exception("Ошибка при закрытии драйвера.")

    # collectors/hh_selenium.py
    def _get_soup(self, url: str, wait: float = 2.0) -> BeautifulSoup:
        """Открыть страницу и вернуть BeautifulSoup.
        wait — базовая задержка под загрузку страницы; self._delay — антиботовая
        добавка из конфига. Для вакансий и резюме достаточно 2–3 секунд.
        """
        self.driver.get(url)
        time.sleep(wait + self._delay)
        return BeautifulSoup(self.driver.page_source, "html.parser")

    # ------------------------------------------------------------------ #
    # BaseCollector: get_vacancies                                       #
    # ------------------------------------------------------------------ #
    def get_vacancies(self) -> list[Vacancy]:
        try:
            links = self._collect_vacancy_links(config.vacancies_url)
        except Exception:
            log.exception("Не удалось собрать список вакансий.")
            return []

        vacancies: list[Vacancy] = []
        for i, (title_hint, url) in enumerate(links.items(), 1):
            try:
                log.info("Парсинг вакансии %d/%d: %s", i, len(links), url)
                vacancy = self._parse_vacancy(url, title_hint=title_hint)
                if vacancy:
                    vacancies.append(vacancy)
            except Exception:
                log.exception("Ошибка парсинга вакансии %s — пропуск.", url)
                continue

        log.info("Собрано вакансий: %d", len(vacancies))
        return vacancies

    def _collect_vacancy_links(self, start_url: str) -> dict[str, str]:
        log.info("Сбор ссылок на вакансии...")
        soup = self._get_soup(start_url, wait=5.0)

        links: dict[str, str] = {}
        base_url = "https://hh.ru/"

        for link in soup.find_all("a", href=True):
            href = link["href"]
            if (
                "/vacancy/" in href
                and "?hhtmFrom=employer_vacancies" in href
                and "create" not in href
            ):
                full_url = urljoin(base_url, href)
                text = link.get_text().strip() or f"vacancy_{len(links)}"
                links[text] = full_url

        log.info("Найдено ссылок на вакансии: %d", len(links))
        return links

    def _parse_vacancy(self, url: str, title_hint: str = "") -> Vacancy | None:
        soup = self._get_soup(url)

        # Классы hh.ru динамические, поэтому сначала используем стабильный
        # data-qa. h1 и название из списка вакансий служат резервом.
        title_el = (
            soup.select_one('[data-qa="vacancy-title"]')
            or soup.find("h1")
        )
        title = self._clean(title_el.get_text(separator="\n")) if title_el else ""
        if not title:
            og_title = soup.select_one('meta[property="og:title"]')
            if og_title:
                title = self._clean(og_title.get("content", ""))
        if not title:
            title = self._clean(title_hint)

        desc_el = (
            soup.select_one('[data-qa="vacancy-description"]')
            or soup.find("div", {"class": "vacancy-description"})
        )
        description = self._clean(desc_el.get_text(separator="\n")) if desc_el else ""

        vacancy_id = url.split("/")[-1].split("?")[0]

        # Ссылка на отклики -> в кэш.
        responses_link = soup.find("a", href=lambda x: x and "vacancyresponses" in x)
        if responses_link:
            self._responses_urls[vacancy_id] = urljoin(url, responses_link["href"])
        else:
            log.warning("У вакансии %s нет ссылки на отклики.", vacancy_id)

        if not title and not description:
            log.warning("Вакансия %s: пустые заголовок и описание — пропуск.", vacancy_id)
            return None

        return Vacancy(
            id=vacancy_id,
            title=title or f"vacancy_{vacancy_id}",
            description=description,
            url=url,
        )

    # ------------------------------------------------------------------ #
    # BaseCollector: get_resumes                                         #
    # ------------------------------------------------------------------ #
        # collectors/hh_selenium.py
    def get_resumes(self, vacancy: Vacancy) -> list[Resume]:
        responses_url = self._responses_urls.get(vacancy.id)
        if not responses_url:
            log.warning("Нет URL откликов для вакансии %s — резюме нет.", vacancy.id)
            return []

        try:
            resume_urls = self._collect_resume_links(responses_url)
        except Exception:
            log.exception("Ошибка сбора ссылок на резюме (вакансия %s).", vacancy.id)
            return []

        # Лимит убран: парсим все отклики со всех страниц пагинации.
        log.info("Вакансия %s: резюме к парсингу %d.", vacancy.id, len(resume_urls))

        resumes: list[Resume] = []
        for j, r_url in enumerate(resume_urls, 1):
            try:
                log.info("  Резюме %d/%d", j, len(resume_urls))
                resume = self._parse_resume(r_url)
                if resume:
                    resumes.append(resume)
            except Exception:
                log.exception("Ошибка парсинга резюме %s — пропуск.", r_url)
                continue

        log.info("Вакансия %s: собрано резюме %d.", vacancy.id, len(resumes))
        return resumes

        # collectors/hh_selenium.py
    def _collect_resume_links(self, url: str) -> list[str]:
        """Пройти ВСЕ страницы откликов до конца пагинации.

        Не полагаемся на подсчёт страниц по динамическому CSS-классу (хрупко,
        раздел 11). Идём по page=0,1,2,... пока страница даёт НОВЫЕ ссылки на
        резюме. Как только новых ссылок нет — останавливаемся.
        """
        resume_links: list[str] = []
        seen: set[str] = set()

        page = 0
        max_pages = config.max_response_pages  # защита от бесконечного цикла

        while page < max_pages:
            try:
                if page == 0:
                    page_url = url
                    soup = self._get_soup(page_url)
                else:
                    # hh использует параметр page для пагинации откликов.
                    page_url = url.replace("hhtmFrom=vacancy", f"page={page}")
                    log.info("Страница откликов #%d", page)
                    soup = self._get_soup(page_url, wait=4.0)

                responses_div = soup.find("div", {"data-qa": "vacancy-real-responses"})
                if not responses_div:
                    log.info("Страница #%d: блок откликов не найден — конец.", page)
                    break

                links = responses_div.find_all(
                    "a",
                    href=lambda x: x and "/resume/" in x and "suitable_resume" not in x,
                )

                new_on_page = 0
                for link in links:
                    full = urljoin("https://hh.ru", link["href"])
                    if full not in seen:
                        seen.add(full)
                        resume_links.append(full)
                        new_on_page += 1

                log.info(
                    "Страница откликов #%d: найдено ссылок %d, новых %d",
                    page, len(links), new_on_page,
                )

                # Нет новых резюме на странице — пагинация закончилась.
                if new_on_page == 0:
                    log.info("Страница #%d новых резюме не дала — конец пагинации.", page)
                    break

            except Exception:
                log.exception("Ошибка на странице откликов #%d — стоп пагинации.", page)
                break

            page += 1

        log.info("Всего собрано ссылок на резюме: %d", len(resume_links))
        return resume_links

    def _parse_resume(self, url: str) -> Resume | None:
        soup = self._get_soup(url)

        raw: dict = {}

        main_el = soup.find("div", {"data-qa": "resume-main-info__content-wrapper"})
        raw["main_info"] = self._clean(main_el.get_text(separator="\n")) if main_el else ""

        for qa in RESUME_QA_BLOCKS:
            block = soup.find("div", {"data-qa": qa})
            raw[qa] = self._clean(block.get_text(separator="\n")) if block else ""

        resume_id = url.split("resumeId=")[-1].split("&")[0]
        text = self._build_resume_text(raw)

        if not text.strip():
            log.warning("Резюме %s: пустой текст после сборки — пропуск.", resume_id)
            return None

        return Resume(id=resume_id, url=url, text=text, raw=raw)

    # ------------------------------------------------------------------ #
    # Сборка текста резюме для LLM                                        #
    # ------------------------------------------------------------------ #
    def _build_resume_text(self, raw: dict) -> str:
        """Структурированный текст с заголовками секций. Пустые блоки пропускаем."""
        parts: list[str] = []

        main_info = raw.get("main_info", "").strip()
        if main_info:
            parts.append(main_info)

        for qa, header in RESUME_SECTIONS:
            content = raw.get(qa, "").strip()
            if content:
                parts.append(f"=== {header} ===\n{content}")

        return "\n\n".join(parts)

    @staticmethod
    def _clean(text: str) -> str:
        """Нормализация: обрезка строк, удаление пустых строк и лишних переносов."""
        if not text:
            return ""
        lines = [ln.strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if ln]
        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    # ------------------------------------------------------------------ #
    # На будущее (в интерфейс не входит)                                 #
    # ------------------------------------------------------------------ #
    def _add_to_wishlist(self, url: str) -> None:
        """Добавить резюме в список 'подумать'. Не используется оркестратором."""
        try:
            soup = self._get_soup(url)
            wish_tag = soup.find("a", href=lambda h: h and "state=consider" in h)
            if not wish_tag:
                log.warning("Не найдена ссылка 'подумать' для %s.", url)
                return
            self.driver.get(urljoin("https://hh.ru", wish_tag.get("href")))
            time.sleep(3)
            btn = self.driver.find_element(
                By.CSS_SELECTOR, '[data-qa="negotiations-change-topic__submit"]'
            )
            btn.click()
            log.info("Резюме добавлено в список 'подумать'.")
            time.sleep(5)
        except Exception:
            log.exception("Не удалось добавить резюме в список 'подумать': %s", url)
