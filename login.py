"""
Extracts your X.com session cookies from your real Brave/Chrome browser.
No login window — uses the session you already have.

Usage:
    python login.py
"""

import json
import time
from pathlib import Path

import browser_cookie3

COOKIES_FILE = Path(__file__).parent / "session_cookies.json"

X_DOMAINS = {"x.com", "twitter.com", ".x.com", ".twitter.com"}


def _to_playwright(jar) -> list:
    cookies = []
    for c in jar:
        if not any(d in c.domain for d in ("x.com", "twitter.com")):
            continue
        # Ensure domain starts with a dot for Playwright
        domain = c.domain if c.domain.startswith(".") else f".{c.domain}"
        cookies.append({
            "name": c.name,
            "value": c.value or "",
            "domain": domain,
            "path": c.path or "/",
            "expires": int(c.expires) if c.expires else int(time.time()) + 86400 * 30,
            "httpOnly": bool(getattr(c, "_rest", {}).get("HttpOnly")),
            "secure": bool(c.secure),
            "sameSite": "None",
        })
    return cookies


def main() -> None:
    print("\n🔍 Extracting X.com cookies from your browser...")

    cookies = []
    found_in = None

    for browser_name, loader in [
        ("Brave",  browser_cookie3.brave),
        ("Chrome", browser_cookie3.chrome),
        ("Safari", browser_cookie3.safari),
    ]:
        try:
            jar = loader()
            cookies = _to_playwright(jar)
            if cookies:
                found_in = browser_name
                break
        except Exception as e:
            print(f"  {browser_name}: {e}")

    if not cookies:
        print("\n❌ Could not find X.com cookies in any browser.")
        print("   Make sure you are logged in to X.com in Brave or Chrome.")
        return

    # Sanity-check: auth_token must be present
    auth_names = {c["name"] for c in cookies}
    if "auth_token" not in auth_names:
        print(f"\n⚠️  Found {found_in} cookies but no auth_token.")
        print("   You may not be logged in to X.com in that browser.")
        return

    COOKIES_FILE.write_text(json.dumps(cookies, indent=2))

    print(f"✅ Saved {len(cookies)} cookies from {found_in} → {COOKIES_FILE.name}")
    print("   Run 'python bot.py' and send an X Article link to test.\n")


if __name__ == "__main__":
    main()
