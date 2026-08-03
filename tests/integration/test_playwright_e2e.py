import os
import threading
import time
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from nexus_scalp.web.server import create_app

PORT = 9091

@pytest.fixture(scope="module", autouse=True)
def run_dev_server():
    """Starts the FastAPI Web Server in a background thread."""
    app = create_app(engine_ref=None)
    config = uvicorn.Config(app=app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(2) # Allow server to boot
    yield
    server.should_exit = True
    thread.join(timeout=2)


def test_playwright_e2e_canvas_and_tuner():
    """Programmatically verifies the Web UI Canvas and Algorithm Live Tuner panel using Playwright."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Create context and page
        context = browser.new_context()
        page = context.new_page()

        # Navigate to Dashboard
        url = f"http://127.0.0.1:{PORT}"
        page.goto(url)
        page.wait_for_timeout(1000)

        # 1. Switch to Bot Settings Tab to view the Tuner Panel
        page.click("button:has-text('Bot Settings')")
        page.wait_for_timeout(1000)

        # Assert Algorithm Live Tuner is present
        assert page.locator("h3:has-text('Algorithm Live Tuner')").is_visible()

        # Adjust the sliders
        page.fill("#tuner-atr-sl-buffer", "2.5")
        page.evaluate("document.getElementById('tuner-atr-sl-buffer').dispatchEvent(new Event('input'))")
        page.wait_for_timeout(500)

        page.fill("#tuner-min-rr", "2.2")
        page.evaluate("document.getElementById('tuner-min-rr').dispatchEvent(new Event('input'))")
        page.wait_for_timeout(500)

        # Assert the slider labels updated
        assert page.inner_text("#val-atr-sl-buffer") == "2.5"
        assert page.inner_text("#val-min-rr") == "2.2"

        # Click Apply Tuner Settings button
        # Intercept PUT request to verify HTTP 200
        with page.expect_response("**/api/algo/config") as response_info:
            page.click("button:has-text('Apply Tuner Settings')")
            page.wait_for_timeout(500)

        assert response_info.value.status == 200
        assert response_info.value.json()["success"] is True

        # 2. Switch back to Live Monitoring Tab to verify Chart Canvas Overlays
        page.click("button:has-text('Live Monitoring')")
        page.wait_for_timeout(1000)

        # Assert Chart Canvas is present
        canvas = page.locator("#candleChart")
        assert canvas.is_visible()

        # Capture and save screenshot of the Canvas
        os.makedirs("artifacts/screenshots", exist_ok=True)
        screenshot_path = "artifacts/screenshots/ui_canvas_verification.png"
        page.screenshot(path=screenshot_path)
        print(f"E2E Screenshot saved successfully to {screenshot_path}")

        # Save chart fixed proof screenshot
        proof_path = "artifacts/screenshots/chart_fixed_proof.png"
        page.screenshot(path=proof_path)
        print(f"Fixed Chart Proof saved successfully to {proof_path}")

        context.close()
        browser.close()
