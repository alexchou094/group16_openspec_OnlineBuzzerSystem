import csv
import io
from codecs import BOM_UTF8
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest
import requests

from app.adapters.guided_notes import GoogleWorksheetReader, SheetAccessError, SheetWriteUncertain
from app.domain.guided_notes import MergeRange, coordinates, parse_worksheet
from app.domain.result_export import WriteStatus
from app.repositories.memory import MemoryRepository
from app.services.activities import ActivityError, ActivityService
from app.services.result_export import CSV_HEADER, ResultExport


class FakeWriter:
    def __init__(self, sheet, update_cell):
        self.sheet = sheet
        self.update_cell = update_cell
        self.reads = 0
        self.writes = []
        self.write_error = None
        self.after_write = lambda sheet: sheet

    def read_sheet(self, spreadsheet_id, sheet_id):
        self.reads += 1
        if self.sheet is None or self.sheet.sheet_id != sheet_id:
            raise SheetAccessError(409, "找不到所選工作表。")
        return self.sheet

    def write_answers(self, sheet, answers):
        assert sheet is self.sheet
        self.writes.append(answers)
        if isinstance(self.write_error, SheetAccessError):
            raise self.write_error
        for target, answer in answers:
            self.sheet = self.update_cell(self.sheet, *coordinates(target), answer)
        self.sheet = self.after_write(self.sheet)
        if self.write_error:
            raise self.write_error


@pytest.fixture
def export_setup(mixed_sheet, update_cell):
    service = ActivityService(MemoryRepository(), clock=lambda: 5)
    activity = service.create("teacher", 4)
    source, questions = parse_worksheet(mixed_sheet)
    service.replace_questions(activity.id, activity.owner, source, questions)
    for group in range(1, 5):
        service.join(str(group), activity.code, group, None)
    writer = FakeWriter(mixed_sheet, update_cell)
    return service, activity, ResultExport(service), writer


def opened_round(service, activity, question, submissions):
    round_id = service.open_round(activity.id, activity.owner, [question], False, None)
    ids = [
        service.submit(activity.id, str(group), round_id, question, answers)["submission"]["id"]
        for group, answers in submissions
    ]
    return round_id, ids


def finish_review(service, activity, round_id, decisions):
    version = service.close_round(activity.id, activity.owner, round_id)
    for submission_id, decision in decisions:
        version = service.review(activity.id, activity.owner, round_id, submission_id, decision, version)
    service.finalize(activity.id, activity.owner, round_id, version)


def win(service, activity, question="q1", answers=(" =SUM(1,2) ", '  甲,"乙"\n第二行  ')):
    round_id, ids = opened_round(service, activity, question, [(1, answers)])
    finish_review(service, activity, round_id, [(ids[0], "CORRECT")])


def test_preview_empty_unfinalized_stale_and_concurrent(export_setup):
    service, activity, export, writer = export_setup
    key = activity.id, activity.owner
    no_io = lambda: pytest.fail("無結果不得使用 Google adapter")
    preview = export.preview(*key)
    with pytest.raises(ActivityError, match="沒有可寫回"):
        export.run(*key, no_io, preview_id=preview.id)
    win(service, activity)
    second, ids = opened_round(service, activity, "q3", [(2, ["仍在搶答"])])
    preview = export.preview(*key)
    assert {cell.target for cell in preview.cells} == {"H5", "H6"}
    started, release = Event(), Event()
    read = writer.read_sheet

    def wait_for_round(*args):
        started.set()
        assert release.wait(4)
        return read(*args)

    writer.read_sheet = wait_for_round
    with ThreadPoolExecutor(max_workers=2) as pool:
        write = pool.submit(export.run, *key, lambda: writer, preview_id=preview.id)
        try:
            assert started.wait(3)
            assert export.attempt_view(*key)["status"] == WriteStatus.RUNNING
            with pytest.raises(ActivityError, match="正在執行"):
                export.run(*key, no_io, preview_id=preview.id)
            finalize = pool.submit(finish_review, service, activity, second, [(ids[0], "CORRECT")])
            finalize.result(timeout=2)
        finally:
            release.set()
        assert write.result(timeout=3)["older_snapshot"]
    assert dict(writer.writes[0]).keys() == {"H5", "H6"}
    with pytest.raises(ActivityError, match="重新整理並預覽"):
        export.run(*key, no_io, preview_id=preview.id)


