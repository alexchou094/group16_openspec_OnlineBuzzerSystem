from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from copy import deepcopy

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


def judge(s, a, status, submission_id=None):
    r = a.current_round
    if submission_id is None:
        submission_id = next(result.current_submission_id for result in r.results if result.current_submission_id)
    return s.review(a.id, 'teacher', r.id, submission_id, status, r.review_version)


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
    assert [x.winner_group_id for x in round_.results] == [None, None, None]
    assert round_.results[1].status == 'WAITING'
    assert all(c.rank is None and c.successes_before is None for c in round_.results[1].candidates)
    with pytest.raises(ActivityError, match='審閱'):
        s.finalize(a.id, 'teacher', r, 1)
    original_state = deepcopy(round_)
    assert rank(round_, round_.fixed_question_order)[0] == round_.results
    assert round_ == original_state
    original = round_.submissions['q1', one]
    with pytest.raises(ActivityError, match='當前候選'):
        judge(s, a, 'CORRECT', round_.submissions['q2', one].id)
    judge(s, a, 'CORRECT')
    judge(s, a, 'CORRECT')
    assert [x.winner_group_id for x in round_.results] == [one, two if fair else one, None]
    version = judge(s, a, 'UNREVIEWED', original.id)
    assert round_.submissions['q1', one] == original
    assert [x.winner_group_id for x in round_.results] == [None, None, None]
    assert all(sub.review_status == 'UNREVIEWED' for sub in round_.submissions.values())
    assert set(round_.affected_questions) == {'q1', 'q2'}
    judge(s, a, 'INCORRECT')
    assert round_.results[0].candidates[0].rank is None
    judge(s, a, 'CORRECT')
    assert round_.results[1].winner_group_id is None
    judge(s, a, 'CORRECT')
    assert round_.fixed_question_order == ('q1', 'q2', 'q3')
    assert [x.winner_group_id for x in round_.results] == [two, one, None]
    with pytest.raises(ActivityError, match='版本'):
        s.finalize(a.id, 'teacher', r, 1)
    with pytest.raises(ActivityError):
        s.finish(a.id, 'teacher')
    version = judge(s, a, 'UNREVIEWED', original.id)
    assert round_.submissions['q1', one] == original
    judge(s, a, 'CORRECT')
    version = judge(s, a, 'CORRECT')
    s.finalize(a.id, 'teacher', r, version)
    s.finalize(a.id, 'teacher', r, version)
    assert a.finalized_revision == 1
    assert sum(s._success_counts(a).values()) == 2
    with pytest.raises(ActivityError):
        s.review(a.id, 'teacher', r, original.id, 'UNREVIEWED', version)
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
    for sub in list(a.current_round.submissions.values()):
        judge(s, a, 'INCORRECT', sub.id)
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
    judge(s, a, 'CORRECT')
    s.finalize(a.id, 'teacher', r, 2)
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
    judge(s, a, 'INCORRECT', first['submission']['id'])
    assert a.current_round.fixed_question_order == ('q2', 'q1', 'q3')
    assert [x.question_id for x in a.current_round.results] == ['q2', 'q1', 'q3']
    judge(s, a, 'CORRECT')
    s.finalize(a.id, 'teacher', r, 3)
    s.open_round(a.id, 'teacher', ['q2'], False, None)
    with pytest.raises(ActivityError):
        s.review(a.id, 'teacher', r, first['submission']['id'], 'UNREVIEWED', 3)


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
    assert teacher.get(path + '/state').json()['round']['results'] == []
    assert first.get(path + '/state').json()['group']['successes'] == 0
    for value in ('😀' * 501, ' \n\t'):
        assert first.post(endpoint + '/submissions', json={**body, 'answers': [value]}, headers=headers[1]).status_code == 422
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
    assert teacher.post(endpoint + '/finalize', json={'expected_review_version': 1}, headers=headers[0]).status_code == 409
    review = endpoint + '/reviews/' + sub['id']
    payload = {'review_status': 'INCORRECT', 'expected_review_version': 1}
    assert teacher.patch(review, json={'rejected': True, 'expected_review_version': 1}, headers=headers[0]).status_code == 422
    assert teacher.patch(review, json={**payload, 'review_status': 'INVALID'}, headers=headers[0]).status_code == 422
    assert stranger.patch(review, json=payload, headers=headers[3]).status_code == 403
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


