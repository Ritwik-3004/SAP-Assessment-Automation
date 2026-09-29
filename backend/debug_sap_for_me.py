"""
Standalone debug script for the SAP for Me scraper (sap_for_me.py) --
NOT used by the running app. Run it manually while iterating on selectors
against the live portal:

    python backend/debug_sap_for_me.py TABLE_NAME

Requires SAP for Me credentials already saved via the app (or present in
sap_for_me_credentials.json at the project root). Runs headed (visible
browser) regardless of SAP_FOR_ME_HEADLESS, logs in, performs one search,
and prints what it found -- result titles/types, and the outcome of reading
+ extracting a housekeeping program -- so a broken selector can be spotted
and reported precisely instead of guessed at.
"""

import logging
import sys

import config
import sap_for_me

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")


def main():
    if len(sys.argv) != 2:
        print("Usage: python backend/debug_sap_for_me.py TABLE_NAME")
        sys.exit(1)

    table_name = sys.argv[1]
    sap_for_me.SAP_FOR_ME_HEADLESS = False  # always watch this run

    creds = sap_for_me._load_credentials()
    if not creds:
        print(f"No SAP for Me credentials found at {config.SAP_FOR_ME_CREDENTIALS_FILE}.")
        print("Save them via the app's sidebar panel first.")
        sys.exit(1)

    print(f"Logging in as {creds['email']}...")
    session = sap_for_me.SapForMeSession(creds["email"], creds["password"])
    try:
        session._open()
        print("Login OK.")

        query = f"{table_name} housekeeping program"
        print(f"Searching: {query!r}")
        results = session._search(query)
        print(f"Parsed {len(results)} result(s):")
        for r in results:
            print(f"  [{r['type']}] {r['title']}")

        print("\nRunning full lookup() (search + read top articles + LLM extraction)...")
        found = session.lookup(table_name)
        print(f"\nResult: {found}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
