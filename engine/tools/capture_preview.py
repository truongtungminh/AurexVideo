#!/usr/bin/env python3
"""Capture one deterministic preview frame without rendering the full video."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from render_demo import local_server, mounted_topic_url


async def capture(topic: Path, output: Path, time: float) -> None:
    from playwright.async_api import async_playwright

    topic = topic.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with local_server(topic.parent) as port:
        url = f"http://127.0.0.1:{port}/index.html?topic={mounted_topic_url(topic)}&render=1&preview=1"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1080, "height": 1920})
            await page.goto(url, wait_until="networkidle")
            await page.wait_for_function("window.__AUREX_DEMO_READY__ === true", timeout=30_000)
            await page.evaluate("(time) => window.renderAt(time)", time)
            await page.wait_for_timeout(250)
            await page.screenshot(path=str(output))
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "topic",
        nargs="?",
        type=Path,
        default=Path("project/inox-304-vs-316/topic.json"),
    )
    parser.add_argument("--time", type=float, default=30.3)
    parser.add_argument("--output", type=Path, default=Path("output/inox-preview.png"))
    args = parser.parse_args()
    asyncio.run(capture(args.topic, args.output, args.time))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
