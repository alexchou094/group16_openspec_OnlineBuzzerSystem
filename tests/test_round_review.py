from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.domain.activity import Blank, Question, SourceSnapshot
from app.domain.ranking import rank
from app.main import create_app
from app.repositories.memory import MemoryRepository
from app.services.activities import ActivityService, ActivityError


@pytest.fixture
def system():
    now = [100.0]
    service = ActivityService(MemoryRepository(), lambda: now[0])
    activity = service.create('teacher', 4)
    service.replace_questions(activity.id, 'teacher', SourceSnapshot('fixture', 1, '混合', 'shape'), (
        Question('q1', 1, (Blank('1', 'H5'), Blank('2', 'H6'))),
        Question('q2', 2, (Blank('1', 'G8'),)), Question('q3', 3, (Blank('1', 'G10'),))))
    for i in (1, 2):
        service.join(f's{i}', activity.code, i, None)
    return service, activity, now


def opened(system, fair=False, seconds=None):
    s, a, now = system
    r = s.open_round(a.id, 'teacher', None, fair, seconds)
    return s, a, now, r


@pytest.mark.parametrize('q,answers', [('q1', []), ('q1', ['a']), ('q1', ['a', ' ']),
    ('q1', ['a', 'b', 'c']), ('q1', [1, 'b']), ('missing', ['x'])])
def test_invalid_does_not_consume(system, q, answers):
    s, a, _, r = opened(system)
    with pytest.raises(ActivityError):
        s.submit(a.id, 's1', r, q, answers)
    assert not a.current_round.submissions
    s.submit(a.id, 's1', r, 'q2', ['second'])
    s.submit(a.id, 's1', r, 'q1', [' <script> ', 'b'])
    assert len(a.current_round.submissions) == 2


def test_same_group_concurrency_and_retry(system):
    s, a, _, r = opened(system)
    s.join('device2', a.code, 1, a.groups[1].credential)
    barrier = Barrier(2)
    def submit(session):
        barrier.wait()
        return s.submit(a.id, session, r, 'q1', [session, 'b'])
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, ['s1', 'device2']))
    assert sorted(x['already_submitted'] for x in results) == [False, True]
    assert results[0]['submission'] == results[1]['submission']
    original = next(iter(a.current_round.submissions.values()))
    assert s.submit(a.id, 's1', r, 'q1', ['changed', 'answer'])['submission'] == results[0]['submission']
    assert next(iter(a.current_round.submissions.values())) is original
    assert original.receive_sequence == 1


@pytest.mark.parametrize('offset,accepted', [(0.999, True), (1, False), (1.001, False)])
def test_exact_deadline(system, offset, accepted):
    s, a, now, r = opened(system, seconds=1)
    now[0] += offset
    if accepted:
        s.submit(a.id, 's1', r, 'q2', ['a'])
    else:
        with pytest.raises(ActivityError, match='結束'):
            s.submit(a.id, 's1', r, 'q2', ['a'])
        assert a.current_round.review_version == 1
    assert len(a.current_round.submissions) == int(accepted)


def test_close_race_and_stale_round(system):
    s, a, now, r = opened(system, seconds=1)
    now[0] += 1
    barrier = Barrier(2)
    def close(auto):
        barrier.wait()
        return s.expire_due_rounds() if auto else s.close_round(a.id, 'teacher', r)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(close, [True, False]))
    assert a.current_round.review_version == 1
    snapshot = a.current_round.results
    s.close_round(a.id, 'teacher', r)
    assert a.current_round.results is snapshot
    s.finalize(a.id, 'teacher', r, 1)
    new = s.open_round(a.id, 'teacher', None, False, None)
    with pytest.raises(ActivityError, match='失效'):
        s.submit(a.id, 's1', r, 'q2', ['old'])
    s.submit(a.id, 's1', new, 'q2', ['new'])


