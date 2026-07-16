# analyzers/base.py
from abc import ABC, abstractmethod

from models import Vacancy, Resume, Evaluation


class BaseAnalyzer(ABC):
    """Слой оценки. Знает, КАК оценивать релевантность резюме вакансии."""

    @abstractmethod
    def rank(
        self,
        vacancy: Vacancy,
        resumes: list[Resume],
        top_n: int,
    ) -> list[Evaluation]:
        """Вернуть отсортированный по score топ (не длиннее top_n)."""
        ...