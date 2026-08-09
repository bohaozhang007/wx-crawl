from __future__ import annotations

import os
import sys
from pathlib import Path


def load_article_fetcher(project: Path):
    """Select a Playwright browser without changing we-mp-rss source files."""
    project_text = str(project)
    if project_text not in sys.path:
        sys.path.insert(0, project_text)

    from driver import playwright_driver, wxarticle

    base_controller = playwright_driver.PlaywrightController
    browser_type = os.environ.get("BROWSER_TYPE", "chromium")

    class ConfiguredPlaywrightController(base_controller):
        def __init__(self, *args, **kwargs):
            if len(args) < 2 and "browser_type" not in kwargs:
                kwargs["browser_type"] = browser_type
            super().__init__(*args, **kwargs)

    wxarticle.PlaywrightController = ConfiguredPlaywrightController
    return wxarticle.WXArticleFetcher
