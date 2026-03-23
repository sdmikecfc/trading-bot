"""
Doma Protocol Sniper

Two jobs:
  1. NEW LAUNCH DETECTION — polls the API every SNIPE_POLL_SEC seconds,
     spots tokens that just appeared on the bonding curve, evaluates them,
     and auto-buys if they meet criteria.

  2. GRADUATION MONITORING — watches positions.json for sniped tokens that
     have since graduated to Uniswap V3, then sells GRADUATION_SELL_PERCENT
     of the position to lock in profit.

Snipe criteria (from the performance data):
  - initialFDV <= SNIPE_MAX_INITIAL_FDV  (low BIN range = 95% graduation rate)
  - bondingFDV >= initialFDV * SNIPE_MIN_SPREAD  (meaningful profit spread)
  - Token has never been seen before (genuinely new launch)
  - Launchpad is active

Run alongside bot.py in a separate Command Prompt window:
    python sniper.py

State files (auto-generated, not committed to git):
    seen_tokens.json   — every token address ever observed, to detect new launches
    positions.json     — all sniped positions and their current state
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

import requests
from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_account import Account
from web3 import Web3

import config

# ── File paths ───────────────────────────────────────────────
BASE = os.path.dirname(__file__)
SEEN_FILE = os.path.join(BASE, "seen_tokens.json")
POSITIONS_FILE = os.path.join(BASE, "positions.json")

MAX_UINT256 = 2 ** 256 - 1

# ── ABIs ─────────────────────────────────────────────────────

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
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]

LAUNCHPAD_ABI = [
    {"name": "buy", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "quoteAmount", "type": "uint256"},
                {"name": "minTokenAmount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}]},
    {"name": "sell", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "tokenAmount", "type": "uint256"},
                {"name": "minQuoteAmount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}]},
    {"name": "launchStatus", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "getAvailableTokensToBuy", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
]

POOL_ABI = [
    {"name": "slot0", "type": "function", "stateMutability": "view",
     "inputs": [],
     "outputs": [{"name": "sqrtPriceX96", "type": "uint160"}, {"name": "tick", "type": "int24"},
                 {"name": "observationIndex", "type": "uint16"}, {"name": "observationCardinality", "type": "uint16"},
                 {"name": "observationCardinalityNext", "type": "uint16"}, {"name": "feeProtocol", "type": "uint8"},
                 {"name": "unlocked", "type": "bool"}]},
    {"name": "token0", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"name": "token1", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"name": "fee", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint24"}]},
]

ROUTER_ABI = [
    {"name": "execute", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "commands", "type": "bytes"},
                {"name": "inputs", "type": "bytes[]"},
                {"name": "deadline", "type": "uint256"}],
     "outputs": []},
]

# ── API ──────────────────────────────────────────────────────

GRAPHQL_QUERY = """
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


def fetch_all_tokens_api() -> list[dict]:
    all_items = []
    page = 1
    total_pages = None
    headers = {"Api-Key": config.DOMA_API_KEY, "Content-Type": "application/json"}
    while True:
        resp = requests.post(
            config.DOMA_API_URL,
            json={"query": GRAPHQL_QUERY % page},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data["data"]["fractionalTokens"]
        total_pages = result["totalPages"]
        all_items.extend(result["items"])
        if page >= total_pages:
            break
        page += 1
    return all_items


# ── Logging ──────────────────────────────────────────────────

def log(level, msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [SNIPER] [{level}] {msg}", flush=True)


def info(msg):  log("INFO ", msg)
def warn(msg):  log("WARN ", msg)
def err(msg):   log("ERROR", msg)


# ── State helpers ────────────────────────────────────────────

def load_seen() -> set:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE) as f:
        return set(json.load(f))


def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def load_positions() -> list[dict]:
    if not os.path.exists(POSITIONS_FILE):
        return []
    with open(POSITIONS_FILE) as f:
        return json.load(f)


def save_positions(positions: list[dict]):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)


# ── Wallet / Web3 ─────────────────────────────────────────────

