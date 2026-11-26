import os
import socket
import time
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect

from app.main import create_app
from app.domain.activity import Blank, Question, SourceSnapshot
from app.domain.guided_notes import parse_worksheet
from app.adapters.guided_notes import SheetWriteUncertain
from app.repositories.memory import MemoryRepository
from app.services.activities import ActivityService
from app.web import guided_notes
from test_result_export import FakeWriter, finish_review, win

pytestmark = pytest.mark.skipif(os.getenv('BUZZER_BROWSER_TEST') != '1', reason='需啟用瀏覽器測試')


def decide(page, label='確認正確'):
    control = page.get_by_role('button', name=label, exact=True)
    submission = control.locator('..').get_attribute('data-submission')
    control.click()
    expect(page.locator(f'[data-submission="{submission}"]').get_by_role('button', name=label, exact=True)).to_have_count(0)


def undo_first(page):
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='撤銷審閱').first.click()
    expect(page.get_by_role('button', name='確認正確', exact=True)).to_have_count(1)


@pytest.fixture
def server():
    service = ActivityService(MemoryRepository())
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(service), log_level='warning'))
    thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        end = time.monotonic() + 5
        while not server.started and time.monotonic() < end:
            time.sleep(.01)
        assert server.started
        yield f'http://127.0.0.1:{port}', service
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()


def test_export_page_from_open_round_and_cancelled_activity(
    server, mixed_sheet, update_cell, monkeypatch, google_settings,
):
    url, service = server
    writer = FakeWriter(mixed_sheet, update_cell)
    writer.write_error = SheetWriteUncertain("模擬寫入後回應遺失")
    monkeypatch.setattr(guided_notes, "GoogleWorksheetReader", lambda *_args: writer)

    def headers(page):
        csrf = page.request.get(url + "/api/session").json()["csrf_token"]
        return {"Origin": url, "X-CSRF-Token": csrf}

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("BUZZER_BROWSER_CHANNEL") or None)
        teacher, student = [browser.new_context().new_page() for _ in range(2)]
        teacher.goto(url)
        teacher.get_by_label("組數上限").fill("3")
        teacher.get_by_role("button", name="建立活動").click()
        expect(teacher.locator("#phase")).to_have_text("準備中")
        activity_url = teacher.url
        activity = service.repository.get(activity_url.rsplit("/", 1)[1]).activity
        source, questions = parse_worksheet(mixed_sheet)
        service.replace_questions(activity.id, activity.owner, source, questions)
        service.join("1", activity.code, 1, None)
        win(service, activity)
        second = service.open_round(activity.id, activity.owner, ["q3"], False, None)
        service.submit(activity.id, "1", second, "q3", ["尚未定案的內容"])

        student_headers = headers(student)
        student.request.post(url + "/activities/join", headers=student_headers,
                             data={"code": activity.code, "group_number": 2})
        student.goto(activity_url)
        expect(student.locator("#phase")).to_have_text("搶答中")
        expect(student.get_by_role("link", name="匯出定案成果")).to_have_count(0)
        base = activity_url + "/exports"
        assert student.request.get(base).status == 403
        assert student.request.get(base + "/results.csv").status == 403
        for suffix, body in [("preview", {}), ("write", {"preview_id": "invalid"}), ("oauth", {})]:
            assert student.request.post(base + "/" + suffix, headers=student_headers, data=body).status == 403

        teacher.get_by_role("link", name="匯出定案成果").click()
        expect(teacher.get_by_role("heading", name="成果匯出")).to_be_visible()
        expect(teacher.locator("tbody tr")).to_have_count(2)
        expect(teacher.get_by_text("尚未定案的內容")).to_have_count(0)
        with teacher.expect_download() as download:
            teacher.get_by_role("link", name="下載 CSV").click()
        csv_text = download.value.path().read_text(encoding="utf-8-sig")
        assert "正確" in csv_text and "尚未定案的內容" not in csv_text

        teacher_headers = headers(teacher)
        assert teacher.request.post(base + "/write", data={"preview_id": "invalid"}).status == 403
        oauth = teacher.request.post(base + "/oauth", headers=teacher_headers).json()
        state = parse_qs(urlsplit(oauth["url"]).query)["state"][0]
        callback = teacher.request.get(url + "/google/oauth/callback", params={"state": state, "code": "test-code"})
        assert callback.ok and base.removeprefix(url) in callback.text()

        teacher.get_by_role("button", name="確認覆寫").click()
        progress = teacher.locator("#export-progress")
        expect(progress).to_contain_text("待確認")
        expect(teacher.get_by_role("button", name="重試此次寫回")).to_be_visible()
        teacher.reload()
        expect(progress).to_contain_text("待確認")
        teacher.get_by_role("button", name="讀回核對").click()
        expect(progress).to_contain_text("成功")
        assert len(writer.writes) == 1

        submission = activity.current_round.submissions["q3", activity.groups[1].id]
        finish_review(service, activity, second, [(submission.id, "CORRECT")])
        service.finish(activity.id, activity.owner)
        teacher.reload()
        expect(teacher.locator("tbody tr")).to_have_count(3)

        cancelled = service.create(activity.owner, 1)
        service.cancel(cancelled.id, activity.owner)
        cancelled_base = f"{url}/activities/{cancelled.id}/exports"
        assert teacher.request.get(cancelled_base).status == 409
        assert teacher.request.get(cancelled_base + "/results.csv").status == 409
        for suffix, body in [("preview", {}), ("write", {"preview_id": "invalid"})]:
            assert teacher.request.post(cancelled_base + "/" + suffix, headers=teacher_headers, data=body).status == 409
        browser.close()


