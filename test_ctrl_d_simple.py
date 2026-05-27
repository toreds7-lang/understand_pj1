import asyncio
from playwright.async_api import async_playwright
import time

async def test_ctrl_d_simple():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1600, "height": 900})

        # Navigate to the app
        await page.goto("http://127.0.0.1:8000")
        time.sleep(3)

        # Take initial screenshot
        await page.screenshot(path="initial_test.png")
        print("Screenshot taken: initial_test.png")

        # Find and select a word from the document
        # Use JavaScript to find a word in the <pre> tags of the paper
        word = await page.evaluate("""() => {
            const el = document.querySelector("pre");
            if (el) {
                const text = el.innerText || el.textContent;
                if (text && text.length > 50) {
                    // Get the 5th word
                    const words = text.split(/\\s+/).filter(w => w.length > 3);
                    return words[5] || words[0];
                }
            }
            return null;
        }""")

        print(f"Found word: {word}")

        if word:
            # Select the word
            await page.evaluate(f"""(word) => {{
                const range = document.createRange();
                const sel = window.getSelection();
                const walker = document.createTreeWalker(
                    document.querySelector("pre") || document.body,
                    NodeFilter.SHOW_TEXT
                );
                let node;
                while ((node = walker.nextNode())) {{
                    const idx = node.textContent.indexOf(word);
                    if (idx !== -1) {{
                        range.setStart(node, idx);
                        range.setEnd(node, idx + word.length);
                        sel.removeAllRanges();
                        sel.addRange(range);
                        return true;
                    }}
                }}
                return false;
            }}""{word}""")

            time.sleep(0.5)
            await page.screenshot(path="text_selected.png")
            print("Screenshot: text_selected.png - word is selected")

            # Now press Ctrl+D
            print("Pressing Ctrl+D...")
            await page.keyboard.press("Control+d")
            time.sleep(2)

            # Take screenshot
            await page.screenshot(path="after_ctrl_d.png")
            print("Screenshot: after_ctrl_d.png")

            # Check if popup appeared
            popup = page.locator("#popup")
            is_visible = await popup.is_visible()
            print(f"Popup visible: {is_visible}")

            if is_visible:
                popup_head = await page.locator("#pop-head").text_content()
                popup_target = await page.locator("#pop-target").text_content()
                popup_body = await page.locator("#pop-body").text_content()

                print(f"SUCCESS! Definition popup appeared:")
                print(f"  Title: {popup_head}")
                print(f"  Word: {popup_target}")
                print(f"  Content preview: {popup_body[:100] if popup_body else 'Loading...'}")
            else:
                print("FAIL: Popup did not appear")
        else:
            print("Could not find a word to test")

        await browser.close()

asyncio.run(test_ctrl_d_simple())