def load_wallet():
    if config.MNEMONIC:
        Account.enable_unaudited_hdwallet_features()
        acct = Account.from_mnemonic(
            config.MNEMONIC,
            account_path=f"m/44'/60'/0'/0/{config.MNEMONIC_ACCOUNT_INDEX}",
        )
    else:
        acct = Account.from_key(config.PRIVATE_KEY)
    return acct.address, acct.key.hex()


def connect() -> Web3:
    w3 = Web3(Web3.HTTPProvider(config.RPC_URL))
    if not w3.is_connected():
        err(f"Cannot connect to RPC: {config.RPC_URL}")
        sys.exit(1)
    return w3


# ── TX helpers ───────────────────────────────────────────────

def send_tx(w3, tx, private_key, label):
    try:
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        info(f"{label} tx: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
        if receipt.status != 1:
            err(f"{label} FAILED (status=0)")
            return None
        info(f"{label} confirmed in block {receipt.blockNumber}")
        return receipt
    except Exception as e:
        err(f"{label} exception: {e}")
        return None


def ensure_allowance(w3, wallet, private_key, token_contract, spender, label, gas_price):
    current = token_contract.functions.allowance(wallet, spender).call()
    if current >= MAX_UINT256 // 2:
        return True
    info(f"Approving {label}...")
    if config.DRY_RUN:
        return True
    nonce = w3.eth.get_transaction_count(wallet)
    try:
        gas_est = token_contract.functions.approve(spender, MAX_UINT256).estimate_gas({"from": wallet})
        gas_limit = int(gas_est * 1.5)
    except Exception:
        gas_limit = 80_000
    tx = token_contract.functions.approve(spender, MAX_UINT256).build_transaction({
        "from": wallet, "nonce": nonce, "gas": gas_limit,
        "gasPrice": gas_price, "chainId": config.CHAIN_ID,
    })
    return send_tx(w3, tx, private_key, f"Approve {label}") is not None


# ── Snipe execution ───────────────────────────────────────────

def execute_snipe(w3, wallet, private_key, usdce, token_data: dict,
                  usdce_decimals: int, daily_spent: float, gas_price: int) -> float:
    """Buy on bonding curve. Returns USD amount spent, or 0 on failure."""
    name = token_data["name"]
    launchpad_addr = Web3.to_checksum_address(token_data["launchpadAddress"])
    launchpad = w3.eth.contract(address=launchpad_addr, abi=LAUNCHPAD_ABI)

    # Check launchpad is still active
    try:
        status = launchpad.functions.launchStatus().call()
        if status != 1:
            warn(f"{name}: launchpad not active (status={status})")
            return 0
    except Exception as e:
        warn(f"{name}: could not check status: {e}")

    # Check tokens still available
    try:
        available = launchpad.functions.getAvailableTokensToBuy().call()
        if available == 0:
            warn(f"{name}: no tokens left to buy")
            return 0
    except Exception:
        pass

    # Cap to remaining daily budget
    remaining = config.SNIPE_DAILY_LIMIT - daily_spent
    amount_usd = min(config.SNIPE_AMOUNT_USD, remaining)
    if amount_usd < 0.01:
        warn("Snipe daily limit reached")
        return 0

    amount_in_raw = int(amount_usd * 10 ** usdce_decimals)
    price = token_data.get("priceUsd", 0)
    token_addr = Web3.to_checksum_address(token_data["address"])
    token_contract = w3.eth.contract(address=token_addr, abi=ERC20_ABI)
    try:
        token_decimals = token_contract.functions.decimals().call()
    except Exception:
        token_decimals = 18

    expected_tokens = (amount_usd / price) if price > 0 else 0
    slippage = 1.0 - config.SLIPPAGE_PERCENT / 100.0
    min_out = int(expected_tokens * 10 ** token_decimals * slippage)

    info(f"SNIPE {name} — spending ${amount_usd:.2f} USDC.e on bonding curve")
    info(f"  initialFDV=${token_data.get('initialFDV')}  bondingFDV=${token_data.get('bondingFDV')}  "
         f"spread={token_data.get('bondingFDV',0)/max(token_data.get('initialFDV',1),1):.1f}x")

    if config.DRY_RUN:
        info(f"[DRY RUN] Would call launchpad.buy({amount_in_raw}, {min_out})")
        return amount_usd

    # Approve USDC.e for launchpad
    if not ensure_allowance(w3, wallet, private_key, usdce, launchpad_addr, "USDC.e→launchpad", gas_price):
        return 0

    nonce = w3.eth.get_transaction_count(wallet)
    try:
        gas_est = launchpad.functions.buy(amount_in_raw, min_out).estimate_gas({"from": wallet})
        gas_limit = int(gas_est * 1.5)
    except Exception:
        gas_limit = 300_000

    tx = launchpad.functions.buy(amount_in_raw, min_out).build_transaction({
        "from": wallet, "nonce": nonce, "gas": gas_limit,
        "gasPrice": gas_price, "chainId": config.CHAIN_ID,
    })
    receipt = send_tx(w3, tx, private_key, f"SNIPE {name}")
    if receipt is None:
        return 0

    # Record actual tokens received from transfer event (approximate via balance diff)
    token_bal_after = token_contract.functions.balanceOf(wallet).call()

    position = {
        "name": name,
        "address": token_data["address"],
        "launchpad_address": token_data["launchpadAddress"],
        "token_decimals": token_decimals,
        "snipe_price_usd": price,
        "snipe_usd_spent": amount_usd,
        "tokens_held_raw": token_bal_after,
        "initial_fdv": token_data.get("initialFDV", 0),
        "bonding_fdv": token_data.get("bondingFDV", 0),
        "sniped_at": datetime.now(timezone.utc).isoformat(),
        "graduated": False,
        "pool_address": None,
        "sold_percent": 0,
        "realized_usd": 0.0,
    }

    positions = load_positions()
    positions.append(position)
    save_positions(positions)

    info(f"Position saved: {name} — ${amount_usd:.2f} spent")
    return amount_usd


# ── Graduation sell ───────────────────────────────────────────

def execute_graduation_sell(w3, wallet, private_key, usdce_addr, router,
                            position: dict, usdce_decimals: int, gas_price: int):
    """Sell GRADUATION_SELL_PERCENT of position into the new Uniswap pool."""
    name = position["name"]
    token_addr = Web3.to_checksum_address(position["address"])
    pool_addr = Web3.to_checksum_address(position["pool_address"])
    token_decimals = position.get("token_decimals", 18)

    token_contract = w3.eth.contract(address=token_addr, abi=ERC20_ABI)
    pool = w3.eth.contract(address=pool_addr, abi=POOL_ABI)

    # Pool orientation
    pool_token0 = Web3.to_checksum_address(pool.functions.token0().call())
    token_is_token0 = pool_token0.lower() == token_addr.lower()
    pool_fee = pool.functions.fee().call()

    # Current price
    slot0 = pool.functions.slot0().call()
    sqrt_price_x96 = slot0[0]
    raw = Decimal(sqrt_price_x96) ** 2 / Decimal(2 ** 192)
    multiplier = Decimal(10 ** token_decimals) / Decimal(10 ** usdce_decimals)
    price = float(raw * multiplier) if token_is_token0 else float(multiplier / raw)

    # How many tokens to sell
    token_bal_raw = token_contract.functions.balanceOf(wallet).call()
    sell_fraction = config.GRADUATION_SELL_PERCENT / 100.0
    tokens_to_sell_raw = int(token_bal_raw * sell_fraction)

    if tokens_to_sell_raw == 0:
        warn(f"{name}: no tokens to sell")
        return

    expected_usdc = (tokens_to_sell_raw / 10 ** token_decimals) * price
    slippage = 1.0 - config.SLIPPAGE_PERCENT / 100.0
    min_usdc_raw = int(expected_usdc * 10 ** usdce_decimals * slippage)

    info(f"GRADUATION SELL {name} — {config.GRADUATION_SELL_PERCENT:.0f}% of position")
    info(f"  Pool price=${price:.8f}  Expected return≈${expected_usdc:.4f} USDC.e")
    info(f"  Original cost=${position['snipe_usd_spent']:.2f}  "
         f"Profit≈${expected_usdc - position['snipe_usd_spent']:.2f}")

    if config.DRY_RUN:
        info("[DRY RUN] Would sell via Uniswap V3")
        return

    router_addr = Web3.to_checksum_address(config.ROUTER_ADDRESS)
    nonce = w3.eth.get_transaction_count(wallet)

    # Step 1: transfer tokens to router
    try:
        gas_est = token_contract.functions.transfer(router_addr, tokens_to_sell_raw).estimate_gas({"from": wallet})
        gas_limit = int(gas_est * 1.5)
    except Exception:
        gas_limit = 100_000

    transfer_tx = token_contract.functions.transfer(router_addr, tokens_to_sell_raw).build_transaction({
        "from": wallet, "nonce": nonce, "gas": gas_limit,
        "gasPrice": gas_price, "chainId": config.CHAIN_ID,
    })
    if send_tx(w3, transfer_tx, private_key, f"GRAD transfer {name}") is None:
        return

    # Step 2: router.execute()
    path = encode_packed(
        ["address", "uint24", "address"],
        [token_addr, pool_fee, Web3.to_checksum_address(usdce_addr)],
    )
    swap_input = encode(
        ["address", "uint256", "uint256", "bytes", "bool"],
        [wallet, tokens_to_sell_raw, min_usdc_raw, path, False],
    )
    deadline = int(time.time()) + 3600
    try:
        gas_est = router.functions.execute(
            bytes([0x00]), [swap_input], deadline
        ).estimate_gas({"from": wallet})
        gas_limit = int(gas_est * 1.5)
    except Exception:
        gas_limit = 400_000

    execute_tx = router.functions.execute(
        bytes([0x00]), [swap_input], deadline
    ).build_transaction({
        "from": wallet, "nonce": nonce + 1, "gas": gas_limit,
        "gasPrice": gas_price, "chainId": config.CHAIN_ID,
    })
    receipt = send_tx(w3, execute_tx, private_key, f"GRAD sell {name}")

    if receipt:
        position["sold_percent"] = config.GRADUATION_SELL_PERCENT
        position["realized_usd"] = expected_usdc
        info(f"Graduation sell complete. Realized≈${expected_usdc:.4f} "
             f"on ${position['snipe_usd_spent']:.2f} cost = "
             f"{(expected_usdc/position['snipe_usd_spent'] - 1)*100:.1f}% gain")


# ── Evaluation ────────────────────────────────────────────────

def is_snipe_worthy(token: dict) -> tuple[bool, str]:
    """Returns (eligible, reason)."""
    if token.get("graduatedAt") is not None:
        return False, "already graduated"
    if not token.get("launchpadAddress"):
        return False, "no launchpad address"
    if token.get("poolAddress"):
        return False, "already has pool (graduated)"

    initial_fdv = token.get("initialFDV") or 0
    bonding_fdv = token.get("bondingFDV") or 0

    if initial_fdv <= 0:
        return False, "no initialFDV data"
    if initial_fdv > config.SNIPE_MAX_INITIAL_FDV:
        return False, f"initialFDV ${initial_fdv} > max ${config.SNIPE_MAX_INITIAL_FDV}"

    spread = bonding_fdv / initial_fdv if initial_fdv > 0 else 0
    if spread < config.SNIPE_MIN_SPREAD:
        return False, f"spread {spread:.1f}x < min {config.SNIPE_MIN_SPREAD}x"

    return True, f"initialFDV=${initial_fdv} bondingFDV=${bonding_fdv} spread={spread:.1f}x"


# ── Daily tracker ─────────────────────────────────────────────

class SnipeDailyTracker:
    def __init__(self):
        self._day = datetime.now(timezone.utc).date()
        self.spent = 0.0

    def _reset_if_needed(self):
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            info(f"UTC day reset. Snipe spend yesterday: ${self.spent:.2f}")
            self._day = today
            self.spent = 0.0

    def add(self, usd: float):
        self._reset_if_needed()
        self.spent += usd

    @property
    def remaining(self) -> float:
        self._reset_if_needed()
        return max(0.0, config.SNIPE_DAILY_LIMIT - self.spent)


# ── Main loop ─────────────────────────────────────────────────

def main():
    if config.DRY_RUN:
        print("=" * 60)
        print("  SNIPER — DRY RUN MODE")
        print("  No real transactions. Set DRY_RUN=false to go live.")
        print("=" * 60)

    w3 = connect()
    wallet, private_key = load_wallet()

    info(f"Connected chain_id={w3.eth.chain_id}  Wallet={wallet}")

    usdce_addr = Web3.to_checksum_address(config.USDCE_ADDRESS)
    router_addr = Web3.to_checksum_address(config.ROUTER_ADDRESS)
    usdce = w3.eth.contract(address=usdce_addr, abi=ERC20_ABI)
    router = w3.eth.contract(address=router_addr, abi=ROUTER_ABI)
    usdce_decimals = usdce.functions.decimals().call()

    seen = load_seen()
    tracker = SnipeDailyTracker()
    scan = 0

    info(f"Sniper started. Scanning every {config.SNIPE_POLL_SEC}s. "
         f"Criteria: initialFDV<=${config.SNIPE_MAX_INITIAL_FDV}, "
         f"spread>={config.SNIPE_MIN_SPREAD}x, "
         f"daily limit=${config.SNIPE_DAILY_LIMIT}")
    info("Ctrl+C to stop.")

    while True:
        scan += 1
        info(f"── Scan {scan}  |  Snipe budget remaining: ${tracker.remaining:.2f} ──")

        try:
            all_tokens = fetch_all_tokens_api()
        except Exception as e:
            err(f"API fetch failed: {e}")
            time.sleep(config.SNIPE_POLL_SEC)
            continue

        gas_price = w3.eth.gas_price

        # ── Job 1: Find new tokens ────────────────────────────
        new_tokens = [t for t in all_tokens if t["address"].lower() not in seen]

        if new_tokens:
            info(f"Found {len(new_tokens)} new token(s)")
            for token in new_tokens:
                eligible, reason = is_snipe_worthy(token)
                name = token.get("name", token["address"][:10])
                if eligible:
                    info(f"  ✓ SNIPE CANDIDATE: {name} ({reason})")
                    if tracker.remaining >= config.SNIPE_AMOUNT_USD:
                        usdce_bal = usdce.functions.balanceOf(wallet).call() / 10 ** usdce_decimals
                        eth_bal = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
                        if usdce_bal < config.SNIPE_AMOUNT_USD:
                            warn(f"  Insufficient USDC.e ({usdce_bal:.4f}). Skipping snipe.")
                        elif eth_bal < config.MIN_ETH_FOR_GAS:
                            warn(f"  ETH too low for gas. Skipping snipe.")
                        else:
                            spent = execute_snipe(
                                w3, wallet, private_key, usdce, token,
                                usdce_decimals, tracker.spent, gas_price,
                            )
                            tracker.add(spent)
                    else:
                        warn(f"  Daily snipe limit reached. Would have sniped {name}.")
                else:
                    info(f"  ✗ skip {name}: {reason}")

        # Update seen set with all currently known tokens
        for t in all_tokens:
            seen.add(t["address"].lower())
        save_seen(seen)

        # ── Job 2: Check for graduations ──────────────────────
        positions = load_positions()
        token_map = {t["address"].lower(): t for t in all_tokens}
        positions_updated = False

        for pos in positions:
            if pos.get("graduated") or pos.get("sold_percent", 0) >= 100:
                continue

            addr_lower = pos["address"].lower()
            current = token_map.get(addr_lower)
            if current is None:
                continue

            pool_address = current.get("poolAddress")
            if pool_address and not pos.get("graduated"):
                info(f"GRADUATED: {pos['name']} — pool at {pool_address}")
                pos["graduated"] = True
                pos["pool_address"] = pool_address
                positions_updated = True

                execute_graduation_sell(
                    w3, wallet, private_key, usdce_addr, router,
                    pos, usdce_decimals, gas_price,
                )

        if positions_updated:
            save_positions(positions)

        time.sleep(config.SNIPE_POLL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[SNIPER] Stopped by user.")
    except Exception as e:
        err(f"Fatal: {e}")
        raise
