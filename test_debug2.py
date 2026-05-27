import asyncio
from playwright.async_api import async_playwright
import time

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1600, "height": 900})

        page.on("console", lambda msg: print(f"[CONSOLE] {msg.type.upper()}: {msg.text}"))

        await page.goto("http://127.0.0.1:8000")
        time.sleep(3)

        print("\n=== Test 1: Select text from a pre tag ===\n")

        # Look for text in pre tags (where the paper content is)
        await page.evaluate("""() => {
            const pre = document.querySelector("pre");
            if (pre) {
                console.log("Found pre tag, text length:", pre.textContent.length);
                // Get the first 100 characters
                const text = pre.textContent.trim().substring(0, 100);
                console.log("Pre text sample:", text.substring(0, 50));

                // Try to select just the first word
                const firstWord = text.split(/\\s+/)[0];
                console.log("First word:", firstWord);

                // Use a better selection method
                const range = document.createRange();
                const sel = window.getSelection();

                // Find this word in the pre element
                const walker = document.createTreeWalker(
                    pre,
                    NodeFilter.SHOW_TEXT,
                    null
                );

                let node;
                while (node = walker.nextNode()) {
                    const idx = node.textContent.indexOf(firstWord);
                    if (idx !== -1) {
                        console.log("Found word in text node");
                        range.setStart(node, idx);
                        range.setEnd(node, idx + firstWord.length);
                        sel.removeAllRanges();
                        sel.addRange(range);
                        console.log("Selected:", sel.toString());
                        return;
                    }
                }
            }
        }""")

        time.sleep(1)

        print("\n=== Pressing Ctrl+D ===\n")
        await page.keyboard.press("Control+d")
        time.sleep(2)

        popup_visible = await page.locator("#popup").is_visible()
        print(f"\n✓ Popup visible: {popup_visible}")

        if popup_visible:
            head = await page.locator("#pop-head").text_content()
            target = await page.locator("#pop-target").text_content()
            print(f"✓ SUCCESS! Popup shows: '{head}' for word '{target}'")

        await browser.close()

asyncio.run(test())
