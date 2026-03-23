"""
Doma Protocol Algorithmic Trading Bot
Volume generation and liquidity strategy for fractionalized domain tokens.

Usage:
    python bot.py

Ensure DRY_RUN=true in .env before your first run.
"""

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


# ── Helpers ─────────────────────────────────────────────────

def log(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def info(msg):  log("INFO ", msg)
def warn(msg):  log("WARN ", msg)
def err(msg):   log("ERROR", msg)


# ── Setup ───────────────────────────────────────────────────

def load_wallet() -> tuple[str, str]:
    """Returns (address, private_key)."""
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


# ── Price ───────────────────────────────────────────────────

def get_price(pool, token_is_token0: bool, token_decimals: int, usdce_decimals: int) -> float:
    slot0 = pool.functions.slot0().call()
    sqrt_price_x96 = slot0[0]
    if sqrt_price_x96 == 0:
        return 0.0

    raw = Decimal(sqrt_price_x96) ** 2 / Decimal(2 ** 192)
    multiplier = Decimal(10 ** token_decimals) / Decimal(10 ** usdce_decimals)

    if token_is_token0:
        # token0=domain, token1=USDC.e  →  raw = USDC_raw/token_raw
        price = raw * multiplier
    else:
        # token0=USDC.e, token1=domain  →  raw = token_raw/USDC_raw
        price = multiplier / raw

    return float(price)


# ── Swap ────────────────────────────────────────────────────

def build_path(token_in: str, fee: int, token_out: str) -> bytes:
    return encode_packed(
        ["address", "uint24", "address"],
        [Web3.to_checksum_address(token_in), fee, Web3.to_checksum_address(token_out)],
    )


def send_tx(w3: Web3, tx: dict, private_key: str, label: str) -> dict | None:
    try:
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        info(f"{label} tx sent: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
        if receipt.status != 1:
            err(f"{label} FAILED (status=0): {tx_hash.hex()}")
            return None
        info(f"{label} confirmed in block {receipt.blockNumber}")
        return receipt
    except Exception as e:
        err(f"{label} exception: {e}")
        return None


def execute_swap(
    w3: Web3,
    wallet: str,
    private_key: str,
    router,
    token_in_contract,
    token_in_addr: str,
    token_out_addr: str,
    amount_in_raw: int,
    amount_out_min_raw: int,
    pool_fee: int,
    gas_price: int,
    label: str,
) -> bool:
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
        "from": wallet,
        "nonce": nonce,
        "gas": gas_limit,
        "gasPrice": gas_price,
        "chainId": config.CHAIN_ID,
    })

    receipt = send_tx(w3, transfer_tx, private_key, f"{label} transfer")
    if receipt is None:
        err(f"Transfer to router FAILED — aborting swap")
        return False

    # Step 2: router.execute() — payerIsUser=False (router already holds tokens)
    path = build_path(token_in_addr, pool_fee, token_out_addr)
    swap_input = encode(
        ["address", "uint256", "uint256", "bytes", "bool"],
        [wallet, amount_in_raw, amount_out_min_raw, path, False],
    )

    deadline = int(time.time()) + 3600
    commands = bytes([0x00])  # V3_SWAP_EXACT_IN

    try:
        gas_est = router.functions.execute(
            commands, [swap_input], deadline
        ).estimate_gas({"from": wallet})
        gas_limit = int(gas_est * 1.5)
    except Exception:
        gas_limit = 400_000

    execute_tx = router.functions.execute(
        commands, [swap_input], deadline
    ).build_transaction({
        "from": wallet,
        "nonce": nonce + 1,
        "gas": gas_limit,
        "gasPrice": gas_price,
        "chainId": config.CHAIN_ID,
    })

    receipt = send_tx(w3, execute_tx, private_key, f"{label} execute")
    return receipt is not None


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
            info(f"UTC day rolled over. Yesterday: spent=${self.spent:.4f} gained=${self.gained:.4f} trades={self.trades}")
            self._day = today
            self.spent = 0.0
            self.gained = 0.0
            self.trades = 0

    def add_buy(self, usd: float):
        self._maybe_reset()
        self.spent += usd
        self.trades += 1

    def add_sell(self, usd: float):
        self._maybe_reset()
        self.gained += usd
        self.trades += 1

    def over_limit(self) -> bool:
        self._maybe_reset()
        return self.spent >= config.DAILY_LOSS_LIMIT_USD

    def summary(self) -> str:
        return f"spent=${self.spent:.4f} gained=${self.gained:.4f} net=${self.gained - self.spent:.4f} trades={self.trades}"


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

    # Contracts
    token_addr = Web3.to_checksum_address(config.TOKEN_ADDRESS)
    usdce_addr = Web3.to_checksum_address(config.USDCE_ADDRESS)
    pool_addr = Web3.to_checksum_address(config.POOL_ADDRESS)
    router_addr = Web3.to_checksum_address(config.ROUTER_ADDRESS)

    token = w3.eth.contract(address=token_addr, abi=ERC20_ABI)
    usdce = w3.eth.contract(address=usdce_addr, abi=ERC20_ABI)
    pool = w3.eth.contract(address=pool_addr, abi=POOL_ABI)
    router = w3.eth.contract(address=router_addr, abi=ROUTER_ABI)

    # Decimals
    token_decimals = token.functions.decimals().call()
    usdce_decimals = usdce.functions.decimals().call()
    info(f"Token decimals={token_decimals}, USDC.e decimals={usdce_decimals}")

    # Pool orientation
    pool_token0 = Web3.to_checksum_address(pool.functions.token0().call())
    token_is_token0 = pool_token0.lower() == token_addr.lower()
    pool_fee = pool.functions.fee().call()
    info(f"Pool fee={pool_fee} ({pool_fee/10000:.2f}%), token_is_token0={token_is_token0}")

    tracker = DailyTracker()
    cycle = 0

    info("Bot started. Ctrl+C to stop.")

    while True:
        cycle += 1
        info(f"── Cycle {cycle} ─────────────────────────")

        # Balances
        eth_bal = w3.from_wei(w3.eth.get_balance(wallet), "ether")
        usdce_raw = usdce.functions.balanceOf(wallet).call()
        token_raw = token.functions.balanceOf(wallet).call()
        usdce_bal = usdce_raw / 10 ** usdce_decimals
        gas_price = w3.eth.gas_price

        # Price
        price = get_price(pool, token_is_token0, token_decimals, usdce_decimals)
        token_bal_usd = (token_raw / 10 ** token_decimals) * price if price > 0 else 0.0

        info(f"ETH={float(eth_bal):.6f}  USDC.e={usdce_bal:.4f}  Token≈${token_bal_usd:.4f}  Price=${price:.8f}")
        info(f"Daily: {tracker.summary()}")

        # Guard: gas
        if float(eth_bal) < config.MIN_ETH_FOR_GAS:
            warn(f"ETH balance too low for gas ({float(eth_bal):.6f} < {config.MIN_ETH_FOR_GAS}). Skipping.")
            _sleep()
            continue

        # Guard: daily cap
        if tracker.over_limit():
            info(f"Daily cap ${config.DAILY_LOSS_LIMIT_USD:.2f} reached. Waiting for UTC midnight.")
            _sleep()
            continue

        # What can we do?
        can_buy = usdce_bal >= config.TRADE_MIN_USD
        can_sell = token_bal_usd >= config.TRADE_MIN_USD

        if not can_buy and not can_sell:
            warn("Insufficient USDC.e and tokens for minimum trade size. Skipping.")
            _sleep()
            continue

        # Determine action
        if PRICE_FLOOR_USD_active() and price > 0 and price < config.PRICE_FLOOR_USD:
            if not can_buy:
                warn("Price below floor but no USDC.e to defend. Skipping.")
                _sleep()
                continue
            action = "buy"
            info(f"Price ${price:.8f} < floor ${config.PRICE_FLOOR_USD:.8f} — forcing buy.")
        elif not can_buy:
            action = "sell"
        elif not can_sell:
            action = "buy"
        else:
            action = random.choices(["buy", "sell"], weights=[config.BUY_WEIGHT, config.SELL_WEIGHT])[0]

        # Trade size
        trade_usd = round(random.uniform(config.TRADE_MIN_USD, config.TRADE_MAX_USD), 4)

        # Cap buy size to available USDC
        if action == "buy":
            trade_usd = min(trade_usd, usdce_bal)
            # Also respect remaining daily budget
            remaining = config.DAILY_LOSS_LIMIT_USD - tracker.spent
            trade_usd = min(trade_usd, remaining)
            if trade_usd < config.TRADE_MIN_USD:
                info("Remaining daily budget too small for a buy. Skipping.")
                _sleep()
                continue

        # Cap sell size to available tokens
        if action == "sell":
            max_sell_usd = token_bal_usd
            trade_usd = min(trade_usd, max_sell_usd)
            if trade_usd < config.TRADE_MIN_USD:
                info("Token balance too small for minimum sell. Skipping.")
                _sleep()
                continue

        slippage_mult = 1.0 - config.SLIPPAGE_PERCENT / 100.0

        if action == "buy":
            amount_in_raw = int(trade_usd * 10 ** usdce_decimals)
            expected_tokens = trade_usd / price if price > 0 else 0
            amount_out_min = int(expected_tokens * 10 ** token_decimals * slippage_mult)
            token_in_addr = usdce_addr
            token_out_addr = token_addr
            token_in_contract = usdce

            info(f"BUY  ${trade_usd:.4f} USDC.e → ~{expected_tokens:.2f} tokens (min {amount_out_min / 10**token_decimals:.2f})")
        else:
            tokens_to_sell = trade_usd / price if price > 0 else 0
            amount_in_raw = int(tokens_to_sell * 10 ** token_decimals)
            expected_usdc = trade_usd
            amount_out_min = int(expected_usdc * 10 ** usdce_decimals * slippage_mult)
            token_in_addr = token_addr
            token_out_addr = usdce_addr
            token_in_contract = token

            info(f"SELL ~{tokens_to_sell:.2f} tokens → ${expected_usdc:.4f} USDC.e (min {amount_out_min / 10**usdce_decimals:.4f})")

        if config.DRY_RUN:
            info("[DRY RUN] Would execute swap — no transaction sent.")
            if action == "buy":
                tracker.add_buy(trade_usd)
            else:
                tracker.add_sell(trade_usd)
        else:
            ok = execute_swap(
                w3=w3,
                wallet=wallet,
                private_key=private_key,
                router=router,
                token_in_contract=token_in_contract,
                token_in_addr=token_in_addr,
                token_out_addr=token_out_addr,
                amount_in_raw=amount_in_raw,
                amount_out_min_raw=amount_out_min,
                pool_fee=pool_fee,
                gas_price=gas_price,
                label=action.upper(),
            )
            if ok:
                if action == "buy":
                    tracker.add_buy(trade_usd)
                else:
                    tracker.add_sell(trade_usd)

        _sleep()


def PRICE_FLOOR_USD_active() -> bool:
    return config.PRICE_FLOOR_USD > 0.0


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
