import asyncio
from playwright.async_api import async_playwright
import time

async def test_ctrl_d_wiki():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1600, "height": 900})

        # Navigate to the app
        await page.goto("http://127.0.0.1:8000")
        time.sleep(3)

        # Check if page loaded
        title = await page.title()
        print(f"Page title: {title}")

        # Take initial screenshot
        await page.screenshot(path="initial_page.png")
        print("Screenshot taken: initial_page.png")

        # Press Ctrl+6 to open the wiki/graph panel
        print("Opening wiki/graph panel with Ctrl+6...")
        await page.keyboard.press("Control+6")
        time.sleep(2)

        # Take screenshot after opening wiki panel
        await page.screenshot(path="after_ctrl6_wiki.png")
        print("Screenshot taken: after_ctrl6_wiki.png")

        # Check if wiki panel is visible
        wiki_panel = page.locator("#wiki")
        is_visible = await wiki_panel.is_visible()
        print(f"Wiki panel visible: {is_visible}")

        if is_visible:
            # Try to select text from a wiki page or somewhere
            # First, let's try to select some text from the whole page
            all_text = await page.evaluate("document.body.innerText")
            print(f"Page text length: {len(all_text)}")

            if "Attention" in all_text or "transformer" in all_text.lower():
                print("Found paper content on page")

            # Try to select a word from the paper content area
            try:
                # Select text from the first paragraph in the main content
                word = await page.evaluate("""() => {
                    const el = document.querySelector("pre");
                    if (el) {
                        const text = el.textContent;
                        if (text && text.length > 10) {
                            const words = text.split(/\\s+/);
                            return words.find(w => w.length > 3 && w.length < 15) || words[0];
                        }
                    }
                    return null;
                }""")

                if word:
                    print(f"Found word to test: '{word}'")
                    # Select the word using JavaScript
                    await page.evaluate(f"""(word) => {{
                        const range = document.createRange();
                        const sel = window.getSelection();
                        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                        let node;
                        while ((node = walker.nextNode())) {{
                            if (node.textContent.includes(word)) {{
                                const idx = node.textContent.indexOf(word);
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
                    # Take screenshot with selection
                    await page.screenshot(path="selected_text_wiki.png")
                    print("Screenshot taken with selection: selected_text_wiki.png")

                    # Press Ctrl+D
                    print("Pressing Ctrl+D...")
                    await page.keyboard.press("Control+d")
                    time.sleep(1.5)

                    # Take screenshot after Ctrl+D
                    await page.screenshot(path="after_ctrl_d_wiki.png")
                    print("Screenshot taken: after_ctrl_d_wiki.png")

                    # Check if popup appeared
                    popup_display = await page.locator("#popup").evaluate("el => el.style.display")
                    print(f"Popup display: {popup_display}")

                    if popup_display == "block":
                        popup_head = await page.locator("#pop-head").text_content()
                        popup_text = await page.locator("#pop-target").text_content()
                        print(f"✅ SUCCESS: Definition popup appeared!")
                        print(f"  Label: {popup_head}")
                        print(f"  Word: {popup_text}")
                    else:
                        print("❌ FAIL: Popup did not appear after Ctrl+D")
            except Exception as e:
                print(f"Error during test: {e}")
        else:
            print("⚠️  Wiki panel is not visible")

        await browser.close()

asyncio.run(test_ctrl_d_wiki())
