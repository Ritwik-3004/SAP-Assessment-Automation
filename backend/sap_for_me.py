"""
SAP for Me portal scraper, used by housekeeping.py (housekeeping-program lookup for a table
the DVM Guide doesn't cover) and header_tables.py (header-table lookup for an archiving
object ARCH_DEF can't settle). It searches SAP for Me, keeps only SAP Knowledge Base
Article / SAP Note results, reads the top few articles, and hands their text to the selected
AI model (see llm.py). When SAP for Me yields no readable Note or Knowledge Base Article, it
falls back to searching SAP Community (community.sap.com, public, same browser) and reads the
top few posts instead; those are labelled "SAP Community" so the model and the user can see
the source is a forum post rather than an official SAP document.

The portal is a JS-heavy single-page app behind a login wall, so this drives a real (headless
by default) Chrome browser via Selenium rather than requests/BeautifulSoup. Logging in is the
expensive part, so one SapForMeSession is meant to be opened once per run and reused across
every item that needs it -- callers own that lifecycle (open once via open_session(), call
.lookup() / .fetch_articles() per item, close() when done).

Browser: Chrome for Testing plus its matching chromedriver, set in backend/.env
(SAP_FOR_ME_CHROME_PATH / SAP_FOR_ME_CHROMEDRIVER_PATH). Managed Chrome/Edge installs often
set the IT policy RemoteDebuggingAllowed=0, which stops any automation tool from attaching.
Selenium (not Playwright) because this project's backend runs on a 32-bit Python virtualenv
(needed for the SAP GUI COM scripting elsewhere in the app) and Playwright's `greenlet`
dependency has no prebuilt wheel for 32-bit Windows.

The sign-in, search and article steps have been run against the live portal; the portal's
page text is relied on rather than CSS classes: result titles read "<number> - <title>", each
followed by its resource badge ("SAP Note" / "SAP Knowledge Base Article" / ...), and every
Note and KBA opens at /notes/<number>/E. If the portal changes, backend/debug_sap_for_me.py
saves a screenshot and the page text to output/ to show what broke.

The SAP Community stage has NOT been run against the live site yet. It relies on the forum's
stable address shape rather than CSS classes: a post is any link of the form
/t5/<board>/<title>/(ba|qaq|td|m)-p/<id>, found on the search page at
community.sap.com/t5/forums/searchpage/tab/message?q=<query>. backend/debug_sap_for_me.py
prints what that search finds.
"""

import json
import logging
import re
from urllib.parse import quote

