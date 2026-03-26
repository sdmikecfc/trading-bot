#!/usr/bin/env python3
"""
snipe.py — Doma Protocol Interactive Sniper
============================================
Shows upcoming token launches from the public Doma launch schedule,
lets you pick one, enter how much USDC.e to buy with, then
automatically executes the buy the instant the bonding curve goes live.

No configuration required beyond your wallet credentials.
The launch schedule is read from the public Google Sheet.

Usage:
    python snipe.py

Requirements:
    pip install -r requirements.txt
"""

import contextlib
import csv
import io
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta

# Force UTF-8 output on Windows (handles box-drawing and emoji characters)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Terminal colours (ANSI — works on Windows 10+, macOS, Linux) ─────────────

def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    if not _supports_color():
        return text
    return f"\033[{code}m{text}\033[0m"

def green(t):   return _c("32",   t)
def yellow(t):  return _c("33",   t)
def red(t):     return _c("31",   t)
def cyan(t):    return _c("36",   t)
def bold(t):    return _c("1",    t)
def dim(t):     return _c("2",    t)
def green_b(t): return _c("1;32", t)
def red_b(t):   return _c("1;31", t)


# ── Intro screen ──────────────────────────────────────────────────────────────

def _load_frog() -> list[str]:
    """Load frog.txt from the script's directory, crop and colorize each line."""
    frog_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frog.txt")
    try:
        with open(frog_path, encoding="utf-8", errors="replace") as f:
            raw = f.read().splitlines()
    except FileNotFoundError:
        return []

    # File lines 12–76 (0-indexed 11–75) contain the frog figure.
    # Strip 30 leading '+' background chars, show 100 chars → fits ~80-col terminals.
    lines = []
    for line in raw[11:76]:
        cropped = line[30:130]  # 100-char window centred on the frog
        colored = []
        for ch in cropped:
            if ch in "@%#":
                colored.append(green_b(ch))
            elif ch in "*=":
                colored.append(yellow(ch))
            else:
                colored.append(dim(ch))
        lines.append("".join(colored))
    return lines

WARNINGS = [
    ("USE A NEW WALLET",
     "Create a fresh wallet just for sniping. Never use your main wallet."),
    ("ONLY FUND WHAT YOU'RE WILLING TO LOSE",
     "Only deposit the amount you plan to snipe with. Nothing more."),
    ("THIS IS A COMMUNITY TOOL",
     "Built by web3guides.com — not officially affiliated with Doma Protocol."),
    ("CRYPTO IS RISKY",
     "Token prices can go up and down. This is not financial advice."),
    ("REVIEW YOUR .env FILE",
     "Make sure DRY_RUN=false only when you are ready to trade real funds."),
]


