import os
import socket
import time
from threading import Thread

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from app.main import create_app

pytestmark = pytest.mark.skipif(os.getenv("BUZZER_BROWSER_TEST") != "1", reason="需啟用瀏覽器測試")


@pytest.fixture
def server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(demo=True), log_level="warning", lifespan="on"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        end = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < end:
            time.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()


def test_teacher_and_two_groups_sync(server):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv("BUZZER_BROWSER_CHANNEL") or None)
        teacher_context, first_context, second_context = (browser.new_context() for _ in range(3))
        teacher, first, second = (context.new_page() for context in (teacher_context, first_context, second_context))
        teacher.goto(server)
        teacher.get_by_label("組數上限").fill("2")
        teacher.get_by_role("button", name="建立活動").click()
        expect(teacher.locator("#phase")).to_have_text("準備中")
        code = teacher.locator("#identity").inner_text().split()[1]
        for page, number in ((first, "1"), (second, "2")):
            page.goto(server)
            page.get_by_label("活動代碼").fill(code)
            page.get_by_label("組號", exact=True).fill(number)
            page.get_by_role("button", name="加入活動").click()
            expect(page.locator("#phase")).to_have_text("準備中")
        first.reload()
        expect(first.locator("#identity")).to_contain_text("第 1 組")
        expect(first.get_by_text("組別與加入憑證", exact=True)).to_have_count(0)
        teacher.get_by_role("button", name="刪除第 2 組").click()
        expect(second.locator("#connection")).to_contain_text("沒有此活動的操作權限")
        second.get_by_role("link", name="返回首頁").click()
        second.get_by_label("活動代碼").fill(code)
        second.get_by_label("組號", exact=True).fill("2")
        second.get_by_role("button", name="加入活動").click()
        expect(second.locator("#phase")).to_have_text("準備中")
        another_tab = teacher_context.new_page()
        another_tab.goto(teacher.url)
        expect(another_tab.get_by_role("button", name="取消活動")).to_be_visible()
        teacher.get_by_role("button", name="載入示範題目").click()
        teacher.get_by_role("button", name="開輪").click()
        for page in (teacher, first, second):
            expect(page.locator("#phase")).to_have_text("搶答中", timeout=2000)
        first_context.set_offline(True)
        expect(first.locator("#connection")).to_contain_text("同步中斷", timeout=3000)
        first_context.set_offline(False)
        expect(first.locator("#connection")).to_have_text("已同步", timeout=3000)

        with teacher.expect_response(lambda response: response.url.endswith("/close")) as closed:
            teacher.get_by_role("button", name="關輪").click()
        assert closed.value.ok
        start = time.monotonic()
        for page in (teacher, first, second):
            timeout = max(1, int((2 - (time.monotonic() - start)) * 1000))
            expect(page.locator("#phase")).to_have_text("本輪結果", timeout=timeout)
        assert time.monotonic() - start < 2
        states = [page.request.get(f"{page.url}/state").json() for page in (teacher, first, second)]
        assert [state["round"]["review_version"] for state in states] == [1, 1, 1]
        expect(teacher.get_by_role("button", name="開輪")).to_have_count(0)
        teacher.get_by_role("button", name="確認定案").click()
        teacher.get_by_label("排序模式").select_option("fair")
        teacher.get_by_label("倒數秒數（留空表示無倒數）").fill("1")
        teacher.get_by_role("button", name="開輪").click()
        expect(teacher.locator("#round")).to_contain_text("第 2 輪 · 公平模式")
        expect(teacher.locator("#phase")).to_have_text("本輪結果", timeout=3000)
        teacher.get_by_role("button", name="確認定案").click()
        teacher.get_by_role("button", name="結束活動").click()
        expect(second.locator("#phase")).to_have_text("已結束", timeout=2000)
        expect(second.get_by_text("未成功題目：第 1 題、第 2 題、第 3 題")).to_be_visible()
        browser.close()
