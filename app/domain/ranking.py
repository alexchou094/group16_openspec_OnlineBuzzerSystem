"""不修改提交或歷史基準的整輪順位計算。"""
from collections import Counter

from app.domain.activity import CandidateResult, QuestionResult, ReviewStatus


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
    waiting = False
    for q in order:
        submissions = [s for s in round_.submissions.values() if s.question_id == q]
        if not submissions:
            results.append(QuestionResult(q, status="COMPLETE"))
            continue
        if waiting:
            results.append(QuestionResult(q, candidates=tuple(
                CandidateResult(s.id, None, None) for s in sorted(submissions, key=reception)), status="WAITING"))
            continue
        ordered = sorted(submissions, key=lambda s: (
            counts[s.group_id] if round_.fairness_enabled else 0, *reception(s)))
        winner = None
        current = None
        for submission in ordered:
            if submission.review_status == ReviewStatus.CORRECT:
                winner = submission
                break
            if submission.review_status == ReviewStatus.UNREVIEWED:
                current = submission.id
                break
        eligible = [s for s in ordered if not s.rejected]
        reason = ''
        if winner:
            reason = ('成功次數較少' if round_.fairness_enabled and len(eligible) > 1
                      and counts[winner.group_id] < counts[eligible[1].group_id]
                      else '提交較早（依後端接收先後）')
        ranks = {s.id: i + 1 for i, s in enumerate(eligible)}
        candidates = tuple(CandidateResult(s.id, ranks.get(s.id), counts[s.group_id]) for s in ordered)
        if winner:
            counts[winner.group_id] += 1
        results.append(QuestionResult(q, winner.group_id if winner else None,
                                      candidates, reason, "PENDING" if current else "COMPLETE", current))
        waiting = current is not None
    return tuple(results), tuple(sorted(counts.items()))
