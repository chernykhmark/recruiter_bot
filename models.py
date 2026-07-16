# models.py
from dataclasses import dataclass, field


@dataclass
class Vacancy:
    id: str
    title: str
    description: str
    url: str


@dataclass
class Resume:
    id: str
    url: str
    text: str
    raw: dict = field(default_factory=dict)


@dataclass
class Evaluation:
    resume: Resume
    score: int          # 0–100
    verdict: str        # почему подходит (1–2 предложения)
    red_flags: str      # минусы / риски (может быть пусто)