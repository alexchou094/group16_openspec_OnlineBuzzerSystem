from dataclasses import dataclass, field
from enum import StrEnum


class Phase(StrEnum):
    PREPARING = "PREPARING"
    OPEN = "OPEN"
    RESULTS = "RESULTS"
    CANCELLED = "CANCELLED"
    FINISHED = "FINISHED"


TERMINAL_PHASES = {Phase.CANCELLED, Phase.FINISHED}


class ReviewStatus(StrEnum):
    UNREVIEWED = "UNREVIEWED"
    INCORRECT = "INCORRECT"
    CORRECT = "CORRECT"


@dataclass(frozen=True)
class Blank:
    label: str
    target: str


@dataclass(frozen=True)
class Question:
    id: str
    number: int
    blanks: tuple[Blank, ...]


@dataclass(frozen=True)
class SourceSnapshot:
    spreadsheet_id: str
    sheet_id: int
    title: str
    fingerprint: str
    headers: tuple[tuple[str, str], ...] = ()
    merge_ranges: tuple[str, ...] = ()


@dataclass(frozen=True)
class Group:
    id: str
    number: int
    credential: str


@dataclass(frozen=True)
class Submission:
    id: str
    question_id: str
    group_id: str
    answers: tuple[str, ...]
    received_at: float
    receive_sequence: int
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED

    @property
    def rejected(self) -> bool:
        return self.review_status == ReviewStatus.INCORRECT


@dataclass(frozen=True)
class CandidateResult:
    submission_id: str
    rank: int | None
    successes_before: int | None


@dataclass(frozen=True)
class QuestionResult:
    question_id: str
    winner_group_id: str | None = None
    candidates: tuple[CandidateResult, ...] = ()
    reason: str = ""
    status: str = "PENDING"
    current_submission_id: str | None = None


@dataclass
class Round:
    id: str
    number: int
    question_ids: tuple[str, ...]
    fairness_enabled: bool
    baseline: dict[str, int]
    opened_at: float
    deadline: float | None
    submissions: dict[tuple[str, str], Submission] = field(default_factory=dict)
    next_receive_sequence: int = 1
    fixed_question_order: tuple[str, ...] = ()
    results: tuple[QuestionResult, ...] = ()
    review_version: int = 0
    finalized: bool = False
    cumulative_counts: tuple[tuple[str, int], ...] = ()
    affected_questions: tuple[str, ...] = ()

    def record_accepted(self, submission_id: str, question_id: str, group_id: str,
                        answers: tuple[str, ...], received_at: float) -> Submission:
        """保存已驗證的提交。呼叫端須持有活動鎖。"""
        key = (question_id, group_id)
        if key not in self.submissions:
            self.submissions[key] = Submission(submission_id, question_id, group_id, answers,
                                               received_at, self.next_receive_sequence)
            self.next_receive_sequence += 1
        return self.submissions[key]


@dataclass
class Activity:
    id: str
    code: str
    owner: str
    group_limit: int
    phase: Phase = Phase.PREPARING
    source: SourceSnapshot | None = None
    questions: tuple[Question, ...] = ()
    groups: dict[int, Group] = field(default_factory=dict)
    memberships: dict[str, int] = field(default_factory=dict)
    used_credentials: dict[int, set[str]] = field(default_factory=dict)
    rounds: list[Round] = field(default_factory=list)
    finalized_revision: int = 0

    @property
    def current_round(self) -> Round | None:
        return self.rounds[-1] if self.rounds else None

    @property
    def successful_questions(self) -> set[str]:
        return {
            result.question_id
            for round_ in self.rounds if round_.finalized
            for result in round_.results if result.winner_group_id is not None
        }

    @property
    def submitted_groups(self) -> set[str]:
        return {submission.group_id for round_ in self.rounds for submission in round_.submissions.values()}