from pydantic import BaseModel, Field
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import credentials_store
import llm
from config import (
    OUTPUT_DIR,
    SAP_FOR_ME_CHROME_PATH,
    SAP_FOR_ME_CHROMEDRIVER_PATH,
    SAP_FOR_ME_CREDENTIALS_FILE,
    SAP_FOR_ME_HEADLESS,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://me.sap.com"
MAX_ARTICLES = 3
WANTED_TYPES = ("SAP Knowledge Base Article", "SAP Note")
# Every resource badge the results page shows; a result's type is the first
# of these lines that appears after its title.
KNOWN_TYPES = WANTED_TYPES + ("SAP Community", "SAP Help", "Support Content")
WAIT_SECONDS = 20
ARTICLE_WAIT_SECONDS = 45

# SAP Community (Khoros forum, public -- no sign-in needed). Posts are discussions (td-p),
# questions (qaq-p), blog posts (ba-p) and replies (m-p); every one has a numeric id.
COMMUNITY_URL = "https://community.sap.com"
COMMUNITY_TYPE = "SAP Community"
MAX_COMMUNITY_POSTS = 3
COMMUNITY_LINK_SELECTOR = "a[href*='-p/']"
_COMMUNITY_POST_RE = re.compile(r"^(https://community\.sap\.com/t5/[^?#\s]*?/(?:ba|qaq|td|m)-p/(\d+))")

# On the live results page (confirmed from a real dump), every SAP Note and
# Knowledge Base Article title is "<number> - <title>", and the article opens
# at me.sap.com/notes/<number>/E for both kinds. Other result kinds (SAP Help,
# Support Content) have unnumbered titles and are ignored.
_TITLE_RE = re.compile(r"^(\d{5,9}) - (.+)$")

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
    "cites which article (by the 'Article N' label given) it came from. SAP "
    "Community posts are forum answers, less authoritative than Notes or Knowledge Base "
    "Articles: use one only if it clearly names a program for this table, and say in the "
    "rationale that it came from SAP Community. If "
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
    """The active saved SAP for Me account (most recently saved or selected), or None."""
    data = credentials_store.active_profile(SAP_FOR_ME_CREDENTIALS_FILE)
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


def _describe(exc: Exception) -> str:
    """A readable one-line reason for *exc*. Selenium's TimeoutException (and some others) stringify
    to just "Message:", which tells the user nothing."""
    text = str(exc).replace("Message:", " ").strip().splitlines()
    first = text[0].strip() if text else ""
    if first and first.lower() != "none":
        return first[:200]
    if isinstance(exc, TimeoutException):
        return "timed out waiting for the page to load"
    return type(exc).__name__


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
        # Lets _dump_debug report console errors and failed requests.
        options.set_capability("goog:loggingPrefs", {"browser": "ALL", "performance": "ALL"})
        service = None
        if SAP_FOR_ME_CHROME_PATH:
            options.binary_location = SAP_FOR_ME_CHROME_PATH
        if SAP_FOR_ME_CHROMEDRIVER_PATH:
            from selenium.webdriver.chrome.service import Service

            service = Service(executable_path=SAP_FOR_ME_CHROMEDRIVER_PATH)
        try:
            self._driver = webdriver.Chrome(options=options, service=service)
        except Exception as exc:
            # Corporate Chrome/Edge policy "RemoteDebuggingAllowed=0" makes this fail
            # with "DevToolsActivePort file doesn't exist" (blank browser window).
            reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            raise SapForMeLoginError(f"could not start the browser ({reason})") from exc
        try:
            self._login()
        except Exception:
            self.close()
            raise

    def _login(self):
        driver = self._driver
        wait = WebDriverWait(driver, WAIT_SECONDS)
        try:
            driver.get(f"{BASE_URL}/")
            _dismiss_cookie_banner(driver)
            _click_by_text(driver, wait, "Sign In")
            _fill_input(driver, wait, "input[type='email'], input[type='text']", self._email)
            _click_by_text(driver, wait, "Continue")
            _fill_input(driver, wait, "input[type='password']", self._password)
            _click_by_text(driver, wait, "Continue")
            wait.until(EC.url_contains("me.sap.com/home"))
        except Exception as exc:
            logger.warning("SAP for Me sign-in failed at %s: %s", driver.current_url, exc, exc_info=True)
            reason = (str(exc).splitlines() or [type(exc).__name__])[0]
            dumped = _dump_debug(driver)
            where = f" (screenshot and page text saved: {dumped[0]}, {dumped[1]})" if dumped else ""
            raise SapForMeLoginError(
                f"SAP for Me sign-in failed on page {driver.current_url!r}: {type(exc).__name__}: {reason}{where}"
            ) from exc

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

    def lookup(self, table_name: str, settings: "llm.Settings | None" = None) -> dict:
        """Find a housekeeping program for *table_name*. *settings* pins which AI model
        reads the articles (defaults to the model currently chosen in the app)."""
        settings = settings or llm.load_settings()
        articles, problem = self.fetch_articles(
            f"{table_name} housekeeping program", table_name, settings, subject="this table"
        )
        if not articles:
            return {"program": "", "rationale": problem}
        return _extract_program(table_name, articles, settings)

    def fetch_articles(
        self,
        query: str,
        keyword: str,
        settings: "llm.Settings | None" = None,
        subject: str = "this search",
        community_fallback: bool = True,
    ) -> "tuple[list[tuple[str, str, str]], str]":
        """Search SAP for Me for *query* and read the top few SAP Notes / Knowledge Base
        Articles, trimming each to the passages around *keyword* (and to the AI model's
        article-size budget). Returns (articles, problem): articles is a list of
        (title, type, text); problem is "" on success, otherwise a user-readable reason
        no articles could be read (*subject* names what was searched for)."""
        settings = settings or llm.load_settings()
        max_chars = llm.budget(settings)["article_chars"]

        articles, problem = self._fetch_sap_for_me_articles(query, keyword, subject, max_chars)
        if articles:
            return articles, ""

        if not community_fallback:
            return [], problem
        # No readable Note or Knowledge Base Article: try SAP Community before giving up.
        community, community_problem = self.fetch_community_posts(query, keyword, settings, subject)
        if community:
            return community, ""
        return [], f"{problem} {community_problem}"

    def fetch_community_posts(
        self,
        query: str,
        keyword: str,
        settings: "llm.Settings | None" = None,
        subject: str = "this search",
    ) -> "tuple[list[tuple[str, str, str]], str]":
        """Search SAP Community for *query* and read the top few posts (labelled "SAP Community").
        Same return shape as fetch_articles. Callers that run several queries use this directly
        (with community_fallback=False on fetch_articles) so every query gets a chance to find a
        Note or Knowledge Base Article before a forum post is used."""
        settings = settings or llm.load_settings()
        return self._fetch_community_posts(query, keyword, subject, llm.budget(settings)["article_chars"])

    def _fetch_sap_for_me_articles(
        self, query: str, keyword: str, subject: str, max_chars: int
    ) -> "tuple[list[tuple[str, str, str]], str]":
        try:
            results = self._search(query)
        except Exception as exc:
            logger.warning("SAP for Me search failed for %r: %s", query, exc, exc_info=True)
            return [], f"SAP for Me search failed: {_describe(exc)}"

        chosen = [r for r in results if r["type"] in WANTED_TYPES][:MAX_ARTICLES]
        if not chosen:
            return [], f"No SAP Note or Knowledge Base Article found on SAP for Me for {subject}."

        articles = []
        for result in chosen:
            try:
                text = self._read_article(result["url"], keyword, max_chars)
            except Exception as exc:
                logger.warning("Could not read SAP for Me article '%s': %s", result["title"], exc, exc_info=True)
                continue
            if text:
                articles.append((result["title"], result["type"], text))

        if not articles:
            return [], "Found SAP for Me results but could not read their content."
        return articles, ""

    def _fetch_community_posts(
        self, query: str, keyword: str, subject: str, max_chars: int
    ) -> "tuple[list[tuple[str, str, str]], str]":
        """The SAP Community fallback: search the forum and read the top few posts."""
        try:
            posts = self._search_community(query)
        except Exception as exc:
            logger.warning("SAP Community search failed for %r: %s", query, exc, exc_info=True)
            return [], f"SAP Community search failed: {_describe(exc)}"
        if not posts:
            return [], f"No SAP Community post found for {subject}."

        articles = []
        for post in posts[:MAX_COMMUNITY_POSTS]:
            try:
                text = self._read_community_post(post["url"], keyword, max_chars)
            except Exception as exc:
                logger.warning("Could not read SAP Community post '%s': %s", post["title"], exc, exc_info=True)
                continue
            if text:
                articles.append((post["title"], COMMUNITY_TYPE, text))
        if not articles:
            return [], "Found SAP Community posts but could not read their content."
        return articles, ""

    def _search(self, query: str) -> list[dict]:
        payload = json.dumps({"q": query, "tab": "All"})
        self._driver.get(f"{BASE_URL}/knowledge/search/{quote(payload, safe="")}")
        WebDriverWait(self._driver, WAIT_SECONDS).until(
            lambda d: re.search(
                r"Results \d+-\d+ of \d+|No results|did not match|0 results|no matches",
                d.find_element(By.TAG_NAME, "body").text,
                re.IGNORECASE,
            )
        )
        raw_text = self._driver.find_element(By.TAG_NAME, "body").text
        return _parse_results(raw_text)

    def _read_article(self, url: str, table_name: str, max_chars: int = 8000) -> str:
        """Opens an article by its direct address and returns the part of its
        text most relevant to *table_name*, at most *max_chars* long."""
        driver = self._driver
        driver.get(url)
        try:
            WebDriverWait(driver, ARTICLE_WAIT_SECONDS).until(
                lambda d: _looks_rendered(d.find_element(By.TAG_NAME, "body").text)
            )
        except TimeoutException:
            pass  # use whatever rendered; the extraction step copes with thin text
        return _relevant_excerpt(driver.find_element(By.TAG_NAME, "body").text, table_name, max_chars)

    def _search_community(self, query: str) -> list[dict]:
        """Searches SAP Community for *query*; returns {"id", "title", "url"} per post, in the
        site's relevance order. No sign-in is needed. An empty list means no posts matched."""
        driver = self._driver
        driver.get(f"{COMMUNITY_URL}/t5/forums/searchpage/tab/message?advanced=false&q={quote(query, safe='')}")
        try:
            WebDriverWait(driver, WAIT_SECONDS).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, COMMUNITY_LINK_SELECTOR)
                or re.search(r"no results|0 results|did not match|no matches|nothing found",
                             d.find_element(By.TAG_NAME, "body").text, re.IGNORECASE)
            )
        except TimeoutException:
            pass  # parse whatever rendered; an empty page just yields no posts
        links = [
            (a.get_attribute("href") or "", a.get_attribute("textContent") or "")
            for a in driver.find_elements(By.CSS_SELECTOR, COMMUNITY_LINK_SELECTOR)
        ]
        return _parse_community_links(links)

    def _read_community_post(self, url: str, keyword: str, max_chars: int = 8000) -> str:
        """Opens a SAP Community post and returns the part of its text most relevant to
        *keyword*, at most *max_chars* long."""
        driver = self._driver
        driver.get(url)
        try:
            WebDriverWait(driver, WAIT_SECONDS).until(
                lambda d: len(d.find_element(By.TAG_NAME, "body").text) > 600
            )
        except TimeoutException:
            pass
        return _relevant_excerpt(driver.find_element(By.TAG_NAME, "body").text, keyword, max_chars)