@pytest.mark.parametrize('character', ['中', '𠮷', '😀'])
def test_unicode_limit_and_original_text(system, character):
    s, a, _, r = opened(system)
    original = ' \n' + character * 496 + '\t '
    assert len(original) == 500
    with pytest.raises(ActivityError, match='第 2 格超過 500'):
        s.submit(a.id, 's1', r, 'q1', ['valid', original + ' '])
    with pytest.raises(ActivityError, match='第 2 格未作答'):
        s.submit(a.id, 's1', r, 'q1', [original, '\t\r\n '])
    assert not a.current_round.submissions
    result = s.submit(a.id, 's1', r, 'q1', [original, ' \n尾\n '])
    assert result['submission']['answers'] == [original, ' \n尾\n ']
    s.close_round(a.id, 'teacher', r)
    judge(s, a, 'CORRECT')
    s.finalize(a.id, 'teacher', r, 2)
    assert s.state(a.id, 's2')['history'][0]['submissions'][0]['answers'] == [original, ' \n尾\n ']


@pytest.mark.parametrize('fair', [False, True])
def test_two_wrong_third_correct_tail_and_undo(system, fair):
    s, a, _, r = opened(system, fair)
    for i in (3, 4):
        s.join(f's{i}', a.code, i, None)
    for i in range(1, 5):
        s.submit(a.id, f's{i}', r, 'q1', [f'答案{i}', '子格'])
        s.submit(a.id, f's{i}', r, 'q2', [f'答案{i}'])
    s.close_round(a.id, 'teacher', r)
    round_ = a.current_round
    raw = {k: (sub.answers, sub.received_at, sub.receive_sequence) for k, sub in round_.submissions.items()}
    ids = [round_.submissions['q1', a.groups[i].id].id for i in range(1, 5)]
    with pytest.raises(ActivityError):
        judge(s, a, 'CORRECT', ids[2])
    judge(s, a, 'INCORRECT')
    judge(s, a, 'INCORRECT')
    judge(s, a, 'CORRECT')
    assert [round_.submissions['q1', a.groups[i].id].review_status for i in range(1, 5)] == [
        'INCORRECT', 'INCORRECT', 'CORRECT', 'UNREVIEWED']
    assert [c.rank for c in round_.results[0].candidates] == [None, None, 1, 2]
    with pytest.raises(ActivityError):
        judge(s, a, 'INCORRECT', ids[3])
    judge(s, a, 'CORRECT')
    assert sum(dict(round_.cumulative_counts).values()) == 2
    # 撤銷第二個錯誤保留第一筆判錯，後面與後題所有判定都失效。
    judge(s, a, 'UNREVIEWED', ids[1])
    assert round_.submissions['q1', a.groups[1].id].rejected
    assert round_.results[0].current_submission_id == ids[1]
    assert round_.results[1].status == 'WAITING'
    assert all(sub.review_status == 'UNREVIEWED' for sub in round_.submissions.values()
               if sub.id != ids[0])
    assert sum(dict(round_.cumulative_counts).values()) == 0
    for _ in range(3):
        assert rank(round_, round_.fixed_question_order) == (round_.results, round_.cumulative_counts)
    judge(s, a, 'CORRECT')
    judge(s, a, 'CORRECT')
    assert raw == {k: (sub.answers, sub.received_at, sub.receive_sequence) for k, sub in round_.submissions.items()}
    version = round_.review_version
    s.finalize(a.id, 'teacher', r, version)
    s.finalize(a.id, 'teacher', r, version)
    assert a.finalized_revision == 1 and sum(s._success_counts(a).values()) == 2
    assert round_.submissions['q1', a.groups[3].id].review_status == 'UNREVIEWED'


def test_competing_reviews_and_finalize(system):
    s, a, _, r = opened(system)
    sub = s.submit(a.id, 's1', r, 'q1', ['a', 'b'])['submission']
    s.close_round(a.id, 'teacher', r)
    barrier = Barrier(2)
    def review(status):
        barrier.wait()
        try:
            return s.review(a.id, 'teacher', r, sub['id'], status, 1)
        except ActivityError as error:
            return error.status
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(review, ['CORRECT', 'INCORRECT'])) == [2, 409]
    barrier = Barrier(2)
    def finalize(_):
        barrier.wait()
        s.finalize(a.id, 'teacher', r, 2)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(finalize, range(2)))
    assert a.finalized_revision == 1
