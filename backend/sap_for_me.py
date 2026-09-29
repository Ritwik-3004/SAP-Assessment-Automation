"""
SAP for Me portal scraper -- the second-stage housekeeping/cleanup-program
fallback for a table the DVM Guide doesn't cover (see housekeeping.py).
Searches SAP for Me for "<table> housekeeping program", keeps only SAP
Knowledge Base Article / SAP Note results (falling back to SAP Community
only if neither of those turns up anything), reads the top few articles, and
asks Claude to extract a specific housekeeping program grounded in what they
say.

The portal is a JS-heavy single-page app behind a login wall, so this drives
a real (headless by default) Chrome browser via Selenium rather than
requests/BeautifulSoup. Logging in is the expensive part, so one
SapForMeSession is meant to be opened once per scoring run and reused across
every table that needs this fallback -- housekeeping.py owns that lifecycle
(open once via open_session(), call .lookup() per table, close() when done).

Selenium (not Playwright) specifically: this project's backend runs on a
32-bit Python virtualenv (needed for the SAP GUI COM scripting elsewhere in
the app), and Playwright's `greenlet` dependency has no prebuilt wheel for
32-bit Windows. Selenium's Python bindings are pure-Python and its built-in
Selenium Manager auto-downloads a matching chromedriver for whatever local
Chrome install it finds, so no compiler and no separate browser-install step
are needed.

Selectors here are built from screenshots of the live portal, not a live DOM
inspection (no portal access from this environment) -- expect these to need
adjustment against the real site; see backend/debug_sap_for_me.py. Result
cards are read by parsing the results page's plain text rather than depending
on exact CSS structure, since each result's resource-type badge ("SAP
Knowledge Base Article" / "SAP Note" / "SAP Community") is reliably plain
text right after its title/snippet even if the surrounding markup changes.
"""

import json
import logging
import re
from urllib.parse import quote

