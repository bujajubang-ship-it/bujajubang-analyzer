import asyncio
from playwright.async_api import async_playwright

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=CHROME_PATH,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--lang=ko-KR,ko",
                "--window-size=1366,768",
            ],
        )
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
            },
        )
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = {runtime: {}};
        """)
        page = await ctx.new_page()

        print("실제 Chrome으로 쿠팡 접속...")
        await page.goto(
            "https://www.coupang.com/np/search?q=업소용+가스레인지&page=1&sorter=scoreDesc",
            wait_until="domcontentloaded", timeout=30000
        )
        await page.wait_for_timeout(3000)

        title = await page.title()
        print(f"타이틀: {title}")

        count = await page.locator('li[id^="productUnit"]').count()
        print(f"productUnit 개수: {count}")

        if count > 0:
            first = page.locator('li[id^="productUnit"]').first
            html = await first.inner_html()
            print(f"\n첫 상품 HTML:\n{html[:800]}")
        else:
            body = await page.inner_text("body")
            print(f"Body: {body[:300]}")

        await browser.close()

asyncio.run(main())