def test_four_contexts_review_recovery_and_isolation(server, tmp_path):
    url, service = server
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv('BUZZER_BROWSER_CHANNEL') or None)
        contexts = [browser.new_context() for _ in range(4)]
        teacher, first, second, device = [c.new_page() for c in contexts]
        def expose_sync(route):
            response = route.fetch()
            route.fulfill(response=response, body=response.text() + '\nwindow.testSync = sync;')
        first.route('**/static/activity.js*', expose_sync)
        teacher.goto(url)
        teacher.get_by_label('組數上限').fill('3')
        teacher.get_by_role('button', name='建立活動').click()
        expect(teacher.locator('#phase')).to_have_text('準備中')
        activity_id = teacher.url.rsplit('/', 1)[1]
        a = service.repository.get(activity_id).activity
        # 測試 fixture 使用正式資料模型與匯入交接服務；正式 app 沒有 fixture 路由。
        service.replace_questions(a.id, a.owner, SourceSnapshot('fixture', 1, '混合', 'shape'), (
            Question('q1', 1, (Blank('1', 'H5'), Blank('2', 'H6'))),
            Question('q2', 2, (Blank('1', 'G8'),)), Question('q3', 3, (Blank('1', 'G10'),))))
        for page, number in ((first, '1'), (second, '2'), (device, '1')):
            page.goto(url)
            page.get_by_label('活動代碼').fill(a.code)
            page.get_by_label('組號', exact=True).fill(number)
            if page is device:
                page.get_by_label('加入憑證').fill(a.groups[1].credential)
            page.get_by_role('button', name='加入活動').click()
            expect(page.locator('#identity')).to_contain_text(f'第 {number} 組')
        teacher.get_by_label('排序模式').select_option('fair')
        teacher.get_by_role('button', name='開輪', exact=True).click()
        for page in (first, second, device):
            expect(page.locator('#phase')).to_have_text('搶答中', timeout=2000)
        expect(first.locator('#timer')).to_contain_text('由老師結束')
        expect(first.locator('#identity')).to_contain_text('成功 0 題')
        old_state = first.request.get(first.url + '/state').json()
        # 同組雙裝置啟動請求，服務鎖決定唯一有效答案。
        for page in (first, device):
            page.evaluate("""() => { window.sent = (async () => {
              const session = await (await fetch('/api/session')).json();
              const state = await (await fetch(location.pathname + '/state')).json();
              return (await fetch(location.pathname + `/rounds/${state.round.id}/submissions`, {
                method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},
                body:JSON.stringify({question_id:'q1', answers:['<img src=x onerror=window.hacked=true>', 'B']})
              })).json(); })(); }""")
        outcomes = [page.evaluate('() => window.sent') for page in (first, device)]
        assert sorted(x['already_submitted'] for x in outcomes) == [False, True]
        assert outcomes[0]['submission'] == outcomes[1]['submission']
        expect(first.locator('#submissions')).to_contain_text('本組已送出', timeout=2000)
        expect(device.locator('#submissions')).to_contain_text('本組已送出', timeout=2000)
        # 先填另一題，確認自己的題一同步不清除輸入。
        first.get_by_label('第 2 題・空格 1').fill('甲二')
        first.get_by_role('button', name='送出第 2 題').click()
        expect(teacher.locator('#submissions')).to_contain_text('甲二', timeout=2000)
        expect(teacher.locator('#submissions')).to_contain_text('提交先後 1')
        assert '暫估' not in teacher.locator('#submissions').inner_text()
        assert teacher.request.get(teacher.url + '/state').json()['round']['results'] == []
        assert '甲二' not in second.request.get(second.url + '/state').text()
        assert '甲二' not in second.locator('#submissions').inner_text()
        for label, answer in [('第 1 題・空格 1', '乙一'), ('第 1 題・空格 2', '乙子格')]:
            second.get_by_label(label).fill(answer)
        second.get_by_role('button', name='送出第 1 題').click()
        expect(second.get_by_role('button', name='送出第 1 題')).to_have_count(0)
        second.get_by_label('第 2 題・空格 1').fill('乙二')
        # 後端成功、回應遺失：UI 必須提示待確認，輪詢恢復本組答案。
        def lose_response(route):
            route.fetch()
            route.abort('failed')
        second.route('**/submissions', lose_response)
        second.get_by_role('button', name='送出第 2 題').click()
        expect(second.locator('#notice')).to_contain_text('結果待確認')
        expect(second.locator('#submissions')).to_contain_text('乙二', timeout=2000)
        second.unroute('**/submissions', lose_response)
        contexts[1].set_offline(True)
        expect(first.locator('#connection')).to_contain_text('同步中斷', timeout=4000)
        contexts[1].set_offline(False)
        expect(first.locator('#connection')).to_have_text('已同步', timeout=4000)
        held = []
        def delay_old(route):
            if not held:
                held.append([route, None])
                held[0][1] = route.fetch().json()
                first.evaluate('window.oldHeld = true')
            else:
                route.continue_()
        first.route('**/state', delay_old)
        with first.expect_request(lambda req: req.url.endswith('/state')):
            first.evaluate('() => { window.delayed = window.testSync(); }')
        # 派送攔截 callback，保留較早 OPEN 回應直到新結果已呈現。
        first.wait_for_function('window.oldHeld === true')
        assert held
        with teacher.expect_response(lambda r: r.url.endswith('/close')):
            teacher.get_by_role('button', name='關輪', exact=True).click()
        start = time.monotonic()
        first.evaluate('() => window.testSync()')
        for page in (teacher, first, second):
            expect(page.locator('#phase')).to_have_text('本輪結果', timeout=max(1, int((2 - (time.monotonic()-start))*1000)))
        elapsed = time.monotonic() - start
        assert elapsed < 2
        held[0][0].fulfill(json=held[0][1])
        first.evaluate('() => window.delayed')
        expect(first.locator('#phase')).to_have_text('本輪結果')
        first.unroute('**/state', delay_old)
        expect(first.get_by_text('第 1 題 · 待審閱', exact=False)).to_be_visible()
        expect(first.get_by_text('第 2 題 · 等待前題審閱', exact=False)).to_be_visible()
        assert teacher.get_by_role('button', name='確認定案').count() == 0
        decide(teacher)
        decide(teacher)
        expect(first.get_by_text('第 1 題 · 第 1 組勝出', exact=False)).to_be_visible()
        expect(first.get_by_text('第 2 題 · 第 2 組勝出', exact=False)).to_be_visible()
        assert teacher.locator('#panels img').count() == 0
        assert teacher.evaluate('window.hacked') is None
        assert first.get_by_role('button', name='判錯', exact=True).count() == 0
        assert teacher.get_by_role('button', name='開輪', exact=True).count() == 0
        assert teacher.get_by_role('button', name='結束活動').count() == 0
        # 比最新結果更晚抵達的舊版本，不得退回 OPEN。
        first.route('**/state', lambda route: route.fulfill(json=old_state))
        with first.expect_response(lambda response: response.url.endswith('/state')):
            pass
        expect(first.locator('#phase')).to_have_text('本輪結果')
        first.unroute('**/state')
        undo_first(teacher)
        for page in (teacher, first, second):
            expect(page.get_by_text('第 2 題 · 等待前題審閱', exact=False)).to_be_visible(timeout=2000)
            expect(page.get_by_text('結果更新：第 1、2 題')).to_be_visible()
        decide(teacher, '判錯')
        decide(teacher)
        expect(first.get_by_text('第 2 題 · 待審閱', exact=False)).to_be_visible(timeout=2000)
        decide(teacher)
        for page in (teacher, first, second):
            expect(page.get_by_text('第 1 題 · 第 2 組勝出', exact=False)).to_be_visible(timeout=2000)
            expect(page.get_by_text('第 2 題 · 第 1 組勝出', exact=False)).to_be_visible(timeout=2000)
        undo_first(teacher)
        decide(teacher)
        decide(teacher)
        expect(first.get_by_text('第 2 題 · 第 2 組勝出', exact=False)).to_be_visible(timeout=2000)
        # 題一兩候選都判錯，定案後只可重開題一及無人回答的題三。
        undo_first(teacher)
        decide(teacher, '判錯')
        decide(teacher, '判錯')
        decide(teacher)
        expect(first.get_by_text('第 1 題 · 未成功', exact=False)).to_be_visible(timeout=2000)
        teacher.get_by_role('button', name='確認定案').click()
        expect(teacher.get_by_label('第 1 題', exact=True)).to_be_visible()
        assert teacher.get_by_label('第 2 題', exact=True).count() == 0
        assert teacher.get_by_role('button', name='撤銷審閱').count() == 0
        teacher.get_by_role('button', name='開輪', exact=True).click()
        expect(first.get_by_role('button', name='送出第 1 題')).to_be_visible(timeout=2000)
        assert second.request.get(second.url + '/state').json()['round']['submissions'] == []
        assert len(second.request.get(second.url + '/state').json()['history']) == 1
        teacher.get_by_role('button', name='關輪', exact=True).click()
        teacher.get_by_role('button', name='確認定案').click()
        teacher.get_by_role('button', name='結束活動').click()
        expect(second.locator('#phase')).to_have_text('已結束', timeout=2000)
        teacher.locator('#panels > section').last.screenshot(path=str(tmp_path / 'teacher-results.png'))
        print(f'四 context 完整流程；關輪同步 {elapsed:.3f}s；截圖 {tmp_path / "teacher-results.png"}')
        browser.close()


