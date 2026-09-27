#!/usr/bin/env python3
"""One-time helper to mint a long-lived Threads token and print env exports.

Usage:
    python scripts/setup_token.py --client-id <APP_ID> --client-secret <APP_SECRET> \
        --code <oauth_code> [--redirect-uri <uri>]

The OAuth code comes from the Threads OAuth dialog:
  https://auth.threadapp.com/auth/connect?client_id=<APP_ID>\
&redirect_uri=<URI>&scope=threads_basic,threads_content_publish,\
threads_manage_replies,threads_read_replies,threads_manage_insights\&response_type=code
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
        default="https://localhost/callback",
        help="Must match the redirect URI registered on the app",
    )
    args = p.parse_args()

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