@pytest.mark.parametrize("change", ["insert_row", "question_number", "merge", "deleted", "moved_answer", "wrong_file"])
def test_structure_changes_stop_all_writes(export_setup, update_cell, change):
    service, activity, export, writer = export_setup
    if change == "moved_answer":
        writer.sheet = replace(writer.sheet, merges=tuple(merge for merge in writer.sheet.merges if merge.row != 10))
        source, questions = parse_worksheet(writer.sheet)
        service.replace_questions(activity.id, activity.owner, source, questions)
    win(service, activity)
    key = activity.id, activity.owner
    preview = export.preview(*key)
    sheet = writer.sheet
    if change == "insert_row":
        writer.sheet = replace(sheet, values=((),) + sheet.values, merges=tuple(
            replace(merge, row=merge.row + 1, end_row=merge.end_row + 1) for merge in sheet.merges))
    elif change == "question_number":
        writer.sheet = update_cell(sheet, 7, 1, 3)
    elif change == "merge":
        writer.sheet = replace(sheet, merges=sheet.merges + (MergeRange(4, 7, 5, 8),))
    elif change == "deleted":
        writer.sheet = None
    elif change == "moved_answer":
        writer.sheet = update_cell(sheet, 10, 6, "(1)")
    else:
        writer.sheet = replace(sheet, spreadsheet_id="other")
    attempt = export.run(*key, lambda: writer, preview_id=preview.id)
    assert attempt["status"] == WriteStatus.FAILED and not writer.writes
    assert "結構" in attempt["message"] or "找不到" in attempt["message"]


def test_precise_mixed_cells_and_raw_readback(export_setup, update_cell):
    service, activity, export, writer = export_setup
    win(service, activity)
    win(service, activity, "q3", ["  x\ny  "])
    key = activity.id, activity.owner
    writer.sheet = update_cell(update_cell(writer.sheet, 4, 7, "原答案"), 12, 6, "保留答案")
    before = writer.sheet
    preview = export.preview(*key)
    attempt = export.run(*key, lambda: writer, preview_id=preview.id)
    assert attempt["status"] == WriteStatus.SUCCESS and writer.reads == 2
    assert dict(writer.writes[0]) == {"H5": " =SUM(1,2) ", "H6": '  甲,"乙"\n第二行  ', "G11": "  x\ny  "}
    assert writer.sheet.merges == before.merges
    assert writer.sheet.value(4, 6) == "（1）"
    assert writer.sheet.value(12, 6) == "保留答案"


def test_answer_resembling_subnumber_survives_readback_and_later_exports(export_setup):
    service, activity, export, writer = export_setup
    writer.sheet = replace(writer.sheet, merges=tuple(m for m in writer.sheet.merges if m.row != 10))
    source, questions = parse_worksheet(writer.sheet)
    service.replace_questions(activity.id, activity.owner, source, questions)
    win(service, activity, "q3", ["(1)"])
    key = activity.id, activity.owner
    for _ in range(2):
        preview = export.preview(*key)
        assert export.run(*key, lambda: writer, preview_id=preview.id)["status"] == WriteStatus.SUCCESS
    assert writer.sheet.has_raw_answer("G11", "(1)")


@pytest.mark.parametrize("verify_only", [True, False])
def test_uncertain_verify_and_retry_keep_old_snapshot(export_setup, verify_only):
    service, activity, export, writer = export_setup
    win(service, activity)
    key = activity.id, activity.owner
    preview = export.preview(*key)
    writer.write_error = SheetWriteUncertain("回應遺失")
    lost = export.run(*key, lambda: writer, preview_id=preview.id)
    assert lost["status"] == WriteStatus.UNCERTAIN
    win(service, activity, "q3", ["新定案"])
    writer.write_error = SheetAccessError(403, "權限失效")
    retry = export.run(*key, lambda: writer, attempt_id=lost["id"])
    assert retry["status"] == WriteStatus.UNCERTAIN and retry["older_snapshot"]
    assert writer.writes[0] == writer.writes[1]
    writer.write_error = None
    recovered = export.run(*key, lambda: writer, attempt_id=lost["id"], verify_only=verify_only)
    assert recovered["status"] == WriteStatus.SUCCESS and recovered["older_snapshot"]
    assert writer.writes == [writer.writes[0]] * (2 if verify_only else 3)
    assert len(activity.successful_questions) == activity.finalized_revision == 2