@pytest.mark.parametrize('fair,winners', [(False, [1, 1]), (True, [1, 2])])
def test_browser_speed_fair_difference(server, fair, winners):
    url, service = server
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv('BUZZER_BROWSER_CHANNEL') or None)
        pages = [browser.new_context().new_page() for _ in range(3)]
        teacher, first, second = pages
        teacher.goto(url)
        teacher.get_by_label('組數上限').fill('2')
        teacher.get_by_role('button', name='建立活動').click()
        expect(teacher.locator('#phase')).to_have_text('準備中')
        a = service.repository.get(teacher.url.rsplit('/', 1)[1]).activity
        service.replace_questions(a.id, a.owner, SourceSnapshot('fixture', 1, '單題', 'shape'), tuple(
            Question(f'q{n}', n, (Blank('1', f'G{n}'),)) for n in (1, 2)))
        for page, number in ((first, '1'), (second, '2')):
            page.goto(url)
            page.get_by_label('活動代碼').fill(a.code)
            page.get_by_label('組號', exact=True).fill(number)
            page.get_by_role('button', name='加入活動').click()
            expect(page.locator('#phase')).to_have_text('準備中')
        teacher.get_by_label('排序模式').select_option('fair' if fair else 'speed')
        teacher.get_by_role('button', name='開輪', exact=True).click()
        for page in (first, second):
            for q in (1, 2):
                page.get_by_label(f'第 {q} 題・空格 1').fill(f'答案{q}')
                page.get_by_role('button', name=f'送出第 {q} 題').click()
                expect(page.get_by_role('button', name=f'送出第 {q} 題')).to_have_count(0)
        teacher.get_by_role('button', name='關輪', exact=True).click()
        decide(teacher)
        decide(teacher)
        for page in pages:
            for q, winner in enumerate(winners, 1):
                expect(page.get_by_text(f'第 {q} 題 · 第 {winner} 組勝出', exact=False)).to_be_visible(timeout=2000)
        browser.close()


