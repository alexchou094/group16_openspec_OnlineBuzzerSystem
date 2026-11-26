from dataclasses import dataclass
from enum import StrEnum

from app.domain.activity import Question, SourceSnapshot


@dataclass(frozen=True)
class AnswerCell:
    question: int
    blank: str
    group: int
    target: str
    answer: str


@dataclass(frozen=True)
class ExportSnapshot:
    id: str
    revision: int
    source: SourceSnapshot | None
    questions: tuple[Question, ...]
    cells: tuple[AnswerCell, ...]


class WriteStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


@dataclass
class WriteAttempt:
    id: str
    snapshot: ExportSnapshot
    status: WriteStatus = WriteStatus.RUNNING
    message: str = ""
