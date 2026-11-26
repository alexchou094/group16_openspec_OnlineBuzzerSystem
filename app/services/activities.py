import secrets
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Callable

from app.domain.activity import (
    TERMINAL_PHASES, Activity, Group, Phase, Question, QuestionResult, Round, SourceSnapshot,
)
from app.repositories.memory import MemoryRepository, new_id


class ActivityError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class ActivityService:
    def __init__(self, repository: MemoryRepository, clock: Callable[[], float] = time.monotonic):
        self.repository = repository
        self.clock = clock
        self.utc_offset = time.time() - clock()

    def timestamp(self, monotonic: float) -> str:
        return datetime.fromtimestamp(self.utc_offset + monotonic, UTC).isoformat()

    @contextmanager
    def locked(self, activity_id: str, session_id: str, role: str | None = None):
        entry = self.repository.get(activity_id)
        if entry is None:
            raise ActivityError(404, "活動已失效或不存在。")
        with entry.lock:
            activity = entry.activity
            actual = "teacher" if activity.owner == session_id else "student" if session_id in activity.memberships else None
            if actual is None or (role is not None and actual != role):
                raise ActivityError(403, "沒有此活動的操作權限，請確認身分或重新加入。")
            self._expire(activity)
            yield entry

    def create(self, owner: str, group_limit: int) -> Activity:
        if type(group_limit) is not int or group_limit < 1:
            raise ActivityError(422, "組數上限須為正整數。")
        activity = self.repository.create(owner, group_limit)
        if activity is None:
            raise ActivityError(409, "請先取消或結束目前的活動。")
        return activity

    def student_activities(self, session: str) -> list[dict[str, str]]:
        activities = []
        for entry in self.repository.active_entries():
            with entry.lock:
                activity = entry.activity
                if activity.phase not in TERMINAL_PHASES and session in activity.memberships:
                    activities.append({"id": activity.id, "code": activity.code})
        return activities

    def join(self, session: str, code: str, number: int, credential: str | None) -> str:
        activity_id = self.repository.activity_id_for_code(code.strip().upper())
        if activity_id is None:
            raise ActivityError(404, "活動代碼無效或已失效。")
        entry = self.repository.get(activity_id)
        with entry.lock:
            activity = entry.activity
            self._expire(activity)
            if activity.phase in TERMINAL_PHASES:
                raise ActivityError(409, "活動已結束或取消，無法加入。")
            if activity.owner == session:
                raise ActivityError(409, "請用獨立的瀏覽器環境加入學生組別。")
            if type(number) is not int or not 1 <= number <= activity.group_limit:
                raise ActivityError(422, f"組號須為 1 到 {activity.group_limit} 的整數。")
            current = activity.memberships.get(session)
            if current == number:
                return activity.id
            if current is not None:
                raise ActivityError(409, "請先離開目前的組別。")
            group = activity.groups.get(number)
            if group is None and not credential:
                used = activity.used_credentials.setdefault(number, set())
                if len(used) == 1_000_000:
                    raise ActivityError(409, "此組號的加入憑證已用完。")
                while True:
                    value = f"{secrets.randbelow(1_000_000):06d}"
                    if value not in used:
                        break
                used.add(value)
                group = Group(new_id(), number, value)
                activity.groups[number] = group
            elif (group is None or not credential or not credential.isascii()
                  or not secrets.compare_digest(group.credential, credential)):
                raise ActivityError(403, "加入憑證錯誤或已失效。")
            activity.memberships[session] = number
            return activity.id

    def leave(self, activity_id: str, session: str) -> None:
        with self.locked(activity_id, session, "student") as entry:
            activity = entry.activity
            if activity.phase not in (Phase.PREPARING, Phase.RESULTS):
                raise ActivityError(409, "只能在準備中或結果階段離組。")
            del activity.memberships[session]

    def delete_group(self, activity_id: str, session: str, number: int) -> None:
        with self.locked(activity_id, session, "teacher") as entry:
            activity = entry.activity
            if activity.phase in TERMINAL_PHASES:
                raise ActivityError(409, "活動已結束或取消。")
            group = activity.groups.get(number)
            if group is None:
                raise ActivityError(404, "組別不存在。")
            if group.id in activity.submitted_groups:
                raise ActivityError(409, "此組曾有效提交，無法刪除。")
            del activity.groups[number]
            entry.projections.pop(group.id, None)
            activity.memberships = {sid: n for sid, n in activity.memberships.items() if n != number}

    def replace_questions(self, activity_id: str, session: str, source: SourceSnapshot, questions: tuple[Question, ...]) -> None:
        """接收匯入模組已完整驗證的題目映射。"""
        with self.locked(activity_id, session, "teacher") as entry:
            activity = entry.activity
            if activity.phase != Phase.PREPARING:
                raise ActivityError(409, "只能在準備中匯入題目。")
            if not questions or len({question.id for question in questions}) != len(questions):
                raise ActivityError(422, "請提供至少一道題目，且題目識別不得重複。")
            activity.source, activity.questions = source, questions

    def open_round(self, activity_id: str, session: str, question_ids: list[str] | None,
                   fairness_enabled: bool, seconds: int | None) -> str:
        with self.locked(activity_id, session, "teacher") as entry:
            activity = entry.activity
            current = activity.current_round
            if activity.phase != Phase.PREPARING and not (activity.phase == Phase.RESULTS and current.finalized):
                raise ActivityError(409, "請先關輪並定案。")
            if activity.source is None:
                raise ActivityError(409, "請先匯入題目。")
            eligible = self._eligible(activity)
            chosen = eligible if question_ids is None else tuple(question_ids)
            if not chosen or len(set(chosen)) != len(chosen) or not set(chosen) <= set(eligible):
                raise ActivityError(422, "請選擇至少一道尚未成功且不重複的題目。")
            if seconds is not None and (type(seconds) is not int or not 1 <= seconds <= 3600):
                raise ActivityError(422, "倒數須為 1 到 3600 的整數秒。")
            counts = self._success_counts(activity)
            baseline = {group.id: counts[group.id] for group in activity.groups.values()}
            now = self.clock()
            round_ = Round(new_id(), len(activity.rounds) + 1, chosen, fairness_enabled,
                           baseline, now, None if seconds is None else now + seconds)
            activity.rounds.append(round_)
            activity.phase = Phase.OPEN
            return round_.id

    def close_round(self, activity_id: str, session: str, round_id: str) -> int:
        with self.locked(activity_id, session, "teacher") as entry:
            activity = entry.activity
            round_ = self._match_round(activity, round_id)
            if activity.phase == Phase.OPEN:
                self._close(activity, round_)
            return round_.review_version

    def finalize(self, activity_id: str, session: str, round_id: str, expected_review_version: int) -> None:
        with self.locked(activity_id, session, "teacher") as entry:
            activity = entry.activity
            round_ = self._match_round(activity, round_id)
            if round_.review_version != expected_review_version:
                raise ActivityError(409, "結果版本已更新，請重新檢視後再定案。")
            if round_.finalized:
                return
            if activity.phase != Phase.RESULTS:
                raise ActivityError(409, "請先關輪並檢視結果。")
            round_.finalized = True
            activity.finalized_revision += 1

    def cancel(self, activity_id: str, session: str) -> None:
        with self.locked(activity_id, session, "teacher") as entry:
            if entry.activity.phase != Phase.PREPARING:
                raise ActivityError(409, "只能在準備中取消活動。")
            entry.activity.phase = Phase.CANCELLED
            self.repository.release_owner(entry.activity)

    def finish(self, activity_id: str, session: str) -> None:
        with self.locked(activity_id, session, "teacher") as entry:
            activity = entry.activity
            if activity.phase != Phase.RESULTS or not activity.current_round.finalized:
                raise ActivityError(409, "請先關輪並定案。")
            activity.phase = Phase.FINISHED
            self.repository.release_owner(activity)

    def expire_due_rounds(self) -> None:
        for entry in self.repository.active_entries():
            with entry.lock:
                self._expire(entry.activity)

    def state(self, activity_id: str, session: str) -> dict:
        with self.locked(activity_id, session) as entry:
            activity = entry.activity
            teacher = session == activity.owner
            group = None if teacher else activity.groups[activity.memberships[session]]
            counts = self._success_counts(activity)
            questions = {question.id: question for question in activity.questions}
            groups = {group.id: group.number for group in activity.groups.values()}
            current = activity.current_round
            eligible = self._eligible(activity)

            def group_data(item: Group) -> dict:
                return {"number": item.number, "credential": item.credential, "successes": counts[item.id]}

            def round_data(round_: Round) -> dict:
                return {
                    "id": round_.id, "number": round_.number,
                    "fairness_enabled": round_.fairness_enabled,
                    "questions": [questions[q].number for q in round_.question_ids],
                    "deadline": None if round_.deadline is None else self.timestamp(round_.deadline),
                    "review_version": round_.review_version, "finalized": round_.finalized,
                    "results": [{"question": questions[result.question_id].number,
                                 "winner": groups.get(result.winner_group_id)} for result in round_.results],
                }

            view = {
                "id": activity.id, "phase": activity.phase, "role": "teacher" if teacher else "student",
                "round": round_data(current) if current else None,
                "history": [round_data(round_) for round_ in activity.rounds if round_.finalized],
                "unsuccessful_questions": [questions[q].number for q in eligible] if activity.phase == Phase.FINISHED else [],
            }
            if teacher:
                submitted = activity.submitted_groups
                view.update(code=activity.code, group_limit=activity.group_limit,
                            groups=[{**group_data(item), "can_delete": item.id not in submitted}
                                    for item in sorted(activity.groups.values(), key=lambda group: group.number)],
                            available_questions=[{"id": q, "number": questions[q].number} for q in eligible])
            else:
                view["group"] = group_data(group)
            key = "teacher" if teacher else group.id
            version, previous = entry.projections.get(key, (0, None))
            if view != previous:
                version += 1
                entry.projections[key] = (version, view)
            return {**view, "state_version": version, "server_time": self.timestamp(self.clock())}

    @staticmethod
    def _eligible(activity: Activity) -> tuple[str, ...]:
        successful = activity.successful_questions
        return tuple(q.id for q in activity.questions if q.id not in successful)

    @staticmethod
    def _success_counts(activity: Activity) -> Counter:
        return Counter(result.winner_group_id for round_ in activity.rounds if round_.finalized
                       for result in round_.results if result.winner_group_id is not None)

    @staticmethod
    def _match_round(activity: Activity, round_id: str) -> Round:
        round_ = activity.current_round
        if round_ is None or round_.id != round_id:
            raise ActivityError(409, "此輪次已失效，請重新同步。")
        return round_

    def _expire(self, activity: Activity) -> None:
        if activity.phase == Phase.OPEN:
            round_ = activity.current_round
            if round_.deadline is not None and self.clock() >= round_.deadline:
                self._close(activity, round_)

    @staticmethod
    def _close(activity: Activity, round_: Round) -> None:
        candidates = {q: [] for q in round_.question_ids}
        for submission in round_.submissions.values():
            candidates[submission.question_id].append(submission)

        def reception(submission):
            return submission.received_at, submission.receive_sequence

        numbers = {q.id: q.number for q in activity.questions}
        counts = Counter(round_.baseline)
        question_order = sorted(candidates, key=lambda q: (
            min(map(reception, candidates[q]), default=(float("inf"), 0)), numbers[q],
        ))
        results = []
        for question_id in question_order:
            accepted = (sub for sub in candidates[question_id] if not sub.rejected)
            winner = min(accepted, default=None, key=lambda sub: (
                counts[sub.group_id] if round_.fairness_enabled else 0, *reception(sub),
            ))
            results.append(QuestionResult(question_id, winner.group_id if winner else None))
            if winner:
                counts[winner.group_id] += 1
        round_.fixed_question_order = tuple(question_order)
        round_.results = tuple(results)
        round_.review_version = 1
        activity.phase = Phase.RESULTS
