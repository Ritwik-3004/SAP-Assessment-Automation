"""
Read a page of the SAP Help Portal (help.sap.com) headlessly, plus the sub-pages that hold conditions.

help.sap.com is a JavaScript application, so plain HTTP only returns an empty shell; this drives the same
headless Chrome for Testing the SAP for Me scraper uses (SAP_FOR_ME_CHROME_PATH / _CHROMEDRIVER_PATH). No
sign-in is needed. Verified live on the pages SARA's information button opens, e.g. FI_DOCUMNT
("Archiving Financial Accounting Documents (FI-GL, FI-AR, FI-AP)"):

  * the article is the page's <h1> plus its ".section" blocks (Definition, Use, Structure, Integration, ...);
    each block appears twice in the DOM, so blocks are de-duplicated;
  * a modal "Out of Maintenance" notice appears on older versions; it is dismissed with "Got it";
  * the contents tree marks the open page with `a.link.active` -- it appears a moment AFTER the article, so
    the reader waits for it (reading it straight away missed the sub-pages); its child pages are the other
    links inside the same `li.toc-item`. For SD_VBAK these include "Checks and Variants (SD-SLS)" and "Define
    Preconditions for Customer-Specific Additional Checks", which is where archiving conditions are
    described, so child pages whose title looks like a condition/check page are read too.
"""

import logging
import re
import time
from typing import Optional

from config import SAP_FOR_ME_CHROMEDRIVER_PATH, SAP_FOR_ME_CHROME_PATH

logger = logging.getLogger(__name__)

CONDITION_PAGE = re.compile(
    r"check|condition|prerequisite|precondition|archivab|residence|criteria|restriction|requirement", re.IGNORECASE
)
MAX_SUBPAGES = 4

_JS_DISMISS = r"""
const b = Array.from(document.querySelectorAll('button')).find(x => /^\s*got it\s*$/i.test(x.innerText));
if (b) { b.click(); return true; } return false;
"""

_JS_ARTICLE = r"""
const h1 = Array.from(document.querySelectorAll('h1')).pop();
if (!h1) return null;
const art = h1.parentElement;
const seen = new Set(), blocks = [];
art.querySelectorAll('.section').forEach(e => {
  const t = e.textContent.replace(/\s+/g, ' ').trim();
  if (t && !seen.has(t)) { seen.add(t); blocks.push(t); }
});
return {title: h1.innerText.trim(), blocks: blocks};
"""

_JS_CHILD_PAGES = r"""
const act = document.querySelector('a.link.active');
if (!act) return null;
const li = act.closest('li');
if (!li) return null;
return Array.from(li.querySelectorAll('a.link'))
  .filter(a => a !== act)
  .map(a => [a.innerText.replace(/\s+/g, ' ').trim(), a.href]);
"""


_JS_TOC_LINKS = r"""
return Array.from(document.querySelectorAll('a.link'))
  .map(a => [a.innerText.replace(/\s+/g, ' ').trim(), a.href])
  .filter(x => x[0] && x[1].includes('/docs/'));
"""


class HelpPortalError(Exception):
    pass


class HelpPortalReader:
    """One headless browser, reused for every page. Use as a context manager (or call close())."""

    def __init__(self):
        self._driver = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def _open(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,1000")
        if SAP_FOR_ME_CHROME_PATH:
            options.binary_location = SAP_FOR_ME_CHROME_PATH
        service = Service(executable_path=SAP_FOR_ME_CHROMEDRIVER_PATH) if SAP_FOR_ME_CHROMEDRIVER_PATH else None
        try:
            self._driver = webdriver.Chrome(options=options, service=service)
        except Exception as exc:
            reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            raise HelpPortalError(f"could not start the browser ({reason})") from exc

    def _page(self, url: str, timeout: float = 15.0) -> dict:
        """Load *url* and return {"title", "text"} of its article."""
        if self._driver is None:
            self._open()
        driver = self._driver
        driver.get(url)
        deadline = time.monotonic() + timeout
        article = None
        while time.monotonic() < deadline:
            article = driver.execute_script(_JS_ARTICLE)
            if article and article["blocks"]:
                break
            time.sleep(0.7)
        if not article or not article["blocks"]:
            raise HelpPortalError("the page did not show any documentation text")
        try:
            driver.execute_script(_JS_DISMISS)
        except Exception:
            pass
        return {"title": article["title"], "text": "\n\n".join(article["blocks"])}

    def read(self, url: str, max_chars: int = 12000) -> dict:
        """The page at *url* and its condition-type child pages.

        Returns {"pages": [{"title", "url", "text"}], "problem": str}; "pages" is empty and "problem"
        says why if the page could not be read. Total text is capped at *max_chars*."""
        pages: list[dict] = []
        try:
            first = self._page(url)
        except Exception as exc:
            return {"pages": [], "problem": f"Could not read the Help Portal page: {exc}"}
        pages.append({"title": first["title"], "url": url, "text": first["text"]})

        # The contents tree loads a moment after the article: wait until it shows the open page.
        children: list = []
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                found = self._driver.execute_script(_JS_CHILD_PAGES)
            except Exception:
                found = None
            if found is not None:
                children = found
                break
            time.sleep(0.7)
        wanted = [(t, h) for t, h in children if CONDITION_PAGE.search(t)][:MAX_SUBPAGES]

        # A page the article refers to by name ("... refer to Checks (FI-GL, FI-AR, FI-AP)") is not always a
        # child in the contents tree or a link in the text (FI_DOCUMNT): look such titles up in the tree.
        if len(wanted) < MAX_SUBPAGES:
            try:
                toc = self._driver.execute_script(_JS_TOC_LINKS) or []
            except Exception:
                toc = []
            have = {h.split("?")[0] for _, h in wanted} | {url.split("?")[0]}
            for title, href in toc:
                if (
                    len(wanted) >= MAX_SUBPAGES
                    or len(title) < 8
                    or not CONDITION_PAGE.search(title)
                    or title not in first["text"]
                    or href.split("?")[0] in have
                ):
                    continue
                wanted.append((title, href))
                have.add(href.split("?")[0])
        for title, href in wanted:
            try:
                page = self._page(href)
                pages.append({"title": page["title"] or title, "url": href, "text": page["text"]})
            except Exception as exc:
                logger.warning("Could not read Help Portal sub-page '%s': %s", title, exc)

        # share the text budget across pages, the main page first
        budget = max_chars
        for p in pages:
            share = max(1500, budget // max(1, len(pages)))
            p["text"] = p["text"][:share]
        return {"pages": pages, "problem": ""}
