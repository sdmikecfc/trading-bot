"""
Fetch all fractionalized domain tokens from the Doma API and save to tokens.json.

Run this once before starting the bot, and again periodically to pick up new tokens.
The bot also calls this automatically every TOKEN_REFRESH_CYCLES cycles.

Usage:
    python fetch_tokens.py

Output:
    tokens.json — list of all tokens with addresses and pool/launchpad info.
    Any manually-set price_floor_usd values are preserved on refresh.
"""

import json
import os
import sys

import requests

import config

GRAPHQL_URL = config.DOMA_API_URL
HEADERS = {
    "Api-Key": config.DOMA_API_KEY,
    "Content-Type": "application/json",
}

QUERY = """
{
  fractionalTokens(page: %d) {
    currentPage
    totalPages
    items {
      address
      name
      graduatedAt
      poolAddress
      launchpadAddress
      priceUsd
      initialFDV
      bondingFDV
    }
  }
}
"""

TOKENS_FILE = os.path.join(os.path.dirname(__file__), "tokens.json")


def fetch_page(page: int) -> dict:
    resp = requests.post(
        GRAPHQL_URL,
        json={"query": QUERY % page},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"API error: {data['errors']}")
    return data["data"]["fractionalTokens"]


def load_existing() -> dict:
    """Returns a dict of address -> existing token entry (to preserve manual fields)."""
    if not os.path.exists(TOKENS_FILE):
        return {}
    with open(TOKENS_FILE) as f:
        existing = json.load(f)
    return {t["address"].lower(): t for t in existing}


def fetch_all_tokens() -> list[dict]:
    existing = load_existing()

    all_tokens = []
    page = 1
    total_pages = None

    while True:
        print(f"  Fetching page {page}{f'/{total_pages}' if total_pages else ''}...")
        result = fetch_page(page)
        total_pages = result["totalPages"]

        for item in result["items"]:
            addr_lower = item["address"].lower()
            prev = existing.get(addr_lower, {})

            token = {
                "name": item["name"],
                "address": item["address"],
                "pool_address": item.get("poolAddress"),       # None = bonding curve
                "launchpad_address": item.get("launchpadAddress"),
                "price_usd": item.get("priceUsd", 0.0),
                "initial_fdv": item.get("initialFDV", 0.0),
                "bonding_fdv": item.get("bondingFDV", 0.0),
                "graduated": item.get("graduatedAt") is not None,
                # Preserve manually-set price floor — never overwrite with 0
                "price_floor_usd": prev.get("price_floor_usd", 0.0),
            }
            all_tokens.append(token)

        if page >= total_pages:
            break
        page += 1

    return all_tokens


def main():
    print(f"Fetching all tokens from Doma API...")
    try:
        tokens = fetch_all_tokens()
    except Exception as e:
        print(f"[ERROR] Failed to fetch tokens: {e}")
        sys.exit(1)

    graduated = sum(1 for t in tokens if t["graduated"])
    bonding = len(tokens) - graduated

    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

    print(f"Saved {len(tokens)} tokens to tokens.json")
    print(f"  Graduated (Uniswap V3): {graduated}")
    print(f"  Bonding curve:          {bonding}")
    print()
    print("To set a price floor for a specific token, edit tokens.json and set")
    print('  "price_floor_usd": <value>  (e.g. 0.00050 for web3guides.com)')


if __name__ == "__main__":
    main()