def show_intro():
    os.system("cls" if os.name == "nt" else "clear")

    frog_lines = _load_frog()

    # Title panel lines (right side), padded to 44 chars
    title_panel = [
        dim("┌──────────────────────────────────────────┐"),
        dim("│") + "                                          " + dim("│"),
        dim("│") + "  " + bold("D O M A   S N I P E R") + "              " + dim("│"),
        dim("│") + "  " + cyan("Community Build  v1.0") + "               " + dim("│"),
        dim("│") + "                                          " + dim("│"),
        dim("│") + "  " + cyan("by web3guides.com") + "                   " + dim("│"),
        dim("│") + "  " + dim("github.com/sdmikecfc/trading-bot") + "  " + dim("│"),
        dim("│") + "                                          " + dim("│"),
        dim("└──────────────────────────────────────────┘"),
    ]

    if frog_lines:
        # Print frog lines, tuck the title panel alongside lines 10-18
        panel_start = max(0, len(frog_lines) // 2 - len(title_panel) // 2)
        for i, fline in enumerate(frog_lines):
            pi = i - panel_start
            if 0 <= pi < len(title_panel):
                print("  " + fline + "  " + title_panel[pi])
            else:
                print("  " + fline)
    else:
        # Fallback if frog.txt not found
        for line in title_panel:
            print("  " + line)

    print()
    print(bold("  ⚠  PLEASE READ BEFORE CONTINUING"))
    print()

    for i, (title, body) in enumerate(WARNINGS, 1):
        print(f"  {yellow(str(i) + '.')} {bold(title)}")
        print(f"     {dim(body)}")
        print()

    print(dim("  " + "─" * 60))
    print()
    try:
        input("  Press Enter to continue (Ctrl+C to exit)... ")
    except KeyboardInterrupt:
        print("\n\n  Exited.")
        sys.exit(0)

    os.system("cls" if os.name == "nt" else "clear")
    # Compact persistent header shown on every screen after intro
    print(green_b("  \\O//") + "  " + bold("DOMA SNIPER") + "  " + dim("v1.0  |  web3guides.com  |  github.com/sdmikecfc/trading-bot"))
    print(green  ("  (^,^)"))
    print(dim    ("  " + "─" * 70))
    print()

import requests
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

load_dotenv()

# ── Network constants (Doma chain — do not change) ────────────────────────────

SHEET_URL  = "https://docs.google.com/spreadsheets/d/1p5TvCYo7ZQvCkSyxPJWor_GIrRCu-5l1RRMCEv3THm0/export?format=csv"
API_URL    = "https://api.doma.xyz/graphql"
API_KEY    = os.getenv("DOMA_API_KEY", "v1.c6e3f41019fb97237b7f192d49adb2ae464f2ba7ca6c0737fd6eab71ee01d1d4")
RPC_URL    = os.getenv("RPC_URL", "https://rpc.doma.xyz")
CHAIN_ID   = int(os.getenv("CHAIN_ID", "97477"))
USDCE_ADDR = os.getenv("USDCE_ADDRESS", "0x31EEf89D5215C305304a2fA5376a1f1b6C5dc477")

PRELOAD_SEC = 120   # Start tight-polling 2 minutes before scheduled launch
POLL_SEC    = 3     # Poll API every 3 seconds during tight window
GIVE_UP_MIN = 15    # Give up if token has not gone live 15 minutes after schedule
MAX_UINT256 = 2 ** 256 - 1

# ── ABIs ──────────────────────────────────────────────────────────────────────

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]

