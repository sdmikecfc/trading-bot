"""
Doma Protocol Algorithmic Trading Bot — Multi-Token Edition
Trades across all fractionalized domain tokens: graduated (Uniswap V3) and bonding curve.

Usage:
    python fetch_tokens.py   # run once first to build tokens.json
    python bot.py
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_account import Account
from web3 import Web3

import config
from fetch_tokens import fetch_all_tokens

# ── ABIs ────────────────────────────────────────────────────

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

POOL_ABI = [
    {"name": "slot0", "type": "function", "stateMutability": "view",
     "inputs": [],
     "outputs": [
         {"name": "sqrtPriceX96", "type": "uint160"},
         {"name": "tick", "type": "int24"},
         {"name": "observationIndex", "type": "uint16"},
         {"name": "observationCardinality", "type": "uint16"},
         {"name": "observationCardinalityNext", "type": "uint16"},
         {"name": "feeProtocol", "type": "uint8"},
         {"name": "unlocked", "type": "bool"},
     ]},
    {"name": "token0", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"name": "token1", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"name": "fee", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint24"}]},
]

ROUTER_ABI = [
    {"name": "execute", "type": "function", "stateMutability": "payable",
     "inputs": [
         {"name": "commands", "type": "bytes"},
         {"name": "inputs", "type": "bytes[]"},
         {"name": "deadline", "type": "uint256"},
     ],
     "outputs": []},
]

LAUNCHPAD_ABI = [
    {"name": "buy", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "quoteAmount", "type": "uint256"},
         {"name": "minTokenAmount", "type": "uint256"},
     ],
     "outputs": [{"type": "uint256"}, {"type": "uint256"}]},
    {"name": "sell", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "tokenAmount", "type": "uint256"},
         {"name": "minQuoteAmount", "type": "uint256"},
     ],
     "outputs": [{"type": "uint256"}, {"type": "uint256"}]},
    {"name": "getAvailableTokensToBuy", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "launchStatus", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
]

MAX_UINT256 = 2 ** 256 - 1
TOKENS_FILE = os.path.join(os.path.dirname(__file__), "tokens.json")


# ── Logging ─────────────────────────────────────────────────

def log(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def info(msg):  log("INFO ", msg)
def warn(msg):  log("WARN ", msg)
def err(msg):   log("ERROR", msg)


# ── Setup ───────────────────────────────────────────────────

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


def load_tokens() -> list[dict]:
    if not os.path.exists(TOKENS_FILE):
        info("tokens.json not found — fetching from API now...")
        tokens = fetch_all_tokens()
        with open(TOKENS_FILE, "w") as f:
            json.dump(tokens, f, indent=2)
        return tokens
    with open(TOKENS_FILE) as f:
        return json.load(f)


def refresh_tokens() -> list[dict]:
    info("Refreshing token list from API...")
    try:
        tokens = fetch_all_tokens()
        with open(TOKENS_FILE, "w") as f:
            json.dump(tokens, f, indent=2)
        graduated = sum(1 for t in tokens if t.get("graduated"))
        info(f"Token list updated: {len(tokens)} total ({graduated} graduated, {len(tokens)-graduated} bonding curve)")
        return tokens
    except Exception as e:
        warn(f"Token refresh failed: {e} — using existing list")
        return load_tokens()


# ── Price ────────────────────────────────────────────────────

def get_pool_price(pool, token_is_token0: bool, token_decimals: int, usdce_decimals: int) -> float:
    slot0 = pool.functions.slot0().call()
    sqrt_price_x96 = slot0[0]
    if sqrt_price_x96 == 0:
        return 0.0
    raw = Decimal(sqrt_price_x96) ** 2 / Decimal(2 ** 192)
    multiplier = Decimal(10 ** token_decimals) / Decimal(10 ** usdce_decimals)
    if token_is_token0:
        return float(raw * multiplier)
    else:
        return float(multiplier / raw)


# ── Allowance helper ────────────────────────────────────────

def ensure_allowance(w3, wallet, private_key, token_contract, spender, label, gas_price):
    current = token_contract.functions.allowance(wallet, spender).call()
    if current >= MAX_UINT256 // 2:
        return True
    info(f"Approving {label} for {spender[:10]}...")
    if config.DRY_RUN:
        info("[DRY RUN] Would approve")
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
    receipt = send_tx(w3, tx, private_key, f"Approve {label}")
    return receipt is not None


# ── TX helper ───────────────────────────────────────────────

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


# ── Uniswap V3 swap (graduated tokens) ──────────────────────

def swap_v3(w3, wallet, private_key, router, token_in_contract,
            token_in_addr, token_out_addr, amount_in_raw, amount_out_min_raw,
            pool_fee, gas_price, label):
    nonce = w3.eth.get_transaction_count(wallet)

    # Step 1: transfer tokenIn directly to router
    try:
        gas_est = token_in_contract.functions.transfer(
            config.ROUTER_ADDRESS, amount_in_raw
        ).estimate_gas({"from": wallet})
        gas_limit = int(gas_est * 1.5)
    except Exception:
        gas_limit = 100_000

    transfer_tx = token_in_contract.functions.transfer(
        config.ROUTER_ADDRESS, amount_in_raw
    ).build_transaction({
        "from": wallet, "nonce": nonce, "gas": gas_limit,
        "gasPrice": gas_price, "chainId": config.CHAIN_ID,
    })
    if send_tx(w3, transfer_tx, private_key, f"{label} transfer") is None:
        return False

    # Step 2: router.execute() — payerIsUser=False (tokens already in router)
    path = encode_packed(
        ["address", "uint24", "address"],
        [Web3.to_checksum_address(token_in_addr), pool_fee, Web3.to_checksum_address(token_out_addr)],
    )
    swap_input = encode(
        ["address", "uint256", "uint256", "bytes", "bool"],
        [wallet, amount_in_raw, amount_out_min_raw, path, False],
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
    return send_tx(w3, execute_tx, private_key, f"{label} execute") is not None


# ── Launchpad swap (bonding curve tokens) ───────────────────

def buy_launchpad(w3, wallet, private_key, usdce, launchpad, launchpad_addr,
                  amount_in_raw, amount_out_min_raw, gas_price):
    # Launchpad pulls USDC.e via transferFrom — needs approve
    if not ensure_allowance(w3, wallet, private_key, usdce, launchpad_addr, "USDC.e→launchpad", gas_price):
        return False
    if config.DRY_RUN:
        return True
    nonce = w3.eth.get_transaction_count(wallet)
    try:
        gas_est = launchpad.functions.buy(
            amount_in_raw, amount_out_min_raw
        ).estimate_gas({"from": wallet})
        gas_limit = int(gas_est * 1.5)
    except Exception:
        gas_limit = 300_000
    tx = launchpad.functions.buy(amount_in_raw, amount_out_min_raw).build_transaction({
        "from": wallet, "nonce": nonce, "gas": gas_limit,
        "gasPrice": gas_price, "chainId": config.CHAIN_ID,
    })
    return send_tx(w3, tx, private_key, "BUY launchpad") is not None


def sell_launchpad(w3, wallet, private_key, token_contract, launchpad, launchpad_addr,
                   amount_in_raw, amount_out_min_raw, gas_price):
    # Launchpad pulls fractional tokens via transferFrom — needs approve
    if not ensure_allowance(w3, wallet, private_key, token_contract, launchpad_addr, "token→launchpad", gas_price):
        return False
    if config.DRY_RUN:
        return True
    nonce = w3.eth.get_transaction_count(wallet)
    try:
        gas_est = launchpad.functions.sell(
            amount_in_raw, amount_out_min_raw
        ).estimate_gas({"from": wallet})
        gas_limit = int(gas_est * 1.5)
    except Exception:
        gas_limit = 300_000
    tx = launchpad.functions.sell(amount_in_raw, amount_out_min_raw).build_transaction({
        "from": wallet, "nonce": nonce, "gas": gas_limit,
        "gasPrice": gas_price, "chainId": config.CHAIN_ID,
    })
    return send_tx(w3, tx, private_key, "SELL launchpad") is not None


# ── Pool info cache ──────────────────────────────────────────

_pool_cache = {}  # pool_address -> {token_is_token0, pool_fee, token_decimals}


def get_pool_info(w3, pool_addr, token_addr, usdce_decimals):
    if pool_addr in _pool_cache:
        return _pool_cache[pool_addr]
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_addr), abi=POOL_ABI)
    token0 = Web3.to_checksum_address(pool.functions.token0().call())
    token_is_token0 = token0.lower() == token_addr.lower()
    pool_fee = pool.functions.fee().call()
    token_contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
    token_decimals = token_contract.functions.decimals().call()
    info = {
        "pool": pool,
        "token_is_token0": token_is_token0,
        "pool_fee": pool_fee,
        "token_decimals": token_decimals,
        "token_contract": token_contract,
    }
    _pool_cache[pool_addr] = info
    return info


# ── Daily tracker ────────────────────────────────────────────

class DailyTracker:
    def __init__(self):
        self._day = datetime.now(timezone.utc).date()
        self.spent = 0.0
        self.gained = 0.0
        self.trades = 0

    def _maybe_reset(self):
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            info(f"UTC day rolled. Yesterday: {self.summary()}")
            self._day = today
            self.spent = 0.0
            self.gained = 0.0
            self.trades = 0

    def add_buy(self, usd):
        self._maybe_reset()
        self.spent += usd
        self.trades += 1

    def add_sell(self, usd):
        self._maybe_reset()
        self.gained += usd
        self.trades += 1

    def over_limit(self):
        self._maybe_reset()
        return self.spent >= config.DAILY_LOSS_LIMIT_USD

    def summary(self):
        return f"spent=${self.spent:.4f} gained=${self.gained:.4f} net=${self.gained-self.spent:.4f} trades={self.trades}"


# ── Main ─────────────────────────────────────────────────────

def main():
    if config.DRY_RUN:
        print("=" * 60)
        print("  DRY RUN MODE — no real transactions will be sent")
        print("  Set DRY_RUN=false in .env when ready to go live")
        print("=" * 60)

    w3 = connect()
    wallet, private_key = load_wallet()

    info(f"Connected to chain_id={w3.eth.chain_id}")
    info(f"Wallet: {wallet}")

    if w3.eth.chain_id != config.CHAIN_ID:
        err(f"Chain ID mismatch: expected {config.CHAIN_ID}, got {w3.eth.chain_id}")
        sys.exit(1)

    usdce_addr = Web3.to_checksum_address(config.USDCE_ADDRESS)
    router_addr = Web3.to_checksum_address(config.ROUTER_ADDRESS)
    usdce = w3.eth.contract(address=usdce_addr, abi=ERC20_ABI)
    router = w3.eth.contract(address=router_addr, abi=ROUTER_ABI)
    usdce_decimals = usdce.functions.decimals().call()

    tokens = load_tokens()
    graduated = sum(1 for t in tokens if t.get("graduated"))
    info(f"Loaded {len(tokens)} tokens ({graduated} graduated, {len(tokens)-graduated} bonding curve)")

    tracker = DailyTracker()
    cycle = 0

    info("Bot started. Ctrl+C to stop.")

    while True:
        cycle += 1

        # Periodic token list refresh
        if cycle % config.TOKEN_REFRESH_CYCLES == 0:
            tokens = refresh_tokens()

        if not tokens:
            warn("No tokens available. Retrying after sleep.")
            time.sleep(60)
            continue

        token = random.choice(tokens)
        name = token.get("name", token["address"][:10])
        token_addr = Web3.to_checksum_address(token["address"])
        pool_address = token.get("pool_address")
        launchpad_address = token.get("launchpad_address")
        price_floor = token.get("price_floor_usd", 0.0)
        is_graduated = token.get("graduated", False)

        info(f"── Cycle {cycle} │ {name} │ {'V3' if is_graduated else 'bonding'} ──")

        # Balances
        eth_bal = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
        usdce_raw = usdce.functions.balanceOf(wallet).call()
        usdce_bal = usdce_raw / 10 ** usdce_decimals
        gas_price = w3.eth.gas_price

        info(f"ETH={eth_bal:.6f}  USDC.e={usdce_bal:.4f}  Daily: {tracker.summary()}")

        # Guards
        if eth_bal < config.MIN_ETH_FOR_GAS:
            warn(f"ETH too low ({eth_bal:.6f}). Skipping.")
            _sleep()
            continue

        if tracker.over_limit():
            info(f"Daily cap ${config.DAILY_LOSS_LIMIT_USD:.2f} reached. Waiting for UTC midnight.")
            _sleep()
            continue

        # ── Graduated token: Uniswap V3 ─────────────────────
        if is_graduated and pool_address:
            try:
                pi = get_pool_info(w3, pool_address, token_addr, usdce_decimals)
            except Exception as e:
                warn(f"Could not load pool info for {name}: {e}. Skipping.")
                _sleep()
                continue

            price = get_pool_price(pi["pool"], pi["token_is_token0"], pi["token_decimals"], usdce_decimals)
            if price <= 0:
                warn(f"Price is zero for {name}. Skipping.")
                _sleep()
                continue

            token_raw = pi["token_contract"].functions.balanceOf(wallet).call()
            token_bal_usd = (token_raw / 10 ** pi["token_decimals"]) * price

            info(f"Price=${price:.8f}  Token≈${token_bal_usd:.4f}")

            can_buy = usdce_bal >= config.TRADE_MIN_USD
            can_sell = token_bal_usd >= config.TRADE_MIN_USD

            if not can_buy and not can_sell:
                warn("Insufficient balance for any trade. Skipping.")
                _sleep()
                continue

            # Action selection
            if price_floor > 0 and price < price_floor:
                if not can_buy:
                    warn(f"Price below floor but no USDC. Skipping.")
                    _sleep()
                    continue
                action = "buy"
                info(f"Price below floor ${price_floor:.8f} — forcing buy.")
            elif not can_buy:
                action = "sell"
            elif not can_sell:
                action = "buy"
            else:
                action = random.choices(["buy", "sell"], weights=[config.BUY_WEIGHT, config.SELL_WEIGHT])[0]

            slippage = 1.0 - config.SLIPPAGE_PERCENT / 100.0

            if action == "buy":
                trade_usd = min(
                    random.uniform(config.TRADE_MIN_USD, config.TRADE_MAX_USD),
                    usdce_bal,
                    config.DAILY_LOSS_LIMIT_USD - tracker.spent,
                )
                if trade_usd < config.TRADE_MIN_USD:
                    _sleep()
                    continue
                amount_in_raw = int(trade_usd * 10 ** usdce_decimals)
                amount_out_min = int((trade_usd / price) * 10 ** pi["token_decimals"] * slippage)
                info(f"BUY  ${trade_usd:.4f} USDC.e → {name}")
                if config.DRY_RUN:
                    info("[DRY RUN] Would swap via Uniswap V3")
                    tracker.add_buy(trade_usd)
                else:
                    ok = swap_v3(w3, wallet, private_key, router, usdce,
                                 usdce_addr, token_addr, amount_in_raw, amount_out_min,
                                 pi["pool_fee"], gas_price, "BUY V3")
                    if ok:
                        tracker.add_buy(trade_usd)
            else:
                trade_usd = min(
                    random.uniform(config.TRADE_MIN_USD, config.TRADE_MAX_USD),
                    token_bal_usd,
                )
                if trade_usd < config.TRADE_MIN_USD:
                    _sleep()
                    continue
                amount_in_raw = int((trade_usd / price) * 10 ** pi["token_decimals"])
                amount_out_min = int(trade_usd * 10 ** usdce_decimals * slippage)
                info(f"SELL ${trade_usd:.4f} worth of {name} → USDC.e")
                if config.DRY_RUN:
                    info("[DRY RUN] Would swap via Uniswap V3")
                    tracker.add_sell(trade_usd)
                else:
                    ok = swap_v3(w3, wallet, private_key, router, pi["token_contract"],
                                 token_addr, usdce_addr, amount_in_raw, amount_out_min,
                                 pi["pool_fee"], gas_price, "SELL V3")
                    if ok:
                        tracker.add_sell(trade_usd)

        # ── Bonding curve token: launchpad ───────────────────
        elif not is_graduated and launchpad_address:
            launchpad_addr = Web3.to_checksum_address(launchpad_address)
            launchpad = w3.eth.contract(address=launchpad_addr, abi=LAUNCHPAD_ABI)
            token_contract = w3.eth.contract(address=token_addr, abi=ERC20_ABI)

            try:
                token_decimals = token_contract.functions.decimals().call()
            except Exception:
                token_decimals = 18

            price = token.get("price_usd", 0.0)
            token_raw = token_contract.functions.balanceOf(wallet).call()
            token_bal_usd = (token_raw / 10 ** token_decimals) * price if price > 0 else 0.0

            info(f"Price≈${price:.8f}  Token≈${token_bal_usd:.4f}")

            # Check launchpad is still active
            try:
                status = launchpad.functions.launchStatus().call()
                # 1 = InProgress, anything else = ended/migrated
                if status != 1:
                    warn(f"{name} launchpad not active (status={status}). Skipping.")
                    _sleep()
                    continue
            except Exception:
                pass  # If we can't check status, try anyway

            can_buy = usdce_bal >= config.TRADE_MIN_USD
            can_sell = token_bal_usd >= config.TRADE_MIN_USD

            if not can_buy and not can_sell:
                warn("Insufficient balance. Skipping.")
                _sleep()
                continue

            if not can_buy:
                action = "sell"
            elif not can_sell:
                action = "buy"
            else:
                action = random.choices(["buy", "sell"], weights=[config.BUY_WEIGHT, config.SELL_WEIGHT])[0]

            slippage = 1.0 - config.SLIPPAGE_PERCENT / 100.0

            if action == "buy":
                trade_usd = min(
                    random.uniform(config.TRADE_MIN_USD, config.TRADE_MAX_USD),
                    usdce_bal,
                    config.DAILY_LOSS_LIMIT_USD - tracker.spent,
                )
                if trade_usd < config.TRADE_MIN_USD:
                    _sleep()
                    continue
                amount_in_raw = int(trade_usd * 10 ** usdce_decimals)
                amount_out_min = int((trade_usd / price) * 10 ** token_decimals * slippage) if price > 0 else 0
                info(f"BUY  ${trade_usd:.4f} USDC.e → {name} (bonding curve)")
                if config.DRY_RUN:
                    info("[DRY RUN] Would call launchpad.buy()")
                    tracker.add_buy(trade_usd)
                else:
                    ok = buy_launchpad(w3, wallet, private_key, usdce, launchpad,
                                       launchpad_addr, amount_in_raw, amount_out_min, gas_price)
                    if ok:
                        tracker.add_buy(trade_usd)
            else:
                trade_usd = min(
                    random.uniform(config.TRADE_MIN_USD, config.TRADE_MAX_USD),
                    token_bal_usd,
                )
                if trade_usd < config.TRADE_MIN_USD:
                    _sleep()
                    continue
                amount_in_raw = int((trade_usd / price) * 10 ** token_decimals) if price > 0 else 0
                amount_out_min = int(trade_usd * 10 ** usdce_decimals * slippage)
                info(f"SELL ${trade_usd:.4f} worth of {name} → USDC.e (bonding curve)")
                if config.DRY_RUN:
                    info("[DRY RUN] Would call launchpad.sell()")
                    tracker.add_sell(trade_usd)
                else:
                    ok = sell_launchpad(w3, wallet, private_key, token_contract, launchpad,
                                        launchpad_addr, amount_in_raw, amount_out_min, gas_price)
                    if ok:
                        tracker.add_sell(trade_usd)

        else:
            warn(f"{name} has no pool or launchpad address. Skipping.")

        _sleep()


def _sleep():
    delay = random.randint(config.INTERVAL_MIN_SEC, config.INTERVAL_MAX_SEC)
    info(f"Sleeping {delay}s...")
    time.sleep(delay)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO ] Stopped by user.")
    except Exception as e:
        err(f"Fatal error: {e}")
        raise