def _click_by_text(driver, wait: WebDriverWait, text: str):
    """Clicks the first visible, enabled button/link whose text contains
    *text*. Deliberately limited to button-like elements: matching any
    element would hit <html>/<body> first, since they contain the text too."""
    lit = _xpath_literal(text)
    xpath = (
        f"//button[contains(normalize-space(.), {lit})]"
        f" | //a[contains(normalize-space(.), {lit})]"
        f" | //*[@role='button'][contains(normalize-space(.), {lit})]"
        f" | //input[@type='submit' or @type='button'][contains(@value, {lit})]"
    )

    def _find_clickable(d):
        for el in d.find_elements(By.XPATH, xpath):
            if el.is_displayed() and el.is_enabled():
                return el
        return False

    el = wait.until(_find_clickable)
    try:
        el.click()
    except ElementClickInterceptedException:
        # Some other overlay is on top; a script click bypasses hit-testing.
        driver.execute_script("arguments[0].click();", el)


def _dismiss_cookie_banner(driver):
    """SAP's TrustArc consent dialog covers the landing page and swallows
    clicks. Choose "Deny All" (declines non-essential cookies; sign-in still
    works) and wait for it to go. Silently does nothing if there's no banner."""
    try:
        _click_by_text(driver, WebDriverWait(driver, 6), "Deny All")
        WebDriverWait(driver, 6).until(
            lambda d: not any(e.is_displayed() for e in d.find_elements(By.ID, "truste-consent-track"))
        )
    except Exception:
        pass


