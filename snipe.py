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
import getpass
import io
import json
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

    # Art is 100 chars wide × 55 lines. Take every other line → ~27 lines tall.
    # Full width shown — launch.bat sets the CMD window to 120 cols.
    lines = []
    for line in raw[0:55:2]:
        colored = []
        for ch in line:
            if ch in "@B%8WM&#":
                colored.append(green_b(ch))
            elif ch in "dpqwmZOQL0CUY":
                colored.append(yellow(ch))
            elif ch == "$":
                colored.append(dim(ch))
            else:
                colored.append(dim(ch))
        lines.append("".join(colored))
    return lines

BIG_MIKE_TIPS = [
    ("Make a fresh wallet just for this app",
     "Don't use your main wallet. Create a brand new one in MetaMask or Rabby.\n"
     "     If anything ever went wrong, only what's in this wallet is at risk."),
    ("Only fund it with what you're willing to snipe with",
     "Think of it like a poker stack. Put in what you're comfortable with.\n"
     "     Never deposit more than you can afford to lose."),
    ("You can move purchased tokens out any time",
     "After a snipe your tokens sit in this wallet on Doma chain.\n"
     "     Transfer or bridge them to your main wallet whenever you like."),
    ("Your key never leaves your machine",
     "We encrypt it locally with a password you choose. Nobody — not us,\n"
     "     not Doma, not anyone online — can access it."),
    ("Got questions? Join the community",
     "The Doma Discord is the best place for help. Real people, quick answers.\n"
     "     discord.gg/doma"),
]


def _print_banner():
    """Frog art with title header above. Full 100-char width — CMD set to 120 cols by launch.bat."""
    print()
    print(green_b("  D O M A   S N I P E R") + "  " + dim("Community Build v1.0  |  web3guides.com  |  discord.gg/doma"))
    print(dim("  " + "─" * 98))
    print()
    frog_lines = _load_frog()
    for line in frog_lines:
        print(line)
    print()


def _print_compact_header():
    """One-line frog header. Shown on every screen after the welcome."""
    print(green_b("  \\O//") + "  " + bold("DOMA SNIPER") + "  " + dim("v1.0  |  web3guides.com  |  github.com/sdmikecfc/trading-bot"))
    print(green  ("  (^,^)"))
    print(dim    ("  " + "─" * 70))
    print()


def _pause(prompt="  Press Enter to continue (Ctrl+C to exit)... "):
    try:
        input(prompt)
    except KeyboardInterrupt:
        print("\n  Exited.")
        sys.exit(0)

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

PRELOAD_SEC   = 120   # Start tight-polling 2 minutes before scheduled launch
POLL_SEC      = 3     # Poll API every 3 seconds during tight window
GIVE_UP_MIN   = 15    # Give up if token has not gone live 15 minutes after schedule
MAX_UINT256   = 2 ** 256 - 1
KEYSTORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keystore.json")

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


# ── Wallet — keystore onboarding & unlock ─────────────────────────────────────

def _account_from_input(key_raw: str):
    """Parse a private key or seed phrase string into an Account."""
    key_raw = key_raw.strip()
    if " " in key_raw:
        Account.enable_unaudited_hdwallet_features()
        idx = int(os.getenv("MNEMONIC_ACCOUNT_INDEX", "0"))
        return Account.from_mnemonic(key_raw, account_path=f"m/44'/60'/0'/0/{idx}")
    return Account.from_key(key_raw)


