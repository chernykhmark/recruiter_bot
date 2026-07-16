# collectors/base.py
from abc import ABC, abstractmethod

from models import Vacancy, Resume


class BaseCollector(ABC):
    """Слой сбора данных. Знает, ОТКУДА берутся вакансии и отклики."""

    @abstractmethod
    def get_vacancies(self) -> list[Vacancy]:
        """Вернуть список активных вакансий."""
        ...

    @abstractmethod
    def get_resumes(self, vacancy: Vacancy) -> list[Resume]:
        """Вернуть отклики (резюме) на конкретную вакансию."""
        ...

    def close(self) -> None:
        """Освободить ресурсы (драйвер/сессию). По умолчанию — ничего."""
        pass