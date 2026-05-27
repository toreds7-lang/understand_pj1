import asyncio
from playwright.async_api import async_playwright
import time

async def test_ctrl_g():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1600, "height": 900})
        
        # Navigate to the app
        await page.goto("http://127.0.0.1:8000")
        
        # Wait for the page to fully load
        await page.wait_for_selector("#left", timeout=10000)
        time.sleep(2)
        
        # Take a screenshot before Ctrl+G
        await page.screenshot(path="before_ctrl_g.png")
        print("Screenshot taken: before_ctrl_g.png")
        
        # Verify panels are visible before Ctrl+G
        left_collapsed = await page.locator("#left").evaluate("el => el.classList.contains('collapsed')")
        middle_collapsed = await page.locator("#middle").evaluate("el => el.classList.contains('collapsed')")
        chat_collapsed = await page.locator("#chat").evaluate("el => el.classList.contains('collapsed')")
        right_collapsed = await page.locator("#right").evaluate("el => el.classList.contains('collapsed')")
        graph_display = await page.locator("#graph").evaluate("el => el.style.display")
        
        print(f"Before Ctrl+G:")
        print(f"  Left panel collapsed: {left_collapsed}")
        print(f"  Middle panel collapsed: {middle_collapsed}")
        print(f"  Chat panel collapsed: {chat_collapsed}")
        print(f"  Right panel collapsed: {right_collapsed}")
        print(f"  Graph display: {graph_display}")
        
        # Press Ctrl+G
        await page.keyboard.press("Control+g")
        time.sleep(1.5)
        
        # Take a screenshot after Ctrl+G
        await page.screenshot(path="after_ctrl_g.png")
        print("\nScreenshot taken: after_ctrl_g.png")
        
        # Check panel states after Ctrl+G
        left_collapsed_after = await page.locator("#left").evaluate("el => el.classList.contains('collapsed')")
        middle_collapsed_after = await page.locator("#middle").evaluate("el => el.classList.contains('collapsed')")
        chat_collapsed_after = await page.locator("#chat").evaluate("el => el.classList.contains('collapsed')")
        right_collapsed_after = await page.locator("#right").evaluate("el => el.classList.contains('collapsed')")
        graph_display_after = await page.locator("#graph").evaluate("el => el.style.display")
        
        print(f"\nAfter Ctrl+G:")
        print(f"  Left panel collapsed: {left_collapsed_after}")
        print(f"  Middle panel collapsed: {middle_collapsed_after}")
        print(f"  Chat panel collapsed: {chat_collapsed_after}")
        print(f"  Right panel collapsed: {right_collapsed_after}")
        print(f"  Graph display: {graph_display_after}")
        
        # Verify the expected behavior
        if (left_collapsed_after and middle_collapsed_after and 
            chat_collapsed_after and right_collapsed_after):
            print("\n✅ SUCCESS: All panels except graph are collapsed!")
        else:
            print("\n❌ FAIL: Not all panels are collapsed as expected")
        
        await browser.close()

asyncio.run(test_ctrl_g())
