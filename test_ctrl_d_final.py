import asyncio
from playwright.async_api import async_playwright
import time

async def test_ctrl_d():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1600, "height": 900})

        try:
            # Navigate to the app
            await page.goto("http://127.0.0.1:8000")
            time.sleep(2)

            print("Page loaded. Trying to select a word from the paper...")

            # Find a word in the paper
            word = await page.evaluate("""() => {
                const pre = document.querySelector("pre");
                if (pre) {
                    const text = pre.innerText;
                    const words = text.split(/\\s+/).filter(w => w.length > 3 && w.length < 12);
                    return words[10] || words[5] || words[0];
                }
                return null;
            }""")

            print(f"Found word to test: '{word}'")

            if word:
                # Select the word using JavaScript
                result = await page.evaluate(f"""(word) => {{
                    const walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_TEXT,
                        null,
                        false
                    );
                    let node;
                    while (node = walker.nextNode()) {{
                        const idx = node.textContent.indexOf(word);
                        if (idx !== -1) {{
                            const range = document.createRange();
                            range.setStart(node, idx);
                            range.setEnd(node, idx + word.length);
                            window.getSelection().removeAllRanges();
                            window.getSelection().addRange(range);
                            return true;
                        }}
                    }}
                    return false;
                }}""{word}""")

                print(f"Word selected: {result}")
                time.sleep(0.5)

                # Press Ctrl+D
                print("Pressing Ctrl+D...")
                await page.keyboard.press("Control+d")
                time.sleep(2)

                # Check if popup appeared
                popup_visible = await page.locator("#popup").is_visible()
                print(f"Popup visible: {popup_visible}")

                if popup_visible:
                    head = await page.locator("#pop-head").text_content()
                    target = await page.locator("#pop-target").text_content()
                    print(f"✓ SUCCESS! Ctrl+D works!")
                    print(f"  Popup title: {head}")
                    print(f"  Word: {target}")
                else:
                    print("✗ FAIL: Popup did not appear")

                await page.screenshot(path="final_test.png")
                print("Screenshot saved: final_test.png")
            else:
                print("Could not find word to test")

        finally:
            await browser.close()

asyncio.run(test_ctrl_d())
