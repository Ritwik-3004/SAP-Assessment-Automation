import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).parent.parent

# One "results" folder at the project root. Every task saves what it produces automatically into a
# sub-folder named exactly like the task in the sidebar, and a task that builds on an earlier one reads
# that earlier task's sub-folder. Keys are the sidebar task ids.
RESULTS_DIR = _PROJECT_ROOT / "results"
RESULT_FOLDERS = {
    "TOP_TABLES": "Generate Table List",
    "BATCH": "Find Archiving Objects for Tables",
    "HEADER_TABLES": "Find Header Tables for Archiving Objects",
    "TABLE_ANALYSIS": "Table Analysis",
    "OBJECT_ANALYSIS": "Archiving Object Analysis",
    "OPEN_ITEM_ANALYSIS": "Open Item Analysis",
    "FINAL_REPORT": "Final report generation",
}
# Screenshots/text dumped when a SAP for Me sign-in fails (diagnostics, not results)
DEBUG_DIR = _PROJECT_ROOT / "debug"


def results_folder(task: str) -> Path:
    """The (created) results sub-folder of a task, e.g. results_folder("BATCH")."""
    path = RESULTS_DIR / RESULT_FOLDERS[task]
    path.mkdir(parents=True, exist_ok=True)
    return path
CREDENTIALS_FILE = _PROJECT_ROOT / "sap_credentials.json"
SAP_FOR_ME_CREDENTIALS_FILE = _PROJECT_ROOT / "sap_for_me_credentials.json"
LLM_SETTINGS_FILE = _PROJECT_ROOT / "llm_settings.json"
LLM_USAGE_FILE = _PROJECT_ROOT / "llm_usage.json"

# SAP Logon landscape file path (used to discover configured systems)
SAP_LANDSCAPE_PATHS = [
    os.path.expanduser(r"~\AppData\Roaming\SAP\Common\SAPUILandscape.xml"),
    r"C:\Program Files (x86)\SAP\FrontEnd\SAPgui\SAPUILandscape.xml",
]

# SAP GUI executable path — adjust if installed elsewhere
SAPLOGON_EXE = os.getenv(
    "SAPLOGON_EXE",
    r"C:\Program Files (x86)\SAP\FrontEnd\SAPgui\saplogon.exe",
)

# Timeout (seconds) waiting for SAP screens to load
SAP_SCREEN_WAIT = float(os.getenv("SAP_SCREEN_WAIT", "1.5"))

# FastAPI server
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))

# Claude API (used to score archiving objects for relevance per table)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SCORING_MODEL = os.getenv("SCORING_MODEL", "claude-haiku-4-5")

# Groq (free-tier alternative to Claude, chosen in the app's "AI Model" panel).
# The key is read only from backend/.env -- the app never asks for or stores it.
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Ollama (models that run on THIS computer, chosen in the "AI Model" panel). The address must be this
# machine (localhost / 127.x / ::1): the app refuses any other host so nothing leaves the computer.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
# Seconds to wait for one local answer. CPU-only machines are slow, so this is generous.
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "900"))

# SAP for Me portal scraping (housekeeping-program fallback lookup). Headless
# by default; flip to "false" to watch the browser while debugging selectors.
SAP_FOR_ME_HEADLESS = os.getenv("SAP_FOR_ME_HEADLESS", "true").lower() != "false"

# Optional: IT-approved Chrome for Testing build + matching chromedriver. When
# unset, Selenium uses the installed Chrome (which managed machines may block).
SAP_FOR_ME_CHROME_PATH = os.getenv("SAP_FOR_ME_CHROME_PATH") or None
SAP_FOR_ME_CHROMEDRIVER_PATH = os.getenv("SAP_FOR_ME_CHROMEDRIVER_PATH") or None
