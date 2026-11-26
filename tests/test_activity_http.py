import pytest
from fastapi.testclient import TestClient

from app.main import create_app

ORIGIN = "http://testserver"


@pytest.fixture
def app():
    return create_app(demo=True, hosts=("testserver",))


def authenticate(client):
    response = client.get("/api/session")
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


@pytest.mark.parametrize("value", [None, "", "1", 0, -1, 1.0, 1.5, True])
def test_create_requires_positive_integer(app, value):
    with TestClient(app) as client:
        response = client.post("/activities", headers=authenticate(client), json={"group_limit": value})
        assert response.status_code == 422
        assert "正整數" in response.json()["detail"]


def test_session_mutations_and_student_projection(app):
    with TestClient(app) as teacher:
        student1 = TestClient(app)
        student2 = TestClient(app)
        stranger = TestClient(app)
        headers = authenticate(teacher)
        assert authenticate(teacher) == headers
        assert teacher.post("/activities", json={"group_limit": 2}).status_code == 403
        assert teacher.post("/activities", headers={**headers, "Origin": "http://other"},
                            json={"group_limit": 2}).status_code == 403
        created = teacher.post("/activities", headers=headers, json={"group_limit": 2})
        assert created.status_code == 201
        activity = created.json()
        path = f"/activities/{activity['id']}"
        assert teacher.post("/activities", headers=headers, json={"group_limit": 2}).status_code == 409
        student1_headers = authenticate(student1)
        student2_headers = authenticate(student2)
        other_headers = authenticate(stranger)
        other = stranger.post("/activities", headers=other_headers, json={"group_limit": 1}).json()
        assert other["code"] != activity["code"]
        assert student1.post("/activities/join", headers=student1_headers,
                             json={"code": activity["code"], "group_number": 1}).status_code == 200
        first = student1.get(path + "/state")
        assert first.headers["Cache-Control"] == "no-store"
        student2.post("/activities/join", headers=student2_headers,
                      json={"code": activity["code"], "group_number": 2}).raise_for_status()
        student_view = student1.get(path + "/state").json()
        assert student_view["state_version"] == first.json()["state_version"]
        assert student_view["group"]["number"] == 1 and "groups" not in student_view and "code" not in student_view
        for client, identity in [(student1, student1_headers), (stranger, other_headers)]:
            for route in ("/cancel", "/finish", "/demo-questions"):
                denied = client.post(path + route, headers=identity)
                assert denied.status_code == 403 and list(denied.json()) == ["detail"]
        assert stranger.get(path + "/state").status_code == 403
        assert teacher.get(path + "/state").json()["phase"] == "PREPARING"
        assert teacher.post(path + "/cancel", headers=headers).status_code == 204
        assert student1.get(path + "/state").json()["phase"] == "CANCELLED"
        assert student2.post("/activities/join", headers=student2_headers,
                             json={"code": activity["code"], "group_number": 2}).status_code == 409
        assert teacher.post("/activities", headers=headers, json={"group_limit": 2}).status_code == 201
        with TestClient(create_app(hosts=("testserver",))) as restarted:
            restarted.cookies.update(teacher.cookies)
            assert restarted.get(path + "/state").status_code == 404


def test_student_session_lists_only_current_activity_memberships(app):
    with TestClient(app) as first_teacher, TestClient(app) as second_teacher, TestClient(app) as student:
        first_headers = authenticate(first_teacher)
        second_headers = authenticate(second_teacher)
        student_headers = authenticate(student)
        first = first_teacher.post("/activities", headers=first_headers, json={"group_limit": 1}).json()
        second = second_teacher.post("/activities", headers=second_headers, json={"group_limit": 1}).json()

        assert student.get("/api/session").json()["student_activities"] == []
        for activity in (first, second):
            student.post("/activities/join", headers=student_headers,
                         json={"code": activity["code"], "group_number": 1}).raise_for_status()
        assert student.get("/api/session").json()["student_activities"] == [first, second]
        assert first_teacher.get("/api/session").json()["active_activity"] == first["id"]

        student.post(f"/activities/{first['id']}/leave", headers=student_headers).raise_for_status()
        assert student.get("/api/session").json()["student_activities"] == [second]
        second_teacher.post(f"/activities/{second['id']}/cancel", headers=second_headers).raise_for_status()
        assert student.get("/api/session").json()["student_activities"] == []


def test_empty_group_release_and_open_delete_authorization(app):
    with TestClient(app) as teacher, TestClient(app) as first, TestClient(app) as second:
        th, fh, sh = authenticate(teacher), authenticate(first), authenticate(second)
        activity = teacher.post("/activities", headers=th, json={"group_limit": 1}).json()
        path = f"/activities/{activity['id']}"
        joining = {"code": activity["code"], "group_number": 1}
        assert first.post("/activities/join", headers=fh, json=joining).status_code == 200
        credential = first.get(path + "/state").json()["group"]["credential"]
        assert second.post("/activities/join", headers=sh,
                           json={**joining, "credential": credential}).status_code == 200
        assert first.post(path + "/leave", headers=fh).status_code == 204
        assert second.get(path + "/state").status_code == 200
        assert second.post(path + "/leave", headers=sh).status_code == 204
        assert first.get(path + "/state").status_code == 403
        assert first.get("/api/session").json()["student_activities"] == []
        assert first.post("/activities/join", headers=fh,
                          json={**joining, "credential": credential}).status_code == 403
        assert first.post("/activities/join", headers=fh, json=joining).status_code == 200
        replacement = first.get(path + "/state").json()["group"]["credential"]
        assert replacement != credential
        assert teacher.post(path + "/demo-questions", headers=th).status_code == 204
        round_id = teacher.post(path + "/rounds", headers=th, json={}).json()["id"]
        assert first.delete(path + "/groups/1", headers=fh).status_code == 403
        assert teacher.delete(path + "/groups/1", headers=th).status_code == 409
        assert teacher.get(path + "/state").json()["groups"][0]["can_delete"] is False
        assert first.get(path + "/state").json()["group"]["credential"] == replacement
        assert teacher.post(path + f"/rounds/{round_id}/close", headers=th).status_code == 200
        assert teacher.delete(path + "/groups/1", headers=th).status_code == 204
        assert first.get(path + "/state").status_code == 403