@pytest.mark.parametrize('fair', [False, True])
def test_ranking_review_and_finalize(system, fair):
    s, a, _, r = opened(system, fair)
    for session in ('s1', 's2'):
        s.submit(a.id, session, r, 'q1', [session, 'b'])
        s.submit(a.id, session, r, 'q2', [session])
    s.close_round(a.id, 'teacher', r)
    round_ = a.current_round
    one, two = a.groups[1].id, a.groups[2].id
    assert round_.fixed_question_order == ('q1', 'q2', 'q3')
    assert [x.winner_group_id for x in round_.results] == [one, two if fair else one, None]
    assert rank(round_, round_.fixed_question_order)[0] == round_.results
    original = round_.submissions['q1', one]
    version = s.review(a.id, 'teacher', r, original.id, True, 1)
    assert round_.fixed_question_order == ('q1', 'q2', 'q3')
    assert [x.winner_group_id for x in round_.results] == [two, one, None]
    assert round_.results[0].candidates[-1].rank is None
    assert set(round_.affected_questions) >= {'q1'}
    if fair:
        assert set(round_.affected_questions) == {'q1', 'q2'}
    assert s.review(a.id, 'teacher', r, original.id, True, version) == version
    with pytest.raises(ActivityError, match='版本'):
        s.finalize(a.id, 'teacher', r, 1)
    with pytest.raises(ActivityError):
        s.finish(a.id, 'teacher')
    version = s.review(a.id, 'teacher', r, original.id, False, version)
    assert round_.submissions['q1', one] == original
    s.finalize(a.id, 'teacher', r, version)
    s.finalize(a.id, 'teacher', r, version)
    assert a.finalized_revision == 1
    assert sum(s._success_counts(a).values()) == 2
    with pytest.raises(ActivityError):
        s.review(a.id, 'teacher', r, original.id, True, version)
    with pytest.raises(ActivityError):
        s.open_round(a.id, 'teacher', ['q1'], False, None)
    s.join('s3', a.code, 3, None)
    s.open_round(a.id, 'teacher', ['q3'], True, None)
    assert a.current_round.baseline[one] == (1 if fair else 2)
    assert a.current_round.baseline[a.groups[3].id] == 0


def test_all_wrong_nonwinner_and_next_round(system):
    s, a, _, r = opened(system, True)
    for session in ('s1', 's2'):
        s.submit(a.id, session, r, 'q1', ['a', 'b'])
    s.close_round(a.id, 'teacher', r)
    for sub in reversed(list(a.current_round.submissions.values())):
        s.review(a.id, 'teacher', r, sub.id, True, a.current_round.review_version)
    assert a.current_round.results[0].winner_group_id is None
    assert not s._success_counts(a)
    s.finalize(a.id, 'teacher', r, a.current_round.review_version)
    new = s.open_round(a.id, 'teacher', ['q1'], True, None)
    s.submit(a.id, 's1', new, 'q1', ['new', 'answer'])
    assert len(a.current_round.submissions) == 1


def test_nonopen_question_midround_group_and_deleted_empty_group(system):
    s, a, _ = system
    r = s.open_round(a.id, 'teacher', ['q1'], True, None)
    with pytest.raises(ActivityError):
        s.submit(a.id, 's1', r, 'q2', ['not open'])
    s.join('late', a.code, 3, None)
    s.submit(a.id, 'late', r, 'q1', ['a', 'b'])
    s.close_round(a.id, 'teacher', r)
    assert a.current_round.results[0].candidates[0].successes_before == 0
    s.finalize(a.id, 'teacher', r, 1)
    s.delete_group(a.id, 'teacher', 2)
    assert s.state(a.id, 's1')['history'][0]['results'][0]['winner'] == 3


def test_manual_close_before_submission(system):
    s, a, _, r = opened(system)
    s.close_round(a.id, 'teacher', r)
    with pytest.raises(ActivityError, match='結束'):
        s.submit(a.id, 's1', r, 'q2', ['late'])
    assert not a.current_round.submissions


