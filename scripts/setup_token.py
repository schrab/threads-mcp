#!/usr/bin/env python3
"""One-time helper for the full OAuth redirect flow.

Only needed to authorize non-tester accounts. For your own account, prefer the
dashboard's user-token generator (README section 1) — it issues a long-lived
token directly and needs none of this.

Usage:
    python scripts/setup_token.py --client-id <THREADS_APP_ID> \
        --client-secret <THREADS_APP_SECRET> --code <oauth_code> \
        --redirect-uri https://your.host/callback

Use the *Threads* app ID / app secret (Settings -> Basic), not the Meta App
ID / App secret; Meta issues both and only the Threads pair is the OAuth
client for this API.

--redirect-uri is required because it must exactly match a URI registered on
your app, and Threads rejects http:// (including http://localhost/...) with
"Insecure Login Blocked" 1349187. Host it on https:// and point the
Authorization Window at it:

  https://threads.net/oauth/authorize?client_id=<THREADS_APP_ID>\
&redirect_uri=<url-encoded-https-uri>&response_type=code\
&scope=threads_basic,threads_content_publish

The browser lands on your redirect URI with ?code=...; nothing has to be
listening there, you can copy the code from the address bar.
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--client-id", required=True, help="Threads app id")
    p.add_argument("--client-secret", required=True, help="Threads app secret")
    p.add_argument("--code", required=True, help="OAuth authorization code")
    p.add_argument(
        "--redirect-uri",
        required=True,
        help="Must exactly match a registered https:// redirect URI on the app",
    )
    args = p.parse_args()

    if not args.redirect_uri.startswith("https://"):
        print(
            "[!] --redirect-uri must be https://; Threads rejects http:// "
            "redirect URIs.",
            file=sys.stderr,
        )
        return 1

    # 1. Exchange the code for a short-lived user token
    q = urllib.parse.urlencode(
        {
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "code": args.code,
            "grant_type": "authorization_code",
            "redirect_uri": args.redirect_uri,
        }
    )
    short = get(f"https://graph.threads.net/oauth/access_token?{q}")
    short_token = short["access_token"]
    user_id = short["user_id"]
    print(f"[+] short-lived token obtained for user id {user_id}", file=sys.stderr)

    # 2. Exchange for a long-lived (60-day) token
    q2 = urllib.parse.urlencode(
        {
            "grant_type": "th_exchange_token",
            "client_secret": args.client_secret,
            "access_token": short_token,
        }
    )
    long_ = get(f"https://graph.threads.net/access_token?{q2}")
    long_token = long_["access_token"]
    print("[+] long-lived token obtained", file=sys.stderr)

    # 3. Emit ready-to-use exports
    print("export THREADS_USER_ID='%s'" % user_id)
    print("export THREADS_ACCESS_TOKEN='%s'" % long_token)
    print("export THREADS_APP_SECRET='%s'" % args.client_secret)
    print(
        "# Tip: refresh before 60 days with GET https://graph.threads.net/access_token"
        "?grant_type=th_refresh_token&access_token=<token>",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
