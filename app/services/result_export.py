import csv
import io
import unicodedata
from collections.abc import Callable
from contextlib import contextmanager

from app.adapters.guided_notes import SheetAccessError, SheetWriteUncertain, WorksheetWriter
from app.domain.activity import Phase, ReviewStatus
from app.domain.guided_notes import SheetSnapshot, WorksheetError, parse_worksheet
from app.domain.result_export import AnswerCell, ExportSnapshot, WriteAttempt, WriteStatus
from app.repositories.memory import new_id
from app.services.activities import ActivityError, ActivityService


CSV_HEADER = ("輪次", "題號", "子空格", "組別", "答案", "送出時間", "順位", "是否成功", "是否被判錯", "審閱狀態")
REVIEW_LABELS = {
    ReviewStatus.CORRECT: "正確",
    ReviewStatus.INCORRECT: "判錯",
    ReviewStatus.UNREVIEWED: "未審閱",
}


def csv_answer(answer: str) -> str:
    for character in answer:
        if character in "\t\r\n":
            return "'" + answer
        if character.isspace() or unicodedata.category(character) in {"Cc", "Cf"}:
            continue
        return ("'" + answer) if character in "=+-@＝＋－＠" else answer
    return answer


class ResultExport:
    def __init__(self, activities: ActivityService):
        self.activities = activities

    @contextmanager
    def locked(self, activity_id: str, session_id: str):
        with self.activities.locked(activity_id, session_id, "teacher") as entry:
            if entry.activity.phase == Phase.CANCELLED:
                raise ActivityError(409, "活動已取消，無法匯出。")
            yield entry

    def preview(self, activity_id: str, session_id: str) -> ExportSnapshot:
        with self.locked(activity_id, session_id) as entry:
            activity = entry.activity
            existing = entry.export_preview
            if existing and existing.revision == activity.finalized_revision and existing.source == activity.source:
                return existing
            questions = {question.id: question for question in activity.questions}
            groups = {group.id: group.number for group in activity.groups.values()}
            cells = []
            for round_ in activity.rounds:
                if not round_.finalized:
                    continue
                for result in round_.results:
                    if result.winner_group_id is None:
                        continue
                    winner = round_.submissions[result.question_id, result.winner_group_id]
                    question = questions[result.question_id]
                    cells.extend(
                        AnswerCell(question.number, blank.label, groups[winner.group_id], blank.target, answer)
                        for blank, answer in zip(question.blanks, winner.answers, strict=True)
                    )
            entry.export_preview = ExportSnapshot(
                new_id(), activity.finalized_revision, activity.source, activity.questions, tuple(cells),
            )
            return entry.export_preview

    def attempt_view(self, activity_id: str, session_id: str, attempt_id: str | None = None) -> dict | None:
        with self.locked(activity_id, session_id) as entry:
            if attempt_id is None:
                attempt = next(reversed(entry.export_attempts.values()), None)
            else:
                attempt = entry.export_attempts.get(attempt_id)
                if attempt is None:
                    raise ActivityError(404, "找不到指定的寫回嘗試。")
            if attempt is None:
                return None
            return {
                "id": attempt.id,
                "status": attempt.status,
                "message": attempt.message,
                "revision": attempt.snapshot.revision,
                "cell_count": len(attempt.snapshot.cells),
                "older_snapshot": attempt.snapshot.revision != entry.activity.finalized_revision,
            }

    def run(self, activity_id: str, session_id: str, reader: Callable[[], WorksheetWriter], *,
            preview_id: str | None = None, attempt_id: str | None = None, verify_only: bool = False) -> dict:
        with self.locked(activity_id, session_id) as entry:
            if any(item.status == WriteStatus.RUNNING for item in entry.export_attempts.values()):
                raise ActivityError(409, "已有寫回或核對作業正在執行。")
            if attempt_id is None:
                snapshot = entry.export_preview
                if (snapshot is None or snapshot.id != preview_id or
                        snapshot.revision != entry.activity.finalized_revision or snapshot.source != entry.activity.source):
                    raise ActivityError(409, "定案成果或來源已更新，請重新整理並預覽。")
                if not snapshot.cells:
                    raise ActivityError(409, "目前沒有可寫回的已定案勝者答案。")
                attempt = WriteAttempt(new_id(), snapshot)
                entry.export_attempts[attempt.id] = attempt
                previously_uncertain = False
            else:
                attempt = entry.export_attempts.get(attempt_id)
                if attempt is None:
                    raise ActivityError(404, "找不到指定的寫回嘗試。")
                if (verify_only and attempt.status != WriteStatus.UNCERTAIN
                        or attempt.status == WriteStatus.SUCCESS):
                    raise ActivityError(409, "目前的寫回狀態無法執行這項操作。")
                previously_uncertain = attempt.status == WriteStatus.UNCERTAIN
                attempt.status = WriteStatus.RUNNING
                attempt.message = ""
                entry.export_attempts[attempt_id] = entry.export_attempts.pop(attempt_id)
            known_answers = tuple({
                (cell.target, cell.answer)
                for previous in entry.export_attempts.values()
                if previous.status == WriteStatus.SUCCESS and previous.snapshot.source == attempt.snapshot.source
                for cell in previous.snapshot.cells
            })

        status = WriteStatus.UNCERTAIN
        message = "作業中斷，請重新核對寫回結果。"
        may_have_written = previously_uncertain
        try:
            writer = reader()
            snapshot = attempt.snapshot
            cells = tuple((cell.target, cell.answer) for cell in snapshot.cells)
            sheet = self._checked_sheet(writer, snapshot, known_answers + cells if verify_only else known_answers)
            if not verify_only:
                try:
                    writer.write_answers(sheet, cells)
                except SheetWriteUncertain:
                    may_have_written = True
                    raise
                may_have_written = True
                sheet = self._checked_sheet(writer, snapshot, known_answers + cells)
            if all(sheet.has_raw_answer(target, answer) for target, answer in cells):
                status, message = WriteStatus.SUCCESS, "已讀回核對，所有目標格均符合確認的答案。"
            else:
                message = "讀回的答案與確認內容有差異。請檢查學習單後再核對或重試。"
        except SheetWriteUncertain as error:
            message = str(error)
        except SheetAccessError as error:
            status = WriteStatus.UNCERTAIN if may_have_written else WriteStatus.FAILED
            message = str(error)
        finally:
            with self.locked(activity_id, session_id):
                attempt.status = status
                attempt.message = message
        return self.attempt_view(activity_id, session_id, attempt.id)

    @staticmethod
    def _checked_sheet(writer: WorksheetWriter, snapshot: ExportSnapshot,
                       known_answers: tuple[tuple[str, str], ...]) -> SheetSnapshot:
        original = snapshot.source
        sheet = writer.read_sheet(original.spreadsheet_id, original.sheet_id)
        try:
            current, questions = parse_worksheet(sheet.without_known_answers(known_answers))
        except WorksheetError as error:
            raise SheetAccessError(409, f"來源結構已變動。{error}") from None
        if current.fingerprint != original.fingerprint:
            differences = []
            if (current.spreadsheet_id, current.sheet_id) != (original.spreadsheet_id, original.sheet_id):
                differences.append("試算表或工作表識別")
            if current.headers != original.headers:
                differences.append("表頭位置")
            if current.merge_ranges != original.merge_ranges:
                differences.append("合併範圍")
            if questions != snapshot.questions or not differences:
                differences.append("題號、子編號或答案格位置")
            raise SheetAccessError(409, "來源結構已變動，請核對" + "、".join(differences) + "。")
        return sheet

    def csv(self, activity_id: str, session_id: str) -> bytes:
        with self.locked(activity_id, session_id) as entry:
            activity = entry.activity
            rounds = tuple(round_ for round_ in activity.rounds if round_.finalized)
            questions = {question.id: question for question in activity.questions}
            groups = {group.id: group.number for group in activity.groups.values()}

        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(CSV_HEADER)
        for round_ in rounds:
            submissions = {item.id: item for item in round_.submissions.values()}
            for result in sorted(round_.results, key=lambda item: questions[item.question_id].number):
                question = questions[result.question_id]
                candidates = sorted(result.candidates, key=lambda item: (
                    item.rank is None, item.rank or 0, submissions[item.submission_id].receive_sequence,
                ))
                for candidate in candidates:
                    submission = submissions[candidate.submission_id]
                    success = result.winner_group_id == submission.group_id
                    timestamp = self.activities.timestamp(submission.received_at, timespec="microseconds")
                    for blank, answer in zip(question.blanks, submission.answers, strict=True):
                        writer.writerow((
                            round_.number, question.number, blank.label, groups[submission.group_id],
                            csv_answer(answer), timestamp, candidate.rank,
                            "是" if success else "否", "是" if submission.rejected else "否",
                            REVIEW_LABELS[submission.review_status],
                        ))
        return buffer.getvalue().encode("utf-8-sig")