@pytest.mark.parametrize("drift", [True, False])
def test_postwrite_drift_or_unverified_text_stays_uncertain(export_setup, update_cell, drift):
    service, activity, export, writer = export_setup
    win(service, activity)
    key = activity.id, activity.owner
    preview = export.preview(*key)
    writer.after_write = (
        (lambda sheet: update_cell(sheet, 4, 1, "9")) if drift else
        (lambda sheet: replace(sheet, literal_text=frozenset({("H6", '  甲,"乙"\n第二行  ')})))
    )
    attempt = export.run(*key, lambda: writer, preview_id=preview.id)
    assert attempt["status"] == WriteStatus.UNCERTAIN and len(writer.writes) == 1
    if drift:
        assert export.run(*key, lambda: writer, attempt_id=attempt["id"])["status"] == WriteStatus.UNCERTAIN
        assert len(writer.writes) == 1


def test_google_adapter_raw_request_and_failure_classification(mixed_sheet, monkeypatch):
    writer = GoogleWorksheetReader("fake", datetime.now(UTC) + timedelta(minutes=1))
    sheet = replace(mixed_sheet, title="教師's 測試")
    code = 200

    def post(url, **kwargs):
        assert url == "https://sheets.googleapis.com/v4/spreadsheets/test_sheet/values:batchUpdate"
        assert kwargs["json"] == {"valueInputOption": "RAW", "data": [
            {"range": "'教師''s 測試'!H5", "values": [["=1+1"]]},
            {"range": "'教師''s 測試'!G11", "values": [["  x\ny  "]]},
        ]}
        assert kwargs["timeout"] == 15 and not kwargs["allow_redirects"]
        if code is None:
            raise requests.Timeout()
        return SimpleNamespace(status_code=code)

    monkeypatch.setattr(requests, "post", post)
    cells = (("H5", "=1+1"), ("G11", "  x\ny  "))
    writer.write_answers(sheet, cells)
    for code, error in [(403, SheetAccessError), (503, SheetWriteUncertain), (None, SheetWriteUncertain)]:
        with pytest.raises(error):
            writer.write_answers(sheet, cells)


def test_csv_finalized_only_all_statuses_and_original_answers(export_setup):
    service, activity, export, _ = export_setup
    key = activity.id, activity.owner
    empty = export.csv(*key)
    assert empty.startswith(BOM_UTF8) and list(csv.reader(io.StringIO(empty.decode("utf-8-sig")))) == [list(CSV_HEADER)]
    round_id, ids = opened_round(service, activity, "q1", [
        (1, [" 判錯 ", "保留"]), (2, ["\t =1+2", '  引號"及,逗號\n第二行  ']),
        (3, ["後續未審閱", "答案"]),
    ])
    finish_review(service, activity, round_id, [(ids[0], "INCORRECT"), (ids[1], "CORRECT")])
    opened_round(service, activity, "q2", [(4, ["尚未", "定案"])])
    records = list(csv.DictReader(io.StringIO(export.csv(*key).decode("utf-8-sig"), newline="")))
    assert len(records) == 6
    assert [(r["組別"], r["順位"], r["是否成功"], r["是否被判錯"], r["審閱狀態"]) for r in records] == [
        ("2", "1", "是", "否", "正確"), ("2", "1", "是", "否", "正確"),
        ("3", "2", "否", "否", "未審閱"), ("3", "2", "否", "否", "未審閱"),
        ("1", "", "否", "是", "判錯"), ("1", "", "否", "是", "判錯"),
    ]
    assert [r["子空格"] for r in records] == ["1", "2"] * 3
    assert records[0]["答案"] == "'\t =1+2"
    assert records[1]["答案"] == '  引號"及,逗號\n第二行  '
    assert records[0]["送出時間"] == datetime.fromisoformat(records[0]["送出時間"]).isoformat(timespec="microseconds")
