# notifiers/base.py
from abc import ABC, abstractmethod

from models import Vacancy, Evaluation


class BaseNotifier(ABC):
    """Слой уведомлений. Знает, КУДА отправлять результат."""

    @abstractmethod
    def send(self, vacancy: Vacancy, top: list[Evaluation]) -> None:
        """Отправить результат оценки по вакансии."""
        ...