def _fill_input(driver, wait: WebDriverWait, css: str, value: str):
    """Types *value* into the first visible input matching *css*. Sign-in
    fields are found by input type (text/email/password) rather than by
    label, since the sign-in page's label wiring isn't verified."""

    def _find_input(d):
        for el in d.find_elements(By.CSS_SELECTOR, css):
            if el.is_displayed() and el.is_enabled():
                return el
        return False

    el = wait.until(_find_input)
    el.clear()
    el.send_keys(value)


def _console_problems(driver) -> list[str]:
    try:
        return [
            f"[{e['level']}] {e['message'][:300]}"
            for e in driver.get_log("browser")
            if e["level"] in ("SEVERE", "WARNING")
        ][:40]
    except Exception as exc:
        return [f"(could not read console log: {exc})"]


def _failed_requests(driver) -> list[str]:
    """URLs the page requested that failed or returned an HTTP error, from
    Chrome's performance log."""
    out: list[str] = []
    try:
        for entry in driver.get_log("performance"):
            msg = json.loads(entry["message"])["message"]
            params = msg.get("params", {})
            if msg.get("method") == "Network.responseReceived":
                resp = params.get("response", {})
                if resp.get("status", 0) >= 400:
                    out.append(f"HTTP {resp['status']}  {resp.get('url', '')[:200]}")
            elif msg.get("method") == "Network.loadingFailed":
                out.append(f"FAILED {params.get('errorText', '')}  (request {params.get('requestId', '')})")
    except Exception as exc:
        out.append(f"(could not read performance log: {exc})")
    return out[:40]


