import json
import re
from dataclasses import dataclass, replace
from hashlib import sha256

from app.domain.activity import Blank, Question, SourceSnapshot


Cell = str | int | float | bool | None
SUB_NUMBER = re.compile(r"\(([0-9]+)\)|（([0-9]+)）")
SUB_LIKE = re.compile(r"[(（]\s*[+-]?[0-9]+\s*[)）]")


def address(row: int, column: int) -> str:
    letters = ""
    column += 1
    while column:
        column, digit = divmod(column - 1, 26)
        letters = chr(65 + digit) + letters
    return f"{letters}{row + 1}"


def coordinates(target: str) -> tuple[int, int]:
    letters, row = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", target).groups()
    column = 0
    for letter in letters:
        column = column * 26 + ord(letter) - ord("A") + 1
    return int(row) - 1, column - 1


@dataclass(frozen=True, order=True)
class MergeRange:
    row: int
    column: int
    end_row: int
    end_column: int

    def contains(self, row: int, column: int) -> bool:
        return self.row <= row < self.end_row and self.column <= column < self.end_column

    def overlaps(self, other: "MergeRange") -> bool:
        return (self.row < other.end_row and other.row < self.end_row
                and self.column < other.end_column and other.column < self.end_column)


@dataclass(frozen=True)
class SheetSnapshot:
    spreadsheet_id: str
    sheet_id: int
    title: str
    values: tuple[tuple[Cell, ...], ...]
    merges: tuple[MergeRange, ...] = ()
    column_count: int | None = None
    literal_text: frozenset[tuple[str, str]] | None = None

    def value(self, row: int, column: int) -> Cell:
        if row < len(self.values) and column < len(self.values[row]):
            return self.values[row][column]
        return None

    def has_raw_answer(self, target: str, answer: str) -> bool:
        if self.literal_text is not None:
            return (target, answer) in self.literal_text
        return self.value(*coordinates(target)) == answer

    def without_known_answers(self, answers: tuple[tuple[str, str], ...]) -> "SheetSnapshot":
        # 已核對的答案原文可能剛好長得像表頭或子編號。
        rows = list(self.values)
        for target, answer in answers:
            if self.has_raw_answer(target, answer):
                row, column = coordinates(target)
                if self.value(row, column) == answer:
                    rows[row] = rows[row][:column] + (None,) + rows[row][column + 1:]
        return replace(self, values=tuple(rows))


class WorksheetError(ValueError):
    def __init__(self, row: int, column: int, message: str):
        super().__init__(f"{address(row, column)}：{message}")


def _text(value: Cell) -> str:
    return value.strip() if isinstance(value, str) else ""


def _empty(value: Cell) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _number(value: Cell) -> int | None:
    if type(value) is int or (type(value) is float and value.is_integer()):
        return int(value)
    normalized = _text(value)
    if re.fullmatch(r"[0-9]+", normalized):
        try:
            return int(normalized)
        except ValueError:
            return None
    return None


def _header(sheet: SheetSnapshot) -> tuple[int, int, int]:
    match = None
    first_label = (0, 0)
    found_label = False
    for row, values in enumerate(sheet.values):
        numbers = [col for col, value in enumerate(values) if _text(value) == "題號"]
        answers = [col for col, value in enumerate(values) if _text(value) == "作答"]
        if not found_label and (numbers or answers):
            first_label = row, (numbers or answers)[0]
            found_label = True
        if numbers and answers:
            if match is not None or len(numbers) != 1 or len(answers) != 1:
                column = (numbers[1:] or answers[1:] or numbers)[0]
                raise WorksheetError(row, column, "表頭有歧義，請只保留一組「題號」與「作答」。")
            match = row, numbers[0], answers[0]
    if match is None:
        raise WorksheetError(*first_label, "找不到同列的「題號」與「作答」表頭。")
    return match


def _merge_at(merges: tuple[MergeRange, ...], row: int, column: int) -> MergeRange | None:
    matches = [merge for merge in merges if merge.contains(row, column)]
    if len(matches) > 1:
        raise WorksheetError(row, column, "合併範圍重疊，無法確認作答位置。")
    return matches[0] if matches else None


