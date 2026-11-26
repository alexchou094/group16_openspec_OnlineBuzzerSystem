from dataclasses import replace

import pytest

from app.adapters.guided_notes import SheetAccessError
from app.domain.guided_notes import MergeRange, WorksheetError, parse_worksheet
from app.repositories.memory import MemoryRepository
from app.services.activities import ActivityError, ActivityService
from app.services.guided_notes import GuidedNotesImport


def targets(questions):
    return [[blank.target for blank in question.blanks] for question in questions]


@pytest.mark.parametrize(("fixture_name", "expected", "shifted_expected"), [
    ("single_sheet", [["G5"], ["G7"], ["G9"], ["G11"], ["G13"], ["G15"]],
     [["I8"], ["I10"], ["I12"], ["I14"], ["I16"], ["I18"]]),
    ("mixed_sheet", [["H5", "H6"], ["H8", "H9"], ["G11"], ["G13"]],
     [["J8", "J9"], ["J11", "J12"], ["I14"], ["I16"]]),
])
def test_equivalent_examples_and_shifted_coordinates(request, fixture_name, expected, shifted_expected):
    sheet = request.getfixturevalue(fixture_name)
    source, questions = parse_worksheet(sheet)
    assert source.sheet_id == sheet.sheet_id
    assert [question.number for question in questions] == list(range(1, len(expected) + 1))
    assert targets(questions) == expected
    moved = replace(
        sheet, title="不同名稱", values=((),) * 3 + tuple((None, None) + row for row in sheet.values),
        merges=tuple(MergeRange(m.row + 3, m.column + 2, m.end_row + 3, m.end_column + 2)
                     for m in sheet.merges),
    )
    shifted_source, shifted_questions = parse_worksheet(moved)
    assert targets(shifted_questions) == shifted_expected
    assert source.fingerprint != shifted_source.fingerprint


def test_nonempty_horizontal_question_number_merge_is_rejected(single_sheet, update_cell):
    invalid = update_cell(single_sheet, 5, 1, 2)
    with pytest.raises(WorksheetError, match="B6：題號資料只能在題號欄縱向合併"):
        parse_worksheet(invalid)


def test_repeat_and_fingerprint(mixed_sheet, update_cell):
    source, expected = parse_worksheet(mixed_sheet)
    assert source.headers == (("B3", "題號"), ("G3", "作答"))
    assert source.merge_ranges == ("G3:H3", "B5:B6", "B8:B9", "G11:H11", "G13:H13")
    values = update_cell(update_cell(mixed_sheet, 5, 1, "01"), 8, 1, 2)
    repeated = replace(values, merges=tuple(m for m in values.merges if m.column != 1))
    repeat_source, questions = parse_worksheet(repeated)
    assert questions == expected
    assert repeat_source.fingerprint != source.fingerprint
    edited = update_cell(update_cell(mixed_sheet, 4, 7, "已有答案"), 10, 6, "(1)")
    assert parse_worksheet(replace(edited, title="改名"))[0].fingerprint == source.fingerprint
    assert parse_worksheet(replace(mixed_sheet, sheet_id=5))[0].fingerprint != source.fingerprint


@pytest.mark.parametrize(("row", "column", "value", "error"), [
    (4, 1, "甲", "B5：題號須為正整數"),
    (4, 1, True, "B5：題號須為正整數"),
    (7, 1, 3, "B8：題號須從 1 連續遞增"),
    (7, 1, 1, "B8：題號須從 1 連續遞增"),
    (5, 6, "(1)", "G6：子編號須連續"),
    (5, 6, "", "G6：子編號須連續"),
    (5, 6, "（2)", "G6：子編號須使用"),
    (6, 6, "(3)", "G7：子編號沒有對應題號"),
    (2, 8, "作答", "I3：表頭有歧義"),
    (2, 6, "", "B3：找不到同列"),
])
def test_parser_rejects_invalid_layout_with_location(mixed_sheet, update_cell, row, column, value, error):
    with pytest.raises(WorksheetError, match=error):
        parse_worksheet(update_cell(mixed_sheet, row, column, value))


def test_missing_or_overlapping_answer_cells(mixed_sheet):
    with pytest.raises(WorksheetError, match="G5：缺少子編號右側的作答格"):
        sheet = replace(mixed_sheet, column_count=7, merges=mixed_sheet.merges[1:3])
        parse_worksheet(sheet)
    with pytest.raises(WorksheetError, match="H6：作答格合併範圍重疊"):
        parse_worksheet(replace(mixed_sheet, merges=mixed_sheet.merges + (MergeRange(4, 7, 6, 8),)))


def test_import_selection_and_atomicity(mixed_sheet, single_sheet, update_cell, fake_worksheet_reader):
    activity_service = ActivityService(MemoryRepository())
    activity = activity_service.create("teacher", 2)
    importer = GuidedNotesImport(activity_service)
    reader = fake_worksheet_reader(mixed_sheet)
    assert importer.select(activity.id, "teacher", reader, "test_sheet", None) == {"imported": True}
    assert reader.reads == [("test_sheet", 0)]
    original = activity.source, activity.questions
    reader.sheets[1] = update_cell(single_sheet, 4, 1, 2)
    assert importer.select(activity.id, "teacher", reader, "test_sheet", None) == {
        "sheets": [{"id": 0, "title": "混合"}, {"id": 1, "title": "單題"}],
    }
    assert (activity.source, activity.questions) == original
    assert reader.reads == [("test_sheet", 0)]
    with pytest.raises(ActivityError, match="B5：題號須從 1"):
        importer.select(activity.id, "teacher", reader, "test_sheet", 1)
    with pytest.raises(SheetAccessError, match="檔案權限"):
        importer.select(activity.id, "teacher", reader, "unavailable", None)
    assert (activity.source, activity.questions) == original
    reader.sheets[1] = single_sheet
    assert importer.select(activity.id, "teacher", reader, "test_sheet", 1) == {"imported": True}
    assert len(activity.questions) == 6
    assert activity.source.sheet_id == 1
    activity_service.open_round(activity.id, "teacher", None, False, None)
    with pytest.raises(ActivityError, match="只能在準備中"):
        importer.select(activity.id, "teacher", reader, "test_sheet", 0)
    assert activity.source.sheet_id == 1


def test_open_round_while_read_is_pending_prevents_replacement(single_sheet, mixed_sheet, fake_worksheet_reader):
    activities = ActivityService(MemoryRepository())
    activity = activities.create("teacher", 2)
    importer = GuidedNotesImport(activities)
    importer.select(activity.id, "teacher", fake_worksheet_reader(single_sheet), "test_sheet", None)
    original = activity.source, activity.questions

    class OpenRoundDuringRead(fake_worksheet_reader):
        def read_sheet(self, spreadsheet_id, sheet_id):
            activities.open_round(activity.id, "teacher", None, False, None)
            return super().read_sheet(spreadsheet_id, sheet_id)

    with pytest.raises(ActivityError, match="只能在準備中"):
        importer.select(activity.id, "teacher", OpenRoundDuringRead(mixed_sheet), "test_sheet", None)
    assert (activity.source, activity.questions) == original
