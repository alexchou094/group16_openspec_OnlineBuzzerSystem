"""不修改提交或歷史基準的整輪順位計算。"""
from collections import Counter

from app.domain.activity import CandidateResult, QuestionResult


def reception(submission):
    return submission.received_at, submission.receive_sequence


def fixed_order(round_, questions):
    numbers = {q.id: q.number for q in questions}
    return tuple(sorted(round_.question_ids, key=lambda q: (
        min((reception(s) for s in round_.submissions.values() if s.question_id == q),
            default=(float('inf'), 0)), numbers[q])))


def rank(round_, order):
    counts = Counter(round_.baseline)
    results = []
    for q in order:
        submissions = [s for s in round_.submissions.values() if s.question_id == q]
        eligible = sorted((s for s in submissions if not s.rejected), key=lambda s: (
            counts[s.group_id] if round_.fairness_enabled else 0, *reception(s)))
        winner = eligible[0] if eligible else None
        reason = ''
        if winner:
            reason = ('成功次數較少' if round_.fairness_enabled and len(eligible) > 1
                      and counts[winner.group_id] < counts[eligible[1].group_id]
                      else '提交較早（依後端接收先後）')
        candidates = tuple(CandidateResult(s.id, i + 1, counts[s.group_id])
                           for i, s in enumerate(eligible))
        candidates += tuple(CandidateResult(s.id, None, counts[s.group_id])
                            for s in sorted(submissions, key=reception) if s.rejected)
        if winner:
            counts[winner.group_id] += 1
        results.append(QuestionResult(q, winner.group_id if winner else None,
                                      candidates, reason))
    return tuple(results), tuple(sorted(counts.items()))