def parse_worksheet(sheet: SheetSnapshot) -> tuple[SourceSnapshot, tuple[Question, ...]]:
    header, number_col, answer_col = _header(sheet)
    merges = tuple(sorted(sheet.merges))
    answer_header = _merge_at(merges, header, answer_col)
    number_header = _merge_at(merges, header, number_col)
    if answer_header and (
        (answer_header.row, answer_header.column) != (header, answer_col)
        or answer_header.end_row != header + 1
    ):
        raise WorksheetError(header, answer_col, "作答表頭的合併範圍不明確。")
    if number_header and (
        (number_header.row, number_header.column) != (header, number_col)
        or number_header.end_row != header + 1
    ):
        raise WorksheetError(header, number_col, "題號表頭的合併範圍不明確。")
    answer_end = answer_header.end_column if answer_header else answer_col + 2
    if sheet.column_count is not None:
        answer_end = min(answer_end, sheet.column_count)
    if answer_col <= number_col < answer_end:
        if number_col == answer_col + 1 and answer_header is None:
            answer_end = number_col
        else:
            raise WorksheetError(header, answer_col, "題號與作答表頭的範圍重疊。")

    number_merges = tuple(
        merge for merge in merges if merge.end_row > header and merge.column <= number_col < merge.end_column
    )
    answer_merges = tuple(
        merge for merge in merges
        if merge.end_row > header and merge.column < answer_end and answer_col < merge.end_column
    )
    end_row = max(len(sheet.values), header + 1,
                  max((merge.end_row for merge in number_merges + answer_merges), default=0))
    rows_by_question: list[list[tuple[int, int | None, int]]] = []
    last_number = None
    for row in range(header + 1, end_row):
        value = sheet.value(row, number_col)
        number_merge = _merge_at(number_merges, row, number_col)
        if number_merge:
            if number_merge.column != number_col or number_merge.end_column != number_col + 1:
                cells = sheet.values[row] if row < len(sheet.values) else ()
                if number_merge.row == row and number_merge.end_row == row + 1 and all(_empty(cell) for cell in cells):
                    last_number = None
                    continue
                raise WorksheetError(row, number_col, "題號資料只能在題號欄縱向合併。")
            if row != number_merge.row:
                if not _empty(value):
                    raise WorksheetError(row, number_col, "題號合併續列不得另填題號。")
                value = sheet.value(number_merge.row, number_col)
        raw_sub = _text(sheet.value(row, answer_col))
        left_merge = _merge_at(answer_merges, row, answer_col)
        single_merge = left_merge and left_merge.end_column > answer_col + 1
        sub_match = None if single_merge else SUB_NUMBER.fullmatch(raw_sub)
        if not single_merge and not sub_match and SUB_LIKE.fullmatch(raw_sub):
            raise WorksheetError(row, answer_col, "子編號須使用 (1) 或（1）的格式。")
        if _empty(value):
            if sub_match:
                raise WorksheetError(row, answer_col, "子編號沒有對應題號。")
            last_number = None
            continue
        number = _number(value)
        if number is None or number < 1:
            raise WorksheetError(row, number_col, "題號須為正整數。")
        if number != last_number:
            if number != len(rows_by_question) + 1:
                raise WorksheetError(row, number_col, "題號須從 1 連續遞增，相同題號須相鄰。")
            rows_by_question.append([])
        last_number = number
        sub_number = _number(sub_match.group(1) or sub_match.group(2)) if sub_match else None
        if sub_match and sub_number is None:
            raise WorksheetError(row, answer_col, "子編號須為正整數。")
        rows_by_question[-1].append((row, sub_number, answer_col + int(sub_match is not None)))

    if not rows_by_question:
        raise WorksheetError(header, number_col, "表頭下方沒有題目。")
    questions = []
    used_targets = set()
    for number, rows in enumerate(rows_by_question, 1):
        blanks = []
        for index, (row, sub_number, column) in enumerate(rows, 1):
            if (len(rows) > 1 or sub_number is not None) and sub_number != index:
                raise WorksheetError(row, answer_col, f"子編號須連續，此列應為（{index}）。")
            if column >= answer_end:
                raise WorksheetError(row, answer_col, "缺少子編號右側的作答格。")
            if sub_number is not None and _merge_at(answer_merges, row, answer_col):
                raise WorksheetError(row, answer_col, "子編號格不可合併。")
            merge = _merge_at(answer_merges, row, column)
            target = (merge.row, merge.column) if merge else (row, column)
            if merge and (target != (row, column) or merge.end_column > answer_end):
                raise WorksheetError(row, column, "作答格合併範圍重疊或超出作答欄。")
            if merge and any(other is not merge and merge.overlaps(other) for other in answer_merges):
                raise WorksheetError(row, column, "作答格合併範圍重疊。")
            if target in used_targets:
                raise WorksheetError(row, column, "作答目標重疊。")
            used_targets.add(target)
            blanks.append(Blank(str(index), address(*target)))
        questions.append(Question(f"q{number}", number, tuple(blanks)))

    last_row = max(rows[-1][0] for rows in rows_by_question)
    relevant_merges = sorted({merge for merge in number_merges + answer_merges if merge.row <= last_row})
    headers = ((address(header, number_col), "題號"), (address(header, answer_col), "作答"))
    merge_ranges = tuple(
        f"{address(m.row, m.column)}:{address(m.end_row - 1, m.end_column - 1)}" for m in relevant_merges
    )
    structure = [
        sheet.spreadsheet_id, sheet.sheet_id, headers,
        [[question.number, [[sub, blank.target] for (_, sub, _), blank in zip(rows, question.blanks)]]
         for question, rows in zip(questions, rows_by_question)],
        merge_ranges,
    ]
    fingerprint = sha256(json.dumps(structure, ensure_ascii=False).encode()).hexdigest()
    source = SourceSnapshot(sheet.spreadsheet_id, sheet.sheet_id, sheet.title, fingerprint, headers, merge_ranges)
    return source, tuple(questions)