def first_run_onboarding() -> tuple[str, str]:
    """
    Three-screen first-run wizard.
    Screen 1 — Welcome banner
    Screen 2 — Big Mike Tips
    Screen 3 — Keystore explanation + key entry + password creation
    Returns (address, private_key_hex).
    """

    # ── Screen 1: Welcome ──────────────────────────────────────────────────────
    os.system("cls" if os.name == "nt" else "clear")
    _print_banner()
    print()
    print(bold("  Welcome to Doma Sniper!"))
    print()
    print("  This tool automatically buys Doma domain tokens the instant a launch")
    print("  goes live — at the floor price, before anyone else.")
    print()
    print("  Before we get started, we need to connect a wallet.")
    print()
    print(dim("  " + "─" * 60))
    print()
    _pause()

    # ── Screen 2: Big Mike Tips ────────────────────────────────────────────────
    os.system("cls" if os.name == "nt" else "clear")
    _print_compact_header()
    print(bold("  Before you connect — a few tips from Big Mike:"))
    print()
    for i, (title, body) in enumerate(BIG_MIKE_TIPS, 1):
        print(f"  {yellow(str(i) + '.')} {bold(title)}")
        print(f"     {dim(body)}")
        print()
    print(dim("  " + "─" * 60))
    print()
    _pause()

    # ── Screen 3: Keystore setup ───────────────────────────────────────────────
    os.system("cls" if os.name == "nt" else "clear")
    _print_compact_header()
    print(bold("  Step 1 — Connect your wallet"))
    print()
    print("  We're going to create a " + bold("private encrypted JSON file") + " that is")
    print("  password protected. That way if someone ever gets access to the file,")
    print("  they won't be able to open it without your password.")
    print()
    print(green("  ✓") + "  Encrypted with AES-256 — the same standard MetaMask uses internally.")
    print(green("  ✓") + "  Your key never leaves this machine.")
    print(green("  ✓") + "  Only your password can unlock it. There is no password reset.")
    print()
    print(dim("  " + "─" * 60))
    print()

    # Get and validate key / seed phrase
    while True:
        try:
            key_raw = getpass.getpass("  Paste your private key or seed phrase (hidden): ")
        except KeyboardInterrupt:
            print("\n  Exited.")
            sys.exit(0)
        if not key_raw.strip():
            print("  Nothing entered — try again.\n")
            continue
        try:
            acct = _account_from_input(key_raw)
            break
        except Exception as e:
            print(f"\n  {red('Invalid key or phrase:')} {e}")
            print("  Double-check it and try again.\n")

    print(f"\n  {green('✓')}  Wallet recognised: {cyan(acct.address)}")
    print()

    # Create and confirm password
    while True:
        try:
            pwd  = getpass.getpass("  Create a keystore password (min 8 characters): ")
            if len(pwd) < 8:
                print("  Password must be at least 8 characters. Try again.\n")
                continue
            pwd2 = getpass.getpass("  Confirm password: ")
        except KeyboardInterrupt:
            print("\n  Exited.")
            sys.exit(0)
        if pwd != pwd2:
            print("  Passwords don't match — try again.\n")
            continue
        break

    # Encrypt and save keystore
    keystore = Account.encrypt(acct.key, pwd)
    with open(KEYSTORE_PATH, "w") as f:
        json.dump(keystore, f, indent=2)

    print()
    print(green_b("  ✓  Keystore saved!"))
    print(dim(f"     {KEYSTORE_PATH}"))
    print()
    print(dim("  You'll only need your password from now on."))
    print(dim("  Keep it somewhere safe — there is no password reset."))
    print()
    _pause("  Press Enter to continue to the launch table... ")

    os.system("cls" if os.name == "nt" else "clear")
    _print_compact_header()
    return acct.address, acct.key.hex()


def unlock_keystore() -> tuple[str, str]:
    """Prompt for password, decrypt existing keystore. Returns (address, key_hex)."""
    with open(KEYSTORE_PATH) as f:
        ks = json.load(f)
    while True:
        try:
            pwd = getpass.getpass("  Keystore password: ")
        except KeyboardInterrupt:
            print("\n  Exited.")
            sys.exit(0)
        try:
            private_key = Account.decrypt(ks, pwd)
            acct        = Account.from_key(private_key)
            return acct.address, acct.key.hex()
        except Exception:
            print(red("  Wrong password — try again."))


def load_wallet() -> tuple[str, str]:
    """
    Keystore present  → unlock with password.
    First run         → full three-screen onboarding wizard, creates keystore.
    .env fallback     → for users who set up before keystore was introduced.
    """
    if os.path.exists(KEYSTORE_PATH):
        return unlock_keystore()

    # Backward-compat: .env credentials present — use them, suggest migration
    mnemonic    = os.getenv("MNEMONIC", "").strip()
    private_key = os.getenv("PRIVATE_KEY", "").strip()
    if mnemonic or private_key:
        try:
            acct = _account_from_input(mnemonic if mnemonic else private_key)
            print(yellow("  Note: using credentials from .env."))
            print(dim   ("  Run snipe.py once with no .env key set to create a keystore."))
            print()
            return acct.address, acct.key.hex()
        except Exception as e:
            print(red(f"  Invalid key in .env: {e}"))
            sys.exit(1)

    # No keystore, no .env — first run
    return first_run_onboarding()


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

