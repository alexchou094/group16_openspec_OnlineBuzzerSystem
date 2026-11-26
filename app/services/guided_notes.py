from app.adapters.guided_notes import WorksheetReader
from app.domain.activity import Phase
from app.domain.guided_notes import WorksheetError, parse_worksheet
from app.services.activities import ActivityError, ActivityService


class GuidedNotesImport:
    def __init__(self, activities: ActivityService):
        self.activities = activities

    def check_preparing(self, activity_id: str, session_id: str):
        with self.activities.locked(activity_id, session_id, "teacher") as entry:
            if entry.activity.phase != Phase.PREPARING:
                raise ActivityError(409, "只能在準備中匯入題目。")

    def select(self, activity_id: str, session_id: str, reader: WorksheetReader,
               spreadsheet_id: str, sheet_id: int | None) -> dict:
        self.check_preparing(activity_id, session_id)
        sheets = reader.list_sheets(spreadsheet_id)
        if not sheets:
            raise ActivityError(422, "試算表內沒有可匯入的工作表。")
        if sheet_id is None and len(sheets) > 1:
            self.check_preparing(activity_id, session_id)
            return {"sheets": [{"id": sheet.id, "title": sheet.title} for sheet in sheets]}
        if sheet_id is None:
            sheet_id = sheets[0].id
        if not any(sheet.id == sheet_id for sheet in sheets):
            raise ActivityError(422, "所選工作表已不存在，請重新選檔。")
        sheet = reader.read_sheet(spreadsheet_id, sheet_id)
        if sheet.spreadsheet_id != spreadsheet_id or sheet.sheet_id != sheet_id:
            raise ActivityError(409, "Google 工作表識別已變更，請重新選檔。")
        try:
            source, questions = parse_worksheet(sheet)
        except WorksheetError as error:
            raise ActivityError(422, str(error)) from None
        self.activities.replace_questions(activity_id, session_id, source, questions)
        return {"imported": True}
