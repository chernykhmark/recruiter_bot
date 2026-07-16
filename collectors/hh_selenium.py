# collectors/hh_selenium.py
import logging
import re
import time
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

    def _build_driver(self):
        options = uc.ChromeOptions()
        options.add_argument(f"--user-data-dir={config.chrome_session_path}")
        driver = uc.Chrome(options=options, version_main=config.chrome_version_main)

        # Открываем сайт и даём время залогиниться вручную (ввести код подтверждения)
        driver.get(config.vacancies_url)
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

    def _get_soup(self, url: str, wait: float = 3.0) -> BeautifulSoup:
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
                vacancy = self._parse_vacancy(url)
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

    def _parse_vacancy(self, url: str) -> Vacancy | None:
        soup = self._get_soup(url)

        title_el = soup.find("div", {"class": "vacancy-title"})
        title = self._clean(title_el.get_text(separator="\n")) if title_el else ""

        desc_el = soup.find("div", {"class": "vacancy-description"})
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

        resume_urls = resume_urls[: config.max_resumes_per_vacancy]
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

    def _collect_resume_links(self, url: str) -> list[str]:
        soup = self._get_soup(url)

        try:
            container = soup.find(
                "ul", class_="magritte-number-pages-container___YIJLn_4-0-103"
            )
            total_pages = len(container.find_all("li"))
        except Exception:
            total_pages = 1
        log.info("Страниц откликов: %d", total_pages)

        resume_links: list[str] = []

        for page in range(total_pages):
            try:
                if page > 0:
                    page_url = url.replace("hhtmFrom=vacancy", f"page={page}")
                    log.info("Страница откликов #%d", page)
                    soup = self._get_soup(page_url, wait=6.0)

                responses_div = soup.find("div", {"data-qa": "vacancy-real-responses"})
                if not responses_div:
                    continue

                for link in responses_div.find_all(
                    "a",
                    href=lambda x: x and "/resume/" in x and "suitable_resume" not in x,
                ):
                    resume_links.append(urljoin("https://hh.ru", link["href"]))
            except Exception:
                log.exception("Ошибка на странице откликов #%d — пропуск страницы.", page)
                continue

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