import asyncio
from playwright.async_api import async_playwright
import time

async def verify():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1600, "height": 900})

        await page.goto("http://127.0.0.1:8000")
        time.sleep(2)

        # Get all text from the page
        body_text = await page.inner_text("body")
        print(f"Page has {len(body_text)} characters of text")

        # Find a simple word like "the"
        if "the" in body_text.lower():
            print("Found 'the' in page - will try to select it")

            # Use a simple approach: just manually select a span that contains "the"
            spans = await page.query_selector_all("span, b, strong, em, p")
            print(f"Found {len(spans)} text elements")

            for span in spans[:10]:
                text = await span.inner_text()
                if "the" in text.lower():
                    print(f"Found element with 'the': {text[:50]}")
                    # Select all text in this element
                    await span.click()
                    await page.keyboard.press("Control+a")
                    time.sleep(0.5)

                    selected = await page.evaluate("window.getSelection().toString()")
                    print(f"Selected text: '{selected}'")

                    if selected and len(selected) > 0:
                        print("Pressing Ctrl+D...")
                        await page.keyboard.press("Control+d")
                        time.sleep(2)

                        popup = await page.locator("#popup").is_visible()
                        print(f"Popup visible after Ctrl+D: {popup}")

                        if popup:
                            print("✓ Ctrl+D WORKS!")
                        else:
                            print("✗ Ctrl+D did not open popup")
                    break

        await browser.close()

asyncio.run(verify())