import anthropic
from pydantic import BaseModel, Field
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import (
    ANTHROPIC_API_KEY,
    SAP_FOR_ME_CREDENTIALS_FILE,
    SAP_FOR_ME_HEADLESS,
    SCORING_MODEL,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://me.sap.com"
MAX_ARTICLES = 3
MAX_EXCERPT_CHARS = 4000
WANTED_TYPES = ("SAP Knowledge Base Article", "SAP Note")
FALLBACK_TYPE = "SAP Community"
ALL_TYPES = WANTED_TYPES + (FALLBACK_TYPE,)
WAIT_SECONDS = 20

# Each result card ends with its resource-type badge on its own line,
# immediately followed by a language line (e.g. "SAP Knowledge Base
# Article\nEnglish") -- used to split a results page's plain text into
# per-result blocks without needing the underlying DOM structure. The match
# consumes both lines so the language line never leaks into the next
# result's block as a false "title".
_BADGE_RE = re.compile(
    r"^(" + "|".join(re.escape(t) for t in ALL_TYPES) + r")\s*\r?\n.*$",
    re.MULTILINE,
)

SYSTEM_PROMPT = (
    "You are an SAP data archiving expert. The database table you are given "
    "has NO SAP archiving object available in this system to remove its "
    "data, and SAP's official Data Management Guide does not name a "
    "housekeeping/cleanup program for it either. You are given the text of "
    "one or more SAP Knowledge Base Articles, SAP Notes, or SAP Community "
    "posts found by searching SAP for Me for this table's housekeeping "
    "programs. Read them and decide: do they name a SPECIFIC "
    "housekeeping/cleanup program, report, or transaction for this table? "
    "If one is named, return its name/ID and a one-sentence rationale that "
    "cites which article (by the 'Article N' label given) it came from. If "
    "none is named, or the text doesn't clearly identify one, set "
    "found=false -- never guess or invent a program name."
)


class SapForMeResult(BaseModel):
    found: bool = Field(
        description="True only if the articles name a specific housekeeping/"
        "cleanup program, report, or transaction for this table."
    )
    program: str = Field(default="", description="The program/report/transaction name or ID, if found.")
    rationale: str = Field(default="", description="One short sentence explaining the finding, citing which article it came from.")


class SapForMeLoginError(Exception):
    pass


def _load_credentials() -> "dict | None":
    if not SAP_FOR_ME_CREDENTIALS_FILE.exists():
        return None
    try:
        data = json.loads(SAP_FOR_ME_CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not read SAP for Me credentials file.")
        return None
    if not data.get("email") or not data.get("password"):
        return None
    return data


def open_session() -> "SapForMeSession | None":
    """Returns a logged-in SapForMeSession, or None if no SAP for Me
    credentials have been saved yet. Raises SapForMeLoginError if
    credentials exist but sign-in fails."""
    creds = _load_credentials()
    if not creds:
        return None
    session = SapForMeSession(creds["email"], creds["password"])
    session._open()
    return session


def _xpath_literal(text: str) -> str:
    """Safely quotes *text* for use inside an XPath expression, even if it
    contains both single and double quotes (XPath 1.0 has no escaping, so a
    literal with both quote types must be built with concat())."""
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"


class SapForMeSession:
    """One authenticated Selenium/Chrome session, meant to be opened once
    and reused across every table looked up during a single scoring run.
    Use as a context manager, or call close() explicitly."""

    def __init__(self, email: str, password: str):
        self._email = email
        self._password = password
        self._driver = None

    def _open(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        if SAP_FOR_ME_HEADLESS:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,1000")
        self._driver = webdriver.Chrome(options=options)
        self._login()

    def _login(self):
        driver = self._driver
        wait = WebDriverWait(driver, WAIT_SECONDS)
        try:
            driver.get(f"{BASE_URL}/")
            _click_by_text(driver, wait, "Sign In")
            _fill(driver, wait, "Email, User ID or Login Name", self._email)
            _click_by_text(driver, wait, "Continue")
            _fill(driver, wait, "Password", self._password)
            _click_by_text(driver, wait, "Continue")
            wait.until(EC.url_contains("me.sap.com/home"))
        except Exception as exc:
            raise SapForMeLoginError(f"SAP for Me sign-in failed: {exc}") from exc

    def close(self):
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def lookup(self, table_name: str) -> dict:
        query = f"{table_name} housekeeping program"
        try:
            results = self._search(query)
        except Exception as exc:
            logger.warning("SAP for Me search failed for %s: %s", table_name, exc, exc_info=True)
            return {"program": "", "rationale": f"SAP for Me search failed: {exc}"}

        chosen = [r for r in results if r["type"] in WANTED_TYPES][:MAX_ARTICLES]
        if not chosen:
            chosen = [r for r in results if r["type"] == FALLBACK_TYPE][:MAX_ARTICLES]
        if not chosen:
            return {"program": "", "rationale": "No SAP for Me articles found for this table."}

        articles = []
        for result in chosen:
            try:
                text = self._read_article(result["title"])
            except Exception as exc:
                logger.warning("Could not read SAP for Me article '%s': %s", result["title"], exc, exc_info=True)
                continue
            if text:
                articles.append((result["title"], result["type"], text))

        if not articles:
            return {"program": "", "rationale": "Found SAP for Me results but could not read their content."}

        return _extract_program(table_name, articles)

    def _search(self, query: str) -> list[dict]:
        payload = json.dumps({"q": query, "tab": "All"})
        self._driver.get(f"{BASE_URL}/knowledge/search/{quote(payload)}")
        WebDriverWait(self._driver, WAIT_SECONDS).until(
            lambda d: re.search(r"Results \d+-\d+ of \d+", d.find_element(By.TAG_NAME, "body").text)
        )
        raw_text = self._driver.find_element(By.TAG_NAME, "body").text
        return _parse_results(raw_text)

    def _read_article(self, title: str) -> str:
        driver = self._driver
        xpath = f"//a[contains(normalize-space(string(.)), {_xpath_literal(title)})]"
        link = driver.find_element(By.XPATH, xpath)
        link.click()
        WebDriverWait(driver, WAIT_SECONDS).until(EC.staleness_of(link))
        text = driver.find_element(By.TAG_NAME, "body").text[:MAX_EXCERPT_CHARS]
        driver.back()
        return text


def _click_by_text(driver, wait: WebDriverWait, text: str):
    """Clicks the first visible, enabled element (button/link/anything)
    whose text contains *text* -- used instead of a CSS/id selector since
    the portal's internal markup isn't inspectable from here."""
    xpath = f"//*[contains(normalize-space(string(.)), {_xpath_literal(text)})]"

    def _find_clickable(d):
        for el in d.find_elements(By.XPATH, xpath):
            if el.is_displayed() and el.is_enabled():
                return el
        return False

    el = wait.until(_find_clickable)
    el.click()


def _fill(driver, wait: WebDriverWait, label_text: str, value: str):
    """Fills the input associated with *label_text*, trying a <label>
    element first (the portal's fields look label-associated in
    screenshots), then falling back to a matching placeholder -- both
    unverified against the live DOM."""
    lit = _xpath_literal(label_text)
    by_label = f"//label[contains(normalize-space(string(.)), {lit})]/following::input[1]"
    by_placeholder = f"//input[contains(@placeholder, {lit})]"

    def _find_input(d):
        els = d.find_elements(By.XPATH, by_label) or d.find_elements(By.XPATH, by_placeholder)
        for el in els:
            if el.is_displayed():
                return el
        return False

    el = wait.until(_find_input)
    el.clear()
    el.send_keys(value)


def _parse_results(raw_text: str) -> list[dict]:
    """Splits a SAP for Me search-results page's plain text into per-result
    (title, resource type) pairs, in the page's own relevance order. Each
    result ends with its resource-type badge on its own line -- the title is
    the first non-empty line after the previous badge (or after the
    "Results X-Y of Z" line, for the first result)."""
    # ".*$" (MULTILINE, not DOTALL) also consumes the rest of that same
    # line (e.g. the trailing "in 884 ms"), so the first result's title
    # isn't mistaken for leftover text on the "Results X-Y of Z" line.
    start = re.search(r"Results \d+-\d+ of \d+.*$", raw_text, re.MULTILINE)
    body = raw_text[start.end():] if start else raw_text

    results = []
    cursor = 0
    for m in _BADGE_RE.finditer(body):
        block = body[cursor:m.start()]
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if lines:
            results.append({"title": lines[0], "type": m.group(1)})
        cursor = m.end()
    return results


def _extract_program(table_name: str, articles: list[tuple[str, str, str]]) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"program": "", "rationale": "ANTHROPIC_API_KEY not set -- cannot read SAP for Me articles."}

    excerpt_blocks = "\n\n".join(
        f"Article {i + 1} ({kind}) - {title}:\n---\n{text}\n---"
        for i, (title, kind, text) in enumerate(articles)
    )
    user_content = (
        f"Table: {table_name}\n\n{excerpt_blocks}\n\n"
        "Do any of these articles name a housekeeping/cleanup program for this table?"
    )
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        response = client.messages.parse(
            model=SCORING_MODEL,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            output_format=SapForMeResult,
        )
    except Exception as exc:
        logger.warning("SAP for Me LLM extraction failed for %s: %s", table_name, exc, exc_info=True)
        return {"program": "", "rationale": f"SAP for Me article extraction failed: {exc}"}

    parsed: SapForMeResult = response.parsed_output
    if parsed.found and parsed.program.strip():
        return {"program": parsed.program.strip(), "rationale": parsed.rationale}
    return {
        "program": "",
        "rationale": parsed.rationale or "SAP for Me articles found, but none name a specific housekeeping program.",
    }
