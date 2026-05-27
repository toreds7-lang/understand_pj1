import asyncio
from playwright.async_api import async_playwright
import time

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1600, "height": 900})

        page.on("console", lambda msg: print(f"[{msg.type.upper()}] {msg.text}"))

        await page.goto("http://127.0.0.1:8000")
        time.sleep(3)

        print("\n=== Finding and selecting text from PDF page ===\n")

        # Select text from pdf-page
        result = await page.evaluate("""() => {
            const pdfPage = document.querySelector(".pdf-page");
            if (pdfPage) {
                console.log("Found PDF page element");
                const text = pdfPage.innerText || pdfPage.textContent;
                console.log("PDF text length:", text.length);

                // Get first word
                const words = text.trim().split(/\\s+/).filter(w => w.length > 2);
                const firstWord = words[0];
                console.log("First word to select:", firstWord);

                // Find and select it
                const range = document.createRange();
                const sel = window.getSelection();
                const walker = document.createTreeWalker(
                    pdfPage,
                    NodeFilter.SHOW_TEXT,
                    null
                );

                let node;
                while (node = walker.nextNode()) {
                    const idx = node.textContent.indexOf(firstWord);
                    if (idx !== -1) {
                        console.log("Found word, selecting...");
                        range.setStart(node, idx);
                        range.setEnd(node, idx + firstWord.length);
                        sel.removeAllRanges();
                        sel.addRange(range);
                        console.log("Selected text:", sel.toString());
                        return true;
                    }
                }
            }
            return false;
        }""")

        print(f"Selection successful: {result}")
        time.sleep(1)

        print("\n=== Pressing Ctrl+D ===\n")
        await page.keyboard.press("Control+d")
        time.sleep(2)

        popup_visible = await page.locator("#popup").is_visible()
        print(f"\nPopup visible: {popup_visible}")

        if popup_visible:
            head = await page.locator("#pop-head").text_content()
            target = await page.locator("#pop-target").text_content()
            body_text = await page.locator("#pop-body").inner_text()
            print(f"SUCCESS! Popup opened:")
            print(f"  Title: {head}")
            print(f"  Word: {target}")
            print(f"  Content preview: {body_text[:100] if body_text else 'Loading...'}")

        await browser.close()

asyncio.run(test())
