import os
import socket
import time
from threading import Thread

import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect

from app.main import create_app
from app.domain.activity import Blank, Question, SourceSnapshot
from app.repositories.memory import MemoryRepository
from app.services.activities import ActivityService

pytestmark = pytest.mark.skipif(os.getenv('BUZZER_BROWSER_TEST') != '1', reason='需啟用瀏覽器測試')


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
        teacher.get_by_role('button', name='判錯', exact=True).first.click()
        for page in (teacher, first, second):
            expect(page.get_by_text('第 1 題 · 第 2 組勝出', exact=False)).to_be_visible(timeout=2000)
            expect(page.get_by_text('第 2 題 · 第 1 組勝出', exact=False)).to_be_visible(timeout=2000)
            expect(page.get_by_text('結果更新：第 1、2 題')).to_be_visible()
        teacher.get_by_role('button', name='撤銷判錯').click()
        expect(first.get_by_text('第 2 題 · 第 2 組勝出', exact=False)).to_be_visible(timeout=2000)
        # 題一兩候選都判錯，定案後只可重開題一及無人回答的題三。
        teacher.get_by_role('button', name='判錯', exact=True).first.click()
        teacher.get_by_role('button', name='判錯', exact=True).first.click()
        expect(first.get_by_text('第 1 題 · 未成功', exact=False)).to_be_visible(timeout=2000)
        teacher.get_by_role('button', name='確認定案').click()
        expect(teacher.get_by_label('第 1 題', exact=True)).to_be_visible()
        assert teacher.get_by_label('第 2 題', exact=True).count() == 0
        assert teacher.get_by_role('button', name='撤銷判錯').count() == 0
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
        for page in pages:
            for q, winner in enumerate(winners, 1):
                expect(page.get_by_text(f'第 {q} 題 · 第 {winner} 組勝出', exact=False)).to_be_visible(timeout=2000)
        browser.close()
