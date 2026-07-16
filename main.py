# main.py
import logging

from config import config
from storage import Storage
from collectors.hh_selenium import HHSeleniumCollector
from analyzers.openai_analyzer import OpenAIAnalyzer
from notifiers.telegram import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def run() -> None:
    # --- Сборка слоёв (единственное место, где выбираются реализации) ---
    collector = HHSeleniumCollector()
    analyzer = OpenAIAnalyzer()
    notifier = TelegramNotifier()
    storage = Storage()

    try:
        vacancies = collector.get_vacancies()
        logger.info("Собрано вакансий: %d", len(vacancies))

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

    finally:
        collector.close()
        storage.close()
        logger.info("Работа завершена.")


if __name__ == "__main__":
    run()