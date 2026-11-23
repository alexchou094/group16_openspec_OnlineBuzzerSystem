from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from app.domain.activity import Blank, Phase, Question, SourceSnapshot
from app.repositories.memory import MemoryRepository
from app.services.activities import ActivityError, ActivityService


@pytest.fixture
def system():
    now = [100.0]
    service = ActivityService(MemoryRepository(), lambda: now[0])
    activity = service.create("teacher", 3)
    return service, activity, now


def import_questions(service, activity):
    questions = tuple(Question(f"q{n}", n, (Blank("1", f"B{n + 1}"),)) for n in (1, 2))
    service.replace_questions(activity.id, activity.owner, SourceSnapshot("sheet", 5, "練習", "shape"), questions)


def test_activity_creation_is_atomic_and_isolated():
    service = ActivityService(MemoryRepository())
    barrier = Barrier(2)

    def create():
        barrier.wait()
        try:
            return service.create("teacher", 1)
        except ActivityError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: create(), range(2)))
    assert (first == 409) != (second == 409)
    owner_activity = second if first == 409 else first
    other = service.create("other", 1)
    assert owner_activity.code != other.code
    with pytest.raises(ActivityError, match="權限"):
        service.cancel(owner_activity.id, other.owner)
    service.cancel(owner_activity.id, owner_activity.owner)
    assert service.create("teacher", 1).code != owner_activity.code
    with pytest.raises(ActivityError, match="失效"):
        ActivityService(MemoryRepository()).state(owner_activity.id, "teacher")


@pytest.mark.parametrize("number", [None, "", "1", 0, -1, 4, 1.0, 1.5, True])
def test_invalid_group_number(system, number):
    service, activity, _ = system
    with pytest.raises(ActivityError, match="組號"):
        service.join("student", activity.code, number, None)


def test_group_credentials_and_device_revocation(system):
    service, activity, _ = system
    service.join("one", activity.code, 1, None)
    service.join("three", activity.code, 3, None)
    credential = service.state(activity.id, "teacher")["groups"][0]["credential"]
    assert len(credential) == 6 and credential.isdecimal()
    for invalid in (None, "abcdef", "００００００"):
        with pytest.raises(ActivityError, match="憑證"):
            service.join("another", activity.code, 1, invalid)
    service.join("two", activity.code, 1, credential)
    assert service.state(activity.id, "one")["group"] == service.state(activity.id, "two")["group"]
    service.leave(activity.id, "one")
    service.leave(activity.id, "two")
    service.join("recovery", activity.code, 1, credential)
    service.join("backup", activity.code, 1, credential)
    service.delete_group(activity.id, "teacher", 1)
    for device in ("recovery", "backup"):
        with pytest.raises(ActivityError, match="權限"):
            service.state(activity.id, device)
    with pytest.raises(ActivityError, match="憑證"):
        service.join("one", activity.code, 1, credential)
    service.join("new", activity.code, 1, None)
    assert service.state(activity.id, "new")["group"]["credential"] != credential


@pytest.mark.parametrize("fairness", [False, True])
@pytest.mark.parametrize("seconds", [None, 1, 3600])
def test_round_modes_and_deadlines(system, fairness, seconds):
    service, activity, now = system
    import_questions(service, activity)
    service.open_round(activity.id, "teacher", None, fairness, seconds)
    round_ = activity.current_round
    assert round_.question_ids == ("q1", "q2")
    assert round_.fairness_enabled is fairness
    assert round_.deadline == (None if seconds is None else now[0] + seconds)
    if seconds is not None:
        now[0] += seconds - 0.1
        service.expire_due_rounds()
        assert activity.phase == Phase.OPEN
        now[0] = round_.deadline
        service.expire_due_rounds()
        assert activity.phase == Phase.RESULTS


@pytest.mark.parametrize("seconds", [0, 3601, 1.5, True, "2"])
def test_round_rejects_invalid_seconds(system, seconds):
    service, activity, _ = system
    import_questions(service, activity)
    with pytest.raises(ActivityError, match="倒數"):
        service.open_round(activity.id, "teacher", None, False, seconds)
    assert activity.phase == Phase.PREPARING and activity.current_round is None