def run_countdown(launch_dt: datetime, domain: str,
                  threaded: bool = False,
                  stop_event: threading.Event | None = None):
    """
    Wait until PRELOAD_SEC seconds before launch.

    threaded=True  — used in 'all' mode. Prints a single status line
                     instead of a live \r countdown (multiple threads
                     writing \r simultaneously garbles the terminal).
    stop_event     — if set, the loop exits immediately so the thread
                     can shut down on Ctrl+C.
    """
    preload_time = launch_dt - timedelta(seconds=PRELOAD_SEC)
    now = datetime.now(timezone.utc)

    if preload_time <= now:
        print(f"  [{domain}] Already within preload window — polling now...")
        return

    if threaded:
        remaining   = launch_dt - now
        total_secs  = int(remaining.total_seconds())
        h, rem      = divmod(total_secs, 3600)
        m, _        = divmod(rem, 60)
        print(f"  [{domain}] Waiting — launch at {launch_dt.strftime('%H:%M UTC')}  ({h:02d}h {m:02d}m)", flush=True)
        while datetime.now(timezone.utc) < preload_time:
            if stop_event and stop_event.is_set():
                return
            time.sleep(5)
        print(f"  [{domain}] Entering poll window...", flush=True)
        return

    # Single-launch mode — live countdown
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
             tx_lock: threading.Lock | None = None,
             stop_event: threading.Event | None = None) -> bool:
    """
    Tight-poll loop: query API every POLL_SEC seconds until launchpadAddress
    appears AND launchStatus == 1, then fire the buy.

    tx_lock    — optional lock shared across threads so concurrent snipes
                 don't collide on nonce. Only used in 'all' mode.
    stop_event — if set by Ctrl+C handler, the loop exits immediately.
    """
    domain    = launch["domain"]
    launch_dt = launch["launch_dt"]
    give_up   = launch_dt + timedelta(minutes=GIVE_UP_MIN)
    polls     = 0

    while datetime.now(timezone.utc) < give_up:
        if stop_event and stop_event.is_set():
            return False
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
    # First-run: onboarding wizard handles all screen management internally.
    # Returning users: show compact header here, then password prompt in load_wallet.
    is_first_run = (
        not os.path.exists(KEYSTORE_PATH)
        and not os.getenv("MNEMONIC", "").strip()
        and not os.getenv("PRIVATE_KEY", "").strip()
    )
    if not is_first_run:
        # Show the frog banner briefly on every launch, then settle to compact header
        os.system("cls" if os.name == "nt" else "clear")
        _print_banner()
        time.sleep(2)
        os.system("cls" if os.name == "nt" else "clear")
        _print_compact_header()

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

        # Shared primitives for all threads
        tx_lock    = threading.Lock()   # serialises approve+nonce+send
        stop_event = threading.Event()  # set on Ctrl+C to kill threads
        results: dict[str, bool] = {}

        def _snipe_one(lx: dict):
            run_countdown(lx["launch_dt"], lx["domain"],
                          threaded=True, stop_event=stop_event)
            if stop_event.is_set():
                return
            ok = do_snipe(w3, wallet, private_key, usdce, usdce_decimals,
                          lx, amount_per, tx_lock=tx_lock, stop_event=stop_event)
            results[lx["domain"]] = ok

        threads = [threading.Thread(target=_snipe_one, args=(lx,), daemon=True)
                   for lx in launches]
        print()
        for t in threads:
            t.start()

        print(dim("  Running — press Ctrl+C to cancel all.\n"))
        try:
            # Join with a short timeout so the main thread wakes up regularly
            # and can catch Ctrl+C — plain t.join() blocks signals on Windows.
            while any(t.is_alive() for t in threads):
                for t in threads:
                    t.join(timeout=0.5)
        except KeyboardInterrupt:
            print(f"\n\n  {yellow('Stopping all snipes...')}")
            stop_event.set()
            for t in threads:
                t.join(timeout=10)
            print("  Stopped.")
            sys.exit(0)

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