def test_unicode_original_text_and_four_group_review(server):
    url, service = server
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv('BUZZER_BROWSER_CHANNEL') or None)
        pages = [browser.new_context().new_page() for _ in range(5)]
        teacher, *students = pages
        teacher.goto(url)
        teacher.get_by_label('組數上限').fill('4')
        teacher.get_by_role('button', name='建立活動').click()
        expect(teacher.locator('#phase')).to_have_text('準備中')
        a = service.repository.get(teacher.url.rsplit('/', 1)[1]).activity
        service.replace_questions(a.id, a.owner, SourceSnapshot('fixture', 1, '原文', 'shape'), (
            Question('q1', 1, (Blank('1', 'G5'),)),))
        for number, page in enumerate(students, 1):
            page.goto(url)
            page.get_by_label('活動代碼').fill(a.code)
            page.get_by_label('組號', exact=True).fill(str(number))
            page.get_by_role('button', name='加入活動').click()
            expect(page.locator('#identity')).to_contain_text(f'第 {number} 組')
        teacher.get_by_role('button', name='開輪', exact=True).click()
        original = ' \n' + '😀' * 496 + '\n '
        for number, page in enumerate(students, 1):
            textarea = page.get_by_label('第 1 題・空格 1')
            if number == 1:
                textarea.fill(original + 'a')
                expect(page.get_by_text('501 / 500 字（空白與換行也計入）')).to_be_visible()
                assert textarea.evaluate('el => el.validity.customError')
                page.get_by_role('button', name='送出第 1 題').click()
                assert not a.current_round.submissions
                textarea.fill(original)
                expect(page.get_by_text('500 / 500 字（空白與換行也計入）')).to_be_visible()
                assert not textarea.evaluate('el => el.validity.customError')
            else:
                textarea.fill(f'第{number}組答案')
            page.get_by_role('button', name='送出第 1 題').click()
            expect(page.get_by_role('button', name='送出第 1 題')).to_have_count(0)
        expect(teacher.locator('#submissions .answer-text')).to_have_count(4, timeout=2000)
        answer_node = teacher.locator('#submissions .answer-text').first
        assert answer_node.text_content() == original
        assert answer_node.evaluate('el => getComputedStyle(el).whiteSpace') == 'pre-wrap'
        teacher.get_by_role('button', name='關輪', exact=True).click()
        decide(teacher, '判錯')
        decide(teacher, '判錯')
        decide(teacher)
        tail_id = a.current_round.submissions['q1', a.groups[4].id].id
        for page in pages:
            expect(page.get_by_text('第 1 題 · 第 3 組勝出', exact=False)).to_be_visible(timeout=2000)
            tail = page.locator(f'[data-submission="{tail_id}"]')
            expect(tail).to_contain_text('搶答失敗')
            assert '判錯' not in tail.inner_text() and '正確' not in tail.inner_text()
            assert tail.get_by_role('button').count() == 0
        assert a.current_round.submissions['q1', a.groups[4].id].review_status == 'UNREVIEWED'
        # 撤銷前提示範圍，取消不變更結果；確認後回到第一候選。
        messages = []
        def cancel_dialog(dialog):
            messages.append(dialog.message)
            dialog.dismiss()
        teacher.once('dialog', cancel_dialog)
        teacher.get_by_role('button', name='撤銷審閱').first.click()
        assert '第 1 題' in messages[0] and '重新審閱' in messages[0]
        assert a.current_round.results[0].winner_group_id == a.groups[3].id
        undo_first(teacher)
        for page in students:
            expect(page.get_by_text('第 1 題 · 待審閱', exact=False)).to_be_visible(timeout=2000)
        decide(teacher)
        teacher.get_by_role('button', name='確認定案').click()
        for page in pages:
            expect(page.get_by_text('第 1 輪 · 已定案', exact=True)).to_be_visible(timeout=2000)
        expect(students[0].locator('#identity')).to_contain_text('成功 1 題')
        assert teacher.get_by_role('button', name='撤銷審閱').count() == 0
        assert a.current_round.submissions['q1', a.groups[1].id].answers == (original,)
        browser.close()