def test_manual_and_automatic_close_publish_once(system):
    service, activity, now = system
    import_questions(service, activity)
    round_id = service.open_round(activity.id, "teacher", None, False, 1)
    now[0] += 1
    barrier = Barrier(2)

    def run(action):
        barrier.wait()
        return action()

    with ThreadPoolExecutor(max_workers=2) as pool:
        manual = pool.submit(run, lambda: service.close_round(activity.id, "teacher", round_id))
        auto = pool.submit(run, service.expire_due_rounds)
        assert manual.result() == 1
        auto.result()
    result = activity.current_round.results
    assert len(result) == 2 and activity.phase == Phase.RESULTS
    assert service.close_round(activity.id, "teacher", round_id) == 1
    assert activity.current_round.results is result


def test_lifecycle_preserves_history_when_devices_change_group(system):
    service, activity, now = system
    with pytest.raises(ActivityError, match="匯入"):
        service.open_round(activity.id, "teacher", None, False, None)
    import_questions(service, activity)
    with pytest.raises(ActivityError, match="題目"):
        service.open_round(activity.id, "teacher", [], False, None)
    service.join("student", activity.code, 1, None)
    round_id = service.open_round(activity.id, "teacher", None, False, None)
    first = activity.groups[1]
    for operation in (lambda: service.leave(activity.id, "student"), lambda: service.cancel(activity.id, "teacher"),
                      lambda: service.finish(activity.id, "teacher"), lambda: import_questions(service, activity)):
        with pytest.raises(ActivityError):
            operation()
    service.join("late", activity.code, 2, None)
    assert service.state(activity.id, "late")["group"]["successes"] == 0
    with service.locked(activity.id, "teacher") as entry:
        current = entry.activity.current_round
        accepted = current.record_accepted("one", "q1", first.id, ("答案",), now[0])
        assert current.record_accepted("repeat", "q1", first.id, ("不同答案",), now[0]) is accepted
        rejected = current.record_accepted("two", "q2", first.id, ("答案",), now[0])
        assert (accepted.receive_sequence, rejected.receive_sequence) == (1, 2)
        current.submissions["q2", first.id] = replace(rejected, rejected=True)
    service.close_round(activity.id, "teacher", round_id)
    with pytest.raises(ActivityError, match="定案"):
        service.open_round(activity.id, "teacher", None, True, None)
    with pytest.raises(ActivityError, match="版本"):
        service.finalize(activity.id, "teacher", round_id, 0)
    service.finalize(activity.id, "teacher", round_id, 1)
    service.finalize(activity.id, "teacher", round_id, 1)
    assert activity.finalized_revision == 1
    service.leave(activity.id, "student")
    service.join("student", activity.code, 3, None)
    assert service.state(activity.id, "student")["group"]["successes"] == 0
    assert service.state(activity.id, "teacher")["groups"][0]["successes"] == 1
    with pytest.raises(ActivityError, match="提交"):
        service.delete_group(activity.id, "teacher", 1)
    with pytest.raises(ActivityError, match="題目"):
        service.open_round(activity.id, "teacher", ["q1"], True, None)
    second_id = service.open_round(activity.id, "teacher", None, True, None)
    assert second_id != round_id and activity.current_round.question_ids == ("q2",)
    assert activity.current_round.baseline[first.id] == 1
    service.close_round(activity.id, "teacher", second_id)
    service.finalize(activity.id, "teacher", second_id, 1)
    service.finish(activity.id, "teacher")
    final = service.state(activity.id, "student")
    assert final["phase"] == "FINISHED" and final["unsuccessful_questions"] == [2]
    assert final["history"][0]["results"][0] == {"question": 1, "winner": 1}
    assert len(final["history"]) == 2
    with pytest.raises(ActivityError, match="結束"):
        service.join("new", activity.code, 2, None)
    assert service.create("teacher", 1).phase == Phase.PREPARING


def test_group_with_only_rejected_submission_cannot_be_deleted(system):
    service, activity, now = system
    import_questions(service, activity)
    service.join("student", activity.code, 1, None)
    round_id = service.open_round(activity.id, "teacher", None, False, None)
    with service.locked(activity.id, "teacher"):
        group = activity.groups[1]
        sub = activity.current_round.record_accepted("id", "q1", group.id, ("答案",), now[0])
        activity.current_round.submissions["q1", group.id] = replace(sub, rejected=True)
    service.close_round(activity.id, "teacher", round_id)
    with pytest.raises(ActivityError, match="提交"):
        service.delete_group(activity.id, "teacher", 1)