def _dump_debug(driver):
    """Saves a screenshot and the visible page text next to the outputs so a
    failed sign-in can be diagnosed without re-running. Never raises."""
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        png = OUTPUT_DIR / "sap_for_me_debug.png"
        txt = OUTPUT_DIR / "sap_for_me_debug.txt"
        driver.save_screenshot(str(png))
        body = driver.find_element(By.TAG_NAME, "body").text
        buttons = [
            (b.text or b.get_attribute("value") or "").strip()
            for b in driver.find_elements(By.CSS_SELECTOR, "button, a, [role='button'], input[type='submit']")
            if b.is_displayed()
        ]
        links = [
            f"{(a.text or '').strip()[:100]!r} -> {a.get_attribute('href')}"
            for a in driver.find_elements(By.CSS_SELECTOR, "a[href]")
            if a.is_displayed() and (a.text or "").strip()
        ][:80]
        txt.write_text(
            f"URL: {driver.current_url}\nTitle: {driver.title}\n\n"
            f"Visible buttons/links: {[b for b in buttons if b]}\n\n"
            f"--- console warnings/errors ---\n" + "\n".join(_console_problems(driver)) +
            f"\n\n--- failed network requests (status >= 400 or failed) ---\n" + "\n".join(_failed_requests(driver)) +
            f"\n\n--- links (text -> href) ---\n" + "\n".join(links) +
            f"\n\n--- page text ---\n{body}",
            encoding="utf-8",
        )
        return str(png), str(txt)
    except Exception:
        return None


