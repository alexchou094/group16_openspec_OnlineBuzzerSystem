import secrets
from dataclasses import dataclass, field
from threading import Lock

from app.domain.activity import Activity


def new_id() -> str:
    return secrets.token_urlsafe(18)


@dataclass(frozen=True)
class Session:
    id: str
    csrf: str


@dataclass
class ActivityEntry:
    activity: Activity
    lock: Lock = field(default_factory=Lock)
    projections: dict[str, tuple[int, dict]] = field(default_factory=dict)


class MemoryRepository:
    def __init__(self):
        self._lock = Lock()
        self._activities: dict[str, ActivityEntry] = {}
        self._codes: dict[str, str] = {}
        self._active: dict[str, str] = {}
        self._sessions: dict[str, Session] = {}

    def session(self, session_id: str | None) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id) if session_id else None

    def new_session(self) -> Session:
        with self._lock:
            session = Session(new_id(), new_id())
            self._sessions[session.id] = session
            return session

    def create(self, owner: str, group_limit: int) -> Activity | None:
        with self._lock:
            if owner in self._active:
                return None
            while True:
                code = secrets.token_hex(4).upper()
                if code not in self._codes:
                    break
            activity = Activity(new_id(), code, owner, group_limit)
            self._activities[activity.id] = ActivityEntry(activity)
            self._codes[code] = activity.id
            self._active[owner] = activity.id
            return activity

    def get(self, activity_id: str) -> ActivityEntry | None:
        with self._lock:
            return self._activities.get(activity_id)

    def activity_id_for_code(self, code: str) -> str | None:
        with self._lock:
            return self._codes.get(code)

    def active_activity_id(self, owner: str) -> str | None:
        with self._lock:
            return self._active.get(owner)

    def release_owner(self, activity: Activity) -> None:
        with self._lock:
            if self._active.get(activity.owner) == activity.id:
                del self._active[activity.owner]

    def active_entries(self) -> tuple[ActivityEntry, ...]:
        with self._lock:
            return tuple(self._activities[activity_id] for activity_id in self._active.values())
