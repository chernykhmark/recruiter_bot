# main.py
import logging

from config import config
from storage import Storage
from collectors.hh_selenium import HHSeleniumCollector
from analyzers.openai_analyzer import OpenRouterAnalyzer
from notifiers.telegram import TelegramNotifier
from shadowsocks_client import ShadowsocksClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def run() -> None:
    collector = None
    storage = None
    shadowsocks = ShadowsocksClient()

    try:
        shadowsocks.start()
        # Конструктор collector запускает Chrome, поэтому он тоже должен быть
        # внутри try: иначе ошибка старта обходила обработку и finally.
        collector = HHSeleniumCollector()
        analyzer = OpenRouterAnalyzer()
        notifier = TelegramNotifier()
        storage = Storage()

        vacancies = collector.get_vacancies()
        logger.info("Собрано вакансий: %d", len(vacancies))

        TEST_VACANCY_ID = "135442372"  # временный тест на одной вакансии
        vacancies = [v for v in vacancies if v.id == TEST_VACANCY_ID]
        if not vacancies:
            logger.error("Тестовая вакансия %s не найдена среди активных.", TEST_VACANCY_ID)
            return

        for vacancy in vacancies:
            # main.py (фрагмент run(): тело for-цикла по вакансиям — заменить целиком)

            try:
                resumes = collector.get_resumes(vacancy)
                logger.info(
                    "Вакансия '%s': собрано резюме — %d",
                    vacancy.title, len(resumes),
                )

                new_resumes = [
                    r for r in resumes
                    if not storage.is_processed(vacancy.id, r.id)
                ]
                if not new_resumes:
                    logger.info(
                        "Вакансия '%s': новых резюме нет, пропуск.",
                        vacancy.title,
                    )
                    continue

                logger.info(
                    "Вакансия '%s': к оценке — %d новых резюме",
                    vacancy.title, len(new_resumes),
                )

                top = analyzer.rank(vacancy, new_resumes, top_n=config.top_n)

                # ВАЖНО: помечаем обработанными СРАЗУ после оценки (деньги
                # потрачены здесь). Так при падении/убийстве процесса на
                # этапе отправки мы не переоцениваем те же резюме заново.
                for r in new_resumes:
                    storage.mark_processed(vacancy.id, r.id)
                for ev in top:
                    storage.save_evaluation(vacancy.id, ev)

                # Отправка после фиксации прогресса. send() сам глотает ошибки
                # (раздел 8) и не роняет цикл.
                notifier.send(vacancy, top)

            except Exception as e:
                logger.error(
                    "Ошибка при обработке вакансии '%s': %s",
                    getattr(vacancy, "title", "?"), e,
                )
                continue

    except Exception:
        logger.exception("Критическая ошибка запуска или выполнения бота.")
    finally:
        if collector is not None:
            collector.close()
        if storage is not None:
            storage.close()
        shadowsocks.stop()
        logger.info("Работа завершена.")


if __name__ == "__main__":
    run()