def _parse_results(raw_text: str) -> list[dict]:
    """Extracts the numbered SAP Note / Knowledge Base Article results from a
    search-results page's plain text, in the page's own relevance order, as
    {"id", "title", "type", "url"}. A result starts at a "<number> - <title>"
    line; its type is the first known badge line ("SAP Note", "SAP Knowledge
    Base Article", ...) after that. Unnumbered results (SAP Help, Support
    Content) are skipped."""
    # ".*$" (MULTILINE, not DOTALL) also consumes the rest of that same
    # line (e.g. the trailing "in 884 ms").
    start = re.search(r"Results \d+-\d+ of \d+.*$", raw_text, re.MULTILINE)
    body = raw_text[start.end():] if start else raw_text

    results: list[dict] = []
    current = None
    for line in (ln.strip() for ln in body.splitlines()):
        m = _TITLE_RE.match(line)
        if m:
            current = {
                "id": m.group(1),
                "title": line,
                "type": "",
                "url": f"{BASE_URL}/notes/{m.group(1)}/E",
            }
            results.append(current)
        elif current is not None and not current["type"] and line in KNOWN_TYPES:
            current["type"] = line
    return results


def _parse_community_links(links: "list[tuple[str, str]]") -> list[dict]:
    """Turns the (href, text) pairs of a SAP Community search page into posts. Only links to a
    post (/t5/<board>/<title>/(ba|qaq|td|m)-p/<id>) count; the query string and #fragment are
    dropped, the same post is listed once (a reply link and its thread share an id), and the
    longest link text is its title. The page's own order is kept."""
    posts: dict[str, dict] = {}
    for href, text in links:
        m = _COMMUNITY_POST_RE.match((href or "").strip())
        if not m:
            continue
        title = " ".join((text or "").split())
        post = posts.setdefault(m.group(2), {"id": m.group(2), "title": "", "url": m.group(1)})
        if len(title) > len(post["title"]):
            post["title"] = title
    return [p for p in posts.values() if len(p["title"]) >= 8]


def _looks_rendered(text: str) -> bool:
    """True once an article page has rendered real content (the portal is a
    single-page app, so the body text is thin until its data arrives)."""
    return len(text) > 1200 and any(w in text for w in ("Symptom", "Solution", "Resolution", "Description"))


def _relevant_excerpt(text: str, table_name: str, max_chars: int = 8000) -> str:
    """Trims a long article to the start (title/symptom) plus a window around
    each mention of *table_name*, so a big how-to that lists dozens of tables
    keeps the part about this one instead of being cut off by a flat limit."""
    if len(text) <= max_chars:
        return text
    head = min(2000, max_chars // 3)
    spans = [(0, head)]
    for m in re.finditer(re.escape(table_name), text, re.IGNORECASE):
        spans.append((max(0, m.start() - 800), min(len(text), m.end() + 800)))
        if len(spans) >= 8:
            break
    spans.sort()
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    pieces = [text[s:e] for s, e in merged]
    return "\n...\n".join(pieces)[:max_chars]


def _extract_program(
    table_name: str, articles: list[tuple[str, str, str]], settings: "llm.Settings | None" = None
) -> dict:
    settings = settings or llm.load_settings()
    problem = llm.not_configured_message(settings)
    if problem:
        return {"program": "", "rationale": f"Cannot read SAP for Me articles: {problem}"}

    excerpt_blocks = "\n\n".join(
        f"Article {i + 1} ({kind}) - {title}:\n---\n{text}\n---"
        for i, (title, kind, text) in enumerate(articles)
    )
    user_content = (
        f"Table: {table_name}\n\n{excerpt_blocks}\n\n"
        "Do any of these articles name a housekeeping/cleanup program for this table?"
    )
    try:
        parsed: SapForMeResult = llm.structured(
            SYSTEM_PROMPT, user_content, SapForMeResult, max_tokens=500, settings=settings
        )
    except Exception as exc:
        logger.warning("SAP for Me LLM extraction failed for %s: %s", table_name, exc, exc_info=True)
        return {"program": "", "rationale": f"SAP for Me article extraction failed: {exc}"}

    if parsed.found and parsed.program.strip():
        return {"program": parsed.program.strip(), "rationale": parsed.rationale}
    return {
        "program": "",
        "rationale": parsed.rationale or "SAP for Me articles found, but none name a specific housekeeping program.",
    }
