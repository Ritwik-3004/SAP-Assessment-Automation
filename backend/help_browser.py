"""
Capture the address of the SAP Help Portal page that SARA opens, and close that browser tab again.

SARA's information (`i`, Documentation) button does not show the documentation inside SAP GUI: SAP GUI
starts the machine's browser on a help.sap.com page. SAP GUI scripting cannot read a browser, so this
uses Windows UI Automation (through PowerShell) to read the address bar of the browser window that SAP
just opened -- only to learn the page address -- and then closes exactly that tab, so no tabs pile up.
The page itself is read separately, headlessly (helpportal.py).

Works with Microsoft Edge or Google Chrome (the machine's default browser). If the address cannot be
read (another browser, a minimised window, UI Automation blocked) the caller gets None and carries on.
"""

import json
import logging
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

HELP_HOST = "help.sap.com"

_PS_COMMON = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]
$TS = [System.Windows.Automation.TreeScope]
$CT = [System.Windows.Automation.ControlType]
function Get-BrowserWindows {
  $root = $AE::RootElement
  $cond = New-Object System.Windows.Automation.PropertyCondition($AE::ClassNameProperty, 'Chrome_WidgetWin_1')
  $wins = $root.FindAll($TS::Children, $cond)
  foreach ($w in $wins) {
    try {
      $proc = (Get-Process -Id $w.Current.ProcessId -ErrorAction Stop).ProcessName
      if ($proc -notmatch '^(msedge|chrome)$') { continue }
      $edits = $w.FindAll($TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, $CT::Edit)))
      $url = ''
      foreach ($e in $edits) {
        if ($e.Current.Name -match 'Address') {
          try { $url = $e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch {}
          break
        }
      }
      [pscustomobject]@{ Window = $w; Title = $w.Current.Name; Url = $url }
    } catch { continue }
  }
}
function Get-SelectedTab($w) {
  $tabs = $w.FindAll($TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, $CT::TabItem)))
  foreach ($t in $tabs) {
    try {
      if ($t.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Current.IsSelected) { return $t }
    } catch {}
  }
  return $null
}
"""

_PS_LIST = _PS_COMMON + r"""
$out = @()
foreach ($b in Get-BrowserWindows) {
  $tab = Get-SelectedTab $b.Window
  $out += [pscustomobject]@{ title = $b.Title; url = $b.Url; tab = $(if ($tab) { $tab.Current.Name } else { '' }) }
}
ConvertTo-Json -InputObject @($out) -Compress
"""

_PS_CLOSE = _PS_COMMON + r"""
$target = ($env:HELP_URL -split '\?')[0]
$closed = $false
foreach ($b in Get-BrowserWindows) {
  if ($b.Url -and ((($b.Url -split '\?')[0]) -eq $target)) {
    $tab = Get-SelectedTab $b.Window
    if (-not $tab) { continue }
    $buttons = $tab.FindAll($TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, $CT::Button)))
    foreach ($btn in $buttons) {
      if ($btn.Current.Name -match 'Close') {
        $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        $closed = $true
        break
      }
    }
    if ($closed) { break }
  }
}
if ($closed) { 'closed' } else { 'not-closed' }
"""


def _run_powershell(script: str, env: Optional[dict] = None, timeout: float = 45.0) -> str:
    import os

    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout, env=full_env,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "PowerShell failed").strip()[:300])
    return result.stdout.strip()


def list_browser_pages() -> list[dict]:
    """The page open in each Edge/Chrome window's selected tab: [{"title", "url", "tab"}]."""
    out = _run_powershell(_PS_LIST)
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def _is_help_page(page: dict) -> bool:
    return HELP_HOST in (page.get("url") or "")


def help_urls_open() -> set[str]:
    try:
        return {p["url"] for p in list_browser_pages() if _is_help_page(p)}
    except Exception:
        return set()


def wait_for_new_help_page(before: set[str], timeout: float = 25.0, interval: float = 1.0) -> Optional[str]:
    """The address of a help.sap.com page that was not open in *before*, once the browser shows one;
    None if none appears within *timeout* seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for page in list_browser_pages():
                url = page.get("url") or ""
                if _is_help_page(page) and url not in before:
                    # The portal rewrites the address once the page has loaded; return the settled one.
                    time.sleep(1.5)
                    for later in list_browser_pages():
                        if _is_help_page(later) and later["url"].split("?")[0] == url.split("?")[0]:
                            return later["url"]
                    return url
        except Exception as exc:
            logger.warning("Could not read the browser address bar: %s", exc)
            return None
        time.sleep(interval)
    return None


def close_help_page(url: str) -> bool:
    """Close the tab showing the page at *url* (matched by path; only if it is the selected tab of a
    browser window, so a tab of the user's own is never touched). True if closed."""
    try:
        return _run_powershell(_PS_CLOSE, env={"HELP_URL": url}) == "closed"
    except Exception as exc:
        logger.warning("Could not close the help page tab: %s", exc)
        return False
