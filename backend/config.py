import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Input / output folders at the project root (one level above backend/)
_PROJECT_ROOT = Path(__file__).parent.parent
INPUT_DIR = _PROJECT_ROOT / "input"
OUTPUT_DIR = _PROJECT_ROOT / "output"
CREDENTIALS_FILE = _PROJECT_ROOT / "sap_credentials.json"
SAP_FOR_ME_CREDENTIALS_FILE = _PROJECT_ROOT / "sap_for_me_credentials.json"

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

# SAP for Me portal scraping (housekeeping-program fallback lookup). Headless
# by default; flip to "false" to watch the browser while debugging selectors.
SAP_FOR_ME_HEADLESS = os.getenv("SAP_FOR_ME_HEADLESS", "true").lower() != "false"

# Optional: IT-approved Chrome for Testing build + matching chromedriver. When
# unset, Selenium uses the installed Chrome (which managed machines may block).
SAP_FOR_ME_CHROME_PATH = os.getenv("SAP_FOR_ME_CHROME_PATH") or None
SAP_FOR_ME_CHROMEDRIVER_PATH = os.getenv("SAP_FOR_ME_CHROMEDRIVER_PATH") or None
