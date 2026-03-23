import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise ValueError(f"Missing required config: {key}")
    return val


def _float(key: str, default: float) -> float:
    return float(os.getenv(key, default))


def _int(key: str, default: int) -> int:
    return int(os.getenv(key, default))


def _bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes")


# ── Wallet ──────────────────────────────────────────────────
MNEMONIC = os.getenv("MNEMONIC", "").strip()
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
MNEMONIC_ACCOUNT_INDEX = _int("MNEMONIC_ACCOUNT_INDEX", 0)

if not MNEMONIC and not PRIVATE_KEY:
    raise ValueError("Set either MNEMONIC or PRIVATE_KEY in your .env file.")

# ── Network ─────────────────────────────────────────────────
RPC_URL = _require("RPC_URL")
CHAIN_ID = _int("CHAIN_ID", 97477)

# ── Fixed addresses ─────────────────────────────────────────
USDCE_ADDRESS = _require("USDCE_ADDRESS")
ROUTER_ADDRESS = _require("ROUTER_ADDRESS")

# ── Doma API ────────────────────────────────────────────────
DOMA_API_KEY = _require("DOMA_API_KEY")
DOMA_API_URL = "https://api.doma.xyz/graphql"

# ── Trade settings ──────────────────────────────────────────
TRADE_MIN_USD = _float("TRADE_MIN_USD", 0.10)
TRADE_MAX_USD = _float("TRADE_MAX_USD", 0.30)
INTERVAL_MIN_SEC = _int("INTERVAL_MIN_SEC", 60)
INTERVAL_MAX_SEC = _int("INTERVAL_MAX_SEC", 300)
SLIPPAGE_PERCENT = _float("SLIPPAGE_PERCENT", 2.0)

# ── Buy/sell bias ───────────────────────────────────────────
BUY_WEIGHT = _float("BUY_WEIGHT", 65)
SELL_WEIGHT = _float("SELL_WEIGHT", 35)

# ── Daily cap ───────────────────────────────────────────────
DAILY_LOSS_LIMIT_USD = _float("DAILY_LOSS_LIMIT_USD", 10.0)
MIN_ETH_FOR_GAS = _float("MIN_ETH_FOR_GAS", 0.001)

# ── Token list refresh ──────────────────────────────────────
TOKEN_REFRESH_CYCLES = _int("TOKEN_REFRESH_CYCLES", 50)

# ── Mode ────────────────────────────────────────────────────
DRY_RUN = _bool("DRY_RUN", True)