LAUNCHPAD_ABI = [
    {"name": "buy", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "quoteAmount", "type": "uint256"},
                {"name": "minTokenAmount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}]},
    {"name": "launchStatus", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
]


# ── Sheet parsing ─────────────────────────────────────────────────────────────

def parse_launch_dt(date_str: str, time_str: str):
    """Parse date strings in both '2026-03-24' and 'March 24, 2026' formats."""
    date_str = date_str.strip()
    time_str = time_str.strip()
    if not date_str or not time_str or ":" not in time_str:
        return None
    try:
        h, m = time_str.split(":")
        time_padded = f"{int(h):02d}:{int(m):02d}"
    except ValueError:
        return None
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(
                f"{date_str} {time_padded}", f"{fmt} %H:%M"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fetch_launches(window_hours: int = 24) -> list[dict]:
    """Download the public Google Sheet and return upcoming launches sorted by time."""
    try:
        resp = requests.get(SHEET_URL, timeout=20, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        print(f"\n  ERROR: Could not fetch launch schedule: {e}")
        sys.exit(1)

    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=window_hours)
    launches = []

    for row in csv.DictReader(io.StringIO(resp.text)):
        domain = row.get("Domain", "").strip()
        date_s = row.get("Launch Date", "").strip()
        time_s = row.get("Launch Time (UTC)", "").strip()
        status = row.get("Status", "").strip().lower()

        if not domain or not date_s or not time_s:
            continue
        if status and status not in ("live", "upcoming", "scheduled"):
            continue

        launch_dt = parse_launch_dt(date_s, time_s)
        if launch_dt is None or not (now <= launch_dt <= cutoff):
            continue

        try:
            initial_fdv = float(row.get("Starting FDV") or 0)
        except ValueError:
            initial_fdv = 0
        try:
            bonding_fdv = float(row.get("Bonding FDV") or 0)
        except ValueError:
            bonding_fdv = 0
        try:
            buyout = float(row.get("Buyout") or row.get("Bonding FDV") or 0)
        except ValueError:
            buyout = bonding_fdv

        launches.append({
            "domain":      domain,
            "launch_dt":   launch_dt,
            "initial_fdv": initial_fdv,
            "bonding_fdv": bonding_fdv,
            "buyout":      buyout,
        })

    return sorted(launches, key=lambda x: x["launch_dt"])


# ── Table display ─────────────────────────────────────────────────────────────

def print_table(launches: list[dict]):
    if not launches:
        print("\n  No launches found in the next 24 hours. Check back soon!")
        sys.exit(0)

    col_w = [4, 28, 10, 13, 12, 10, 7]
    divider = "  +" + "+".join("-" * (w + 2) for w in col_w) + "+"

    def fmt_row(*cells):
        parts = []
        for i, cell in enumerate(cells):
            parts.append(f" {str(cell):<{col_w[i]}} ")
        return "  |" + "|".join(parts) + "|"

    print(f"\n  Upcoming launches — next 24 hours\n")
    print(divider)
    print(fmt_row("#", "Domain", "Launch UTC", "Starting FDV", "Bonding FDV", "Buyout", "Spread"))
    print(divider)

    for i, launch in enumerate(launches, 1):
        fdv_s    = ("$" + f"{launch['initial_fdv']:,.0f}") if launch["initial_fdv"] else "N/A"
        bfv_s    = ("$" + f"{launch['bonding_fdv']:,.0f}") if launch["bonding_fdv"] else "N/A"
        buyout_s = ("$" + f"{launch['buyout']:,.0f}") if launch.get("buyout") else "N/A"
        if launch["initial_fdv"] > 0 and launch["bonding_fdv"] > 0:
            spread = launch["bonding_fdv"] / launch["initial_fdv"]
            spr_s  = green(f"{spread:.1f}x") if spread >= 1.5 else f"{spread:.1f}x"
        else:
            spr_s  = "N/A"
        print(fmt_row(
            i,
            launch["domain"][:28],
            launch["launch_dt"].strftime("%H:%M"),
            fdv_s,
            bfv_s,
            buyout_s,
            spr_s,
        ))

    print(divider)
    print()


# ── User interaction ──────────────────────────────────────────────────────────

def pick_launch(launches: list[dict]):
    """Return a single launch dict, or the string 'all'."""
    while True:
        choice = input("  Pick a launch (number, domain name, or 'all'): ").strip()
        if choice.lower() == "all":
            return "all"
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(launches):
                return launches[idx]
            print(f"  Please enter a number between 1 and {len(launches)}, a domain name, or 'all'.")
        else:
            matches = [l for l in launches if l["domain"].lower() == choice.lower()]
            if matches:
                return matches[0]
            print(f"  '{choice}' not found. Try again, or type 'all' to snipe every launch.")


def pick_amount_for_all(launches: list[dict], usdce_balance: float) -> float:
    """Ask for a per-launch amount and validate total against balance."""
    n = len(launches)
    print(f"  Your USDC.e balance: ${usdce_balance:.4f}")
    print(f"  Sniping all {n} launch{'es' if n != 1 else ''}.")
    while True:
        raw = input("  How much USDC.e per launch? $").strip()
        try:
            amount = float(raw)
        except ValueError:
            print("  Please enter a number (e.g. 1.00).")
            continue
        if amount <= 0:
            print("  Amount must be greater than 0.")
            continue
        total = amount * n
        if total > usdce_balance:
            print(f"  Total would be ${total:.2f}  ({n} × ${amount:.2f})  but you only have ${usdce_balance:.4f}.")
            continue
        return amount


def pick_amount(usdce_balance: float) -> float:
    print(f"  Your USDC.e balance: ${usdce_balance:.4f}")
    while True:
        raw = input("  How much USDC.e to snipe with? $").strip()
        try:
            amount = float(raw)
        except ValueError:
            print("  Please enter a number (e.g. 5.00).")
            continue
        if amount <= 0:
            print("  Amount must be greater than 0.")
            continue
        if amount > usdce_balance:
            print(f"  Insufficient balance. You have ${usdce_balance:.4f} available.")
            continue
        return amount


# ── Wallet ────────────────────────────────────────────────────────────────────

def _wallet_setup_help(reason: str = ""):
    """Print a beginner-friendly wallet setup guide and exit."""
    print()
    print(red_b("  WALLET NOT FOUND" + (f" — {reason}" if reason else "")))
    print()
    print("  No wallet credentials were found in your .env file.")
    print("  Here's how to set it up in under 2 minutes:\n")
    print(bold("  Step 1 ›") + " Find the file called " + cyan(".env") + " in the same folder as snipe.py")
    print(dim  ("           (If it doesn't exist, rename .env.example to .env)"))
    print()
    print(bold("  Step 2 ›") + " Open .env in any text editor  " + dim("(Notepad works fine)"))
    print()
    print(bold("  Step 3 ›") + " Add one of these lines:\n")
    print(dim  ("           Option A — Private key (starts with 0x):"))
    print(cyan ("              PRIVATE_KEY=0xabc123...your64hexcharshere"))
    print()
    print(dim  ("           Option B — 12-word seed phrase:"))
    print(cyan ("              MNEMONIC=word1 word2 word3 word4 word5 word6 word7 word8 word9 word10 word11 word12"))
    print()
    print(bold("  Step 4 ›") + " Save the file, then run snipe.py again.\n")
    print(dim  ("  " + "─" * 62))
    print()
    print(bold("  Is it safe to put my key in a file?") + "  " + green_b("Yes — here's why:"))
    print()
    print(green("  ✓") + "  This app runs " + bold("100% on your own computer.") + " Nothing is uploaded.")
    print(green("  ✓") + "  Your key is used only to " + bold("sign transactions locally."))
    print(green("  ✓") + "  It is " + bold("NEVER sent") + " to any server, API, or website.")
    print(green("  ✓") + "  The .env file is blocked from Git — it cannot be accidentally shared.")
    print(green("  ✓") + "  Use a " + bold("dedicated wallet") + " with only the funds you plan to trade.")
    print()
    print(dim  ("  Need help? → https://web3guides.com/doma"))
    print()
    sys.exit(1)


def load_wallet():
    mnemonic    = os.getenv("MNEMONIC", "").strip()
    private_key = os.getenv("PRIVATE_KEY", "").strip()

    if not mnemonic and not private_key:
        _wallet_setup_help()

    try:
        if mnemonic:
            Account.enable_unaudited_hdwallet_features()
            index = int(os.getenv("MNEMONIC_ACCOUNT_INDEX", "0"))
            acct  = Account.from_mnemonic(mnemonic, account_path=f"m/44'/60'/0'/0/{index}")
        else:
            acct = Account.from_key(private_key)
    except Exception as e:
        _wallet_setup_help(reason=str(e))

    return acct.address, acct.key.hex()


# ── Doma API ──────────────────────────────────────────────────────────────────

def query_token(domain: str) -> dict | None:
    """Query Doma GraphQL API for a specific domain. Returns token dict or None."""
    headers = {"Api-Key": API_KEY, "Content-Type": "application/json"}

    # Try name-filtered query first
    gql = '{ fractionalTokens(name: "%s") { items { address name graduatedAt poolAddress launchpadAddress priceUsd initialFDV bondingFDV } } }' % domain
    try:
        resp = requests.post(API_URL, json={"query": gql}, headers=headers, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("data", {}).get("fractionalTokens", {}).get("items", [])
        for item in items:
            if item.get("name", "").lower() == domain.lower():
                return item
        if len(items) == 1:
            return items[0]
    except Exception:
        pass

    # Fallback: scan first page (newest tokens appear here)
    gql2 = '{ fractionalTokens(page: 1) { items { address name graduatedAt poolAddress launchpadAddress priceUsd initialFDV bondingFDV } } }'
    try:
        resp = requests.post(API_URL, json={"query": gql2}, headers=headers, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("data", {}).get("fractionalTokens", {}).get("items", [])
        for item in items:
            if item.get("name", "").lower() == domain.lower():
                return item
    except Exception:
        pass

    return None


# ── Transaction helpers ───────────────────────────────────────────────────────

def send_tx(w3, tx: dict, private_key: str, label: str):
    signed  = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
    return receipt, tx_hash


def ensure_allowance(w3, wallet: str, private_key: str, usdce, spender: str, gas_price: int):
    """Approve the launchpad to spend USDC.e if not already approved."""
    current = usdce.functions.allowance(wallet, spender).call()
    if current >= MAX_UINT256 // 2:
        return  # Already approved

    print("\n  Approving USDC.e for this launchpad (one-time per launch)...")
    nonce = w3.eth.get_transaction_count(wallet)
    try:
        gas_est   = usdce.functions.approve(spender, MAX_UINT256).estimate_gas({"from": wallet})
        gas_limit = int(gas_est * 1.5)
    except Exception:
        gas_limit = 80_000

    tx = usdce.functions.approve(spender, MAX_UINT256).build_transaction({
        "from": wallet, "nonce": nonce, "gas": gas_limit,
        "gasPrice": gas_price, "chainId": CHAIN_ID,
    })
    receipt, _ = send_tx(w3, tx, private_key, "APPROVE")
    if receipt.status != 1:
        print("  ERROR: Approval transaction failed. Cannot proceed.")
        sys.exit(1)
    print("  Approval confirmed.\n")


# ── Countdown ─────────────────────────────────────────────────────────────────

def run_countdown(launch_dt: datetime, domain: str):
    """Display a live countdown until PRELOAD_SEC seconds before launch."""
    preload_time = launch_dt - timedelta(seconds=PRELOAD_SEC)
    now = datetime.now(timezone.utc)

    if preload_time <= now:
        print(f"\n  Already within preload window — starting poll now...")
        return

    print(f"\n  Waiting for {domain} to go live at {launch_dt.strftime('%H:%M UTC')}")
    print("  Press Ctrl+C to cancel.\n")

    try:
        while True:
            now = datetime.now(timezone.utc)
            if now >= preload_time:
                break
            remaining    = launch_dt - now
            total_secs   = int(remaining.total_seconds())
            h, remainder = divmod(total_secs, 3600)
            m, s         = divmod(remainder, 60)
            print(f"  \r  Countdown: {h:02d}h {m:02d}m {s:02d}s until launch   ", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n  Cancelled.")
        sys.exit(0)

    print(f"\n\n  Entering tight-poll window ({POLL_SEC}s intervals)...")


# ── Core snipe ────────────────────────────────────────────────────────────────

def do_snipe(w3, wallet: str, private_key: str, usdce, usdce_decimals: int,
             launch: dict, amount_usd: float,
             tx_lock: threading.Lock | None = None) -> bool:
    """
    Tight-poll loop: query API every POLL_SEC seconds until launchpadAddress
    appears AND launchStatus == 1, then fire the buy.

    tx_lock — optional lock shared across threads so concurrent snipes don't
               collide on nonce. Only used in 'all' mode.
    """
    domain    = launch["domain"]
    launch_dt = launch["launch_dt"]
    give_up   = launch_dt + timedelta(minutes=GIVE_UP_MIN)
    polls     = 0

    while datetime.now(timezone.utc) < give_up:
        polls += 1
        now_s = datetime.now(timezone.utc).strftime("%H:%M:%S")

        token_data = query_token(domain)

        if token_data is None:
            print(f"  [{now_s}] Poll {polls}: not in API yet...", flush=True)
            time.sleep(POLL_SEC)
            continue

        if token_data.get("poolAddress"):
            print(f"\n  [{now_s}] Token already graduated — missed the bonding curve window.")
            return False

        launchpad_addr = token_data.get("launchpadAddress")
        if not launchpad_addr:
            print(f"  [{now_s}] Poll {polls}: found in API, waiting for launchpadAddress...", flush=True)
            time.sleep(POLL_SEC)
            continue

        # launchpadAddress exists — check on-chain status
        try:
            lp = w3.eth.contract(address=Web3.to_checksum_address(launchpad_addr), abi=LAUNCHPAD_ABI)
            status = lp.functions.launchStatus().call()
        except Exception as e:
            print(f"  [{now_s}] Poll {polls}: status check failed ({e}), retrying...", flush=True)
            time.sleep(POLL_SEC)
            continue

        if status != 1:
            print(f"  [{now_s}] Poll {polls}: launchpad found, waiting for active (status={status})...", flush=True)
            time.sleep(POLL_SEC)
            continue

        # Status is 1 — FIRE
        print(f"\n  [{now_s}] {bold(domain)} ACTIVE! launchpad={launchpad_addr}")
        print(f"  Executing buy of ${amount_usd:.2f} USDC.e for {bold(domain)}...")

        # Serialise the approve + nonce + send across concurrent threads
        with (tx_lock if tx_lock else contextlib.nullcontext()):
            gas_price = w3.eth.gas_price
            ensure_allowance(w3, wallet, private_key, usdce, Web3.to_checksum_address(launchpad_addr), gas_price)

            amount_raw = int(amount_usd * 10 ** usdce_decimals)
            nonce = w3.eth.get_transaction_count(wallet, "pending")
            try:
                gas_est   = lp.functions.buy(amount_raw, 0).estimate_gas({"from": wallet})
                gas_limit = int(gas_est * 1.5)
            except Exception:
                gas_limit = 300_000

            tx = lp.functions.buy(amount_raw, 0).build_transaction({
                "from": wallet, "nonce": nonce, "gas": gas_limit,
                "gasPrice": gas_price, "chainId": CHAIN_ID,
            })

            fire_time = datetime.now(timezone.utc)
            receipt, tx_hash = send_tx(w3, tx, private_key, "BUY")

        elapsed        = (datetime.now(timezone.utc) - fire_time).total_seconds()
        after_schedule = (datetime.now(timezone.utc) - launch_dt).total_seconds()

        if receipt.status == 1:
            print(f"\n  {green_b('✓ BUY CONFIRMED')}  {bold(domain)}")
            print(f"  Amount        : {green_b('$' + f'{amount_usd:.2f}')} USDC.e")
            print(f"  Block         : {receipt.blockNumber}")
            print(f"  Confirmed in  : {elapsed:.1f}s")
            if after_schedule > 0:
                print(f"  After launch  : {after_schedule:.0f}s")
            print(f"  Explorer      : {dim('https://explorer.doma.xyz/tx/' + tx_hash.hex())}")
            print()
            return True
        else:
            print(f"\n  {red_b('✗ BUY FAILED (status=0)')}  {bold(domain)}")
            print(f"  Explorer: {dim('https://explorer.doma.xyz/tx/' + tx_hash.hex())}")
            return False

    print(f"\n  Gave up waiting — {domain} did not go live within {GIVE_UP_MIN} minutes of schedule.")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    show_intro()

    # Connect
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print(f"  ERROR: Cannot connect to {RPC_URL}")
        sys.exit(1)

    # Load wallet
    wallet, private_key = load_wallet()
    usdce_addr = Web3.to_checksum_address(USDCE_ADDR)
    usdce      = w3.eth.contract(address=usdce_addr, abi=ERC20_ABI)

    try:
        usdce_decimals = usdce.functions.decimals().call()
        bal_raw        = usdce.functions.balanceOf(wallet).call()
        usdce_balance  = bal_raw / 10 ** usdce_decimals
        eth_balance    = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
    except Exception as e:
        print(f"  ERROR: Could not read wallet balances: {e}")
        sys.exit(1)

    print(f"  Wallet  : {cyan(wallet)}")
    print(f"  USDC.e  : {green_b('$' + f'{usdce_balance:.4f}')}")
    print(f"  ETH     : {eth_balance:.6f} {dim('(for gas)')}")
    print(f"  Chain   : {w3.eth.chain_id}")
    print()

    if usdce_balance < 0.01:
        print(red_b("  ERROR: USDC.e balance too low. Bridge USDC.e to Doma chain first."))
        sys.exit(1)

    if eth_balance < 0.0001:
        print(yellow("  WARNING: ETH balance very low — you may not have enough for gas."))

    # Fetch launches
    print("  Fetching upcoming launches...", end=" ", flush=True)
    launches = fetch_launches(window_hours=24)
    print(f"found {len(launches)}.")

    # Display table and pick
    print_table(launches)
    selection = pick_launch(launches)

    # ── ALL mode ──────────────────────────────────────────────────────────────
    if selection == "all":
        print()
        amount_per = pick_amount_for_all(launches, usdce_balance)
        total_spend = amount_per * len(launches)

        # Summary box
        print()
        print(f"  ┌─────────────────────────────────────────┐")
        print(f"  │  SNIPE ALL SUMMARY                      │")
        print(f"  │                                         │")
        for lx in launches:
            line = f"{lx['domain'][:20]:<20}  {lx['launch_dt'].strftime('%H:%M UTC')}"
            print(f"  │    {line:<37}│")
        print(f"  │                                         │")
        print(f"  │  Per launch : ${amount_per:<27.2f}│")
        print(f"  │  Launches   : {len(launches):<27} │")
        print(f"  │  Total      : {green_b('$' + f'{total_spend:.2f}'):<38}│")
        print(f"  └─────────────────────────────────────────┘")
        print()
        confirm = input("  Confirm? (yes/no): ").strip().lower()
        if confirm not in ("yes", "y"):
            print("  Cancelled.")
            sys.exit(0)

        # Shared lock — serialises approve+nonce+send across threads
        tx_lock = threading.Lock()
        results: dict[str, bool] = {}

        def _snipe_one(lx: dict):
            run_countdown(lx["launch_dt"], lx["domain"])
            ok = do_snipe(w3, wallet, private_key, usdce, usdce_decimals,
                          lx, amount_per, tx_lock=tx_lock)
            results[lx["domain"]] = ok

        threads = [threading.Thread(target=_snipe_one, args=(lx,), daemon=True)
                   for lx in launches]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Final report
        print()
        print(bold("  ── Results ─────────────────────────────────"))
        wins = 0
        for lx in launches:
            ok = results.get(lx["domain"], False)
            icon = green("  ✓") if ok else red("  ✗")
            print(f"{icon}  {lx['domain']}")
            if ok:
                wins += 1
        print()
        if wins == len(launches):
            print(green_b("  All snipes confirmed! Tokens are in your wallet."))
        elif wins > 0:
            print(yellow(f"  {wins}/{len(launches)} snipes confirmed."))
        else:
            print(red("  No tokens were purchased."))
        print(dim("  Monitor your positions at https://doma.xyz"))
        sys.exit(0 if wins > 0 else 1)

    # ── SINGLE mode ───────────────────────────────────────────────────────────
    launch = selection
    print(f"\n  Selected: {launch['domain']} — {launch['launch_dt'].strftime('%Y-%m-%d %H:%M UTC')}")
    if launch["initial_fdv"]:
        spread = (launch["bonding_fdv"] / launch["initial_fdv"]) if launch["initial_fdv"] > 0 else 0
        print(f"  Starting FDV: ${launch['initial_fdv']:,.0f}  |  Bonding FDV: ${launch['bonding_fdv']:,.0f}  |  Spread: {spread:.1f}x")

    # Pick amount
    print()
    amount_usd = pick_amount(usdce_balance)

    # Confirm
    print()
    print(f"  ┌─────────────────────────────────────────┐")
    print(f"  │  SNIPE SUMMARY                          │")
    print(f"  │                                         │")
    print(f"  │  Domain  : {launch['domain']:<29} │")
    print(f"  │  Launch  : {launch['launch_dt'].strftime('%H:%M UTC'):<29} │")
    print(f"  │  Amount  : ${amount_usd:<28.2f} │")
    print(f"  └─────────────────────────────────────────┘")
    print()
    confirm = input("  Confirm? (yes/no): ").strip().lower()
    if confirm not in ("yes", "y"):
        print("  Cancelled.")
        sys.exit(0)

    # Countdown then snipe
    run_countdown(launch["launch_dt"], launch["domain"])
    success = do_snipe(w3, wallet, private_key, usdce, usdce_decimals, launch, amount_usd)

    if success:
        print(green_b("  Your tokens are in your wallet."))
        print(green  ("  The bonding curve price can only go up from here."))
        print(dim    ("  Monitor your position at https://doma.xyz"))
    else:
        print(red("  No tokens were purchased."))

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