def test_fixed_order_is_reception_not_question_number(system):
    s, a, now, r = opened(system, True)
    first = s.submit(a.id, 's1', r, 'q2', ['first'])
    now[0] += .1
    s.submit(a.id, 's2', r, 'q1', ['later', 'b'])
    s.close_round(a.id, 'teacher', r)
    s.review(a.id, 'teacher', r, first['submission']['id'], True, 1)
    assert a.current_round.fixed_question_order == ('q2', 'q1', 'q3')
    assert [x.question_id for x in a.current_round.results] == ['q2', 'q1', 'q3']
    s.finalize(a.id, 'teacher', r, 2)
    s.open_round(a.id, 'teacher', ['q2'], False, None)
    with pytest.raises(ActivityError):
        s.review(a.id, 'teacher', r, first['submission']['id'], False, 2)


def test_http_roles_versions_and_history():
    s = ActivityService(MemoryRepository(), lambda: 100)
    app = create_app(s, demo=True, hosts=('testserver',))
    clients = [TestClient(app) for _ in range(4)]
    teacher, first, second, stranger = clients
    headers = [{'Origin': 'http://testserver', 'X-CSRF-Token': c.get('/api/session').json()['csrf_token']} for c in clients]
    a = teacher.post('/activities', json={'group_limit': 2}, headers=headers[0]).json()
    path = '/activities/' + a['id']
    for i in (1, 2):
        clients[i].post('/activities/join', json={'code': a['code'], 'group_number': i}, headers=headers[i]).raise_for_status()
    teacher.post(path + '/demo-questions', headers=headers[0]).raise_for_status()
    r = teacher.post(path + '/rounds', json={}, headers=headers[0]).json()['id']
    endpoint = path + '/rounds/' + r
    body = {'question_id': 'demo-1', 'answers': ['<img src=x onerror=alert(1)>']}
    before = first.get(path + '/state').json()
    for extra in ({'group_id': 'fake'}, {'received_at': 0}, {'receive_sequence': 1}):
        assert first.post(endpoint + '/submissions', json={**body, **extra}, headers=headers[1]).status_code == 422
    assert teacher.post(endpoint + '/submissions', json=body, headers=headers[0]).status_code == 403
    assert stranger.post(endpoint + '/submissions', json=body, headers=headers[3]).status_code == 403
    response = second.post(endpoint + '/submissions', json=body, headers=headers[2])
    response.raise_for_status()
    sub = response.json()['submission']
    after_response = first.get(path + '/state')
    assert 'etag' not in after_response.headers
    assert before == after_response.json()
    assert 'receive_sequence' not in response.text
    assert second.get(path + '/state').json()['round']['submissions'] == [sub]
    for client in (first, second):
        data = client.get(path + '/state').json()
        assert 'groups' not in data
        assert 'credential' not in str(data['round'])
    assert stranger.get(path + '/state').status_code == 403
    assert first.post(endpoint + '/close', headers=headers[1]).status_code == 403
    teacher.post(endpoint + '/close', headers=headers[0]).raise_for_status()
    assert first.get(path + '/state').json()['round']['submissions'] == [sub]
    review = endpoint + '/reviews/' + sub['id']
    payload = {'rejected': True, 'expected_review_version': 1}
    assert first.patch(review, json=payload, headers=headers[1]).status_code == 403
    assert teacher.patch(review, json=payload).status_code == 403
    assert teacher.patch(review, json=payload, headers=headers[0]).status_code == 200
    assert teacher.patch(review, json=payload, headers=headers[0]).status_code == 409
    assert teacher.post(endpoint + '/finalize', json={'expected_review_version': 1}, headers=headers[0]).status_code == 409
    teacher.post(endpoint + '/finalize', json={'expected_review_version': 2}, headers=headers[0]).raise_for_status()
    teacher.post(path + '/rounds', json={}, headers=headers[0]).raise_for_status()
    state = first.get(path + '/state').json()
    assert len(state['history']) == 1 and not state['round']['submissions']
    assert 'credential' not in str(state['history'])
