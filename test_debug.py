import asyncio
from playwright.async_api import async_playwright
import time

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1600, "height": 900})

        # Listen for console messages
        page.on("console", lambda msg: print(f"[CONSOLE] {msg.type}: {msg.text}"))

        await page.goto("http://127.0.0.1:8000")
        time.sleep(3)

        print("\n=== Selecting text and pressing Ctrl+D ===\n")

        # Try to select some text
        await page.evaluate("""() => {
            // Create a simple text selection
            const range = document.createRange();
            const sel = window.getSelection();

            // Find first text node
            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_TEXT,
                null
            );

            let node = walker.nextNode();
            while (node && node.textContent.trim().length < 3) {
                node = walker.nextNode();
            }

            if (node) {
                const text = node.textContent;
                const word = text.split(/\\s+/)[0];
                range.setStart(node, 0);
                range.setEnd(node, word.length);
                sel.removeAllRanges();
                sel.addRange(range);
                console.log('Selected text:', window.getSelection().toString());
            }
        }""")

        time.sleep(0.5)

        # Press Ctrl+D
        print("Pressing Ctrl+D...")
        await page.keyboard.press("Control+d")
        time.sleep(2)

        # Check console output
        print("\nChecking if popup appeared...")
        popup_visible = await page.locator("#popup").is_visible()
        print(f"Popup visible: {popup_visible}")

        await browser.close()

asyncio.run(test())
