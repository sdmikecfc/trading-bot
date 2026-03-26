# Doma Protocol Sniper Bot

Automatically snipes token launches on the [Doma Protocol](https://doma.xyz) bonding curve — buying at the floor price the instant a token goes live.

Built and maintained by [@web3guides](https://web3guides.com) · [web3guides.com/doma](https://web3guides.com/doma)

---

## How It Works

Doma Protocol lets users fractionalize domain names into ERC-20 tokens. Each token launches on a **bonding curve** at a fixed floor price — the price can only go **up** from there. This bot:

1. Reads the public Doma launch schedule
2. Waits for your chosen token to go live
3. Buys instantly at the floor price (typically within 10–15 seconds of launch)
4. Optionally monitors for price targets and graduation to Uniswap V3

---

## Two Tools

| File | What it does |
|------|-------------|
| `snipe.py` | **Interactive** — shows upcoming launches, you pick one and set your amount, it handles the rest. Recommended for beginners. |
| `schedule_sniper.py` | **Automated** — runs continuously, auto-snipes every eligible launch based on your criteria. For advanced users. |

---

## Prerequisites

- **Python 3.11+** — [python.org/downloads](https://python.org/downloads)
- **A wallet** with USDC.e and a small amount of ETH on Doma chain (for gas)
- **USDC.e on Doma chain** — bridge from Ethereum or another chain via [Across Protocol](https://across.to) or [Optimism Superbridge](https://superbridge.app)

> Gas on Doma chain is nearly free. Keep at least 0.01 ETH for comfortable headroom.

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/sdmikecfc/trading-bot.git
cd trading-bot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy the config template
cp .env.example .env
```

---

## Configuration

Open `.env` in any text editor and fill in **only** what's required:

```env
# Your wallet — choose ONE of these:
MNEMONIC=word1 word2 word3 ... word12
# OR
PRIVATE_KEY=0xYourPrivateKeyHere

# Start in dry-run mode (no real transactions) — change to false when ready
DRY_RUN=true
```

Everything else has sensible defaults. The network settings, API key, and contract addresses are pre-filled and correct for Doma chain.

> **Security:** Your `.env` file is blocked from Git by `.gitignore`. It never leaves your machine.

---

## Quick Start — Interactive Sniper

```bash
python snipe.py
```

You'll see a table of upcoming launches:

```
  Upcoming launches — next 24 hours

  +------+--------------------------------+------------+----------------+-------------+---------+
  |  #   | Domain                         | Launch UTC |  Starting FDV  | Bonding FDV | Spread  |
  +------+--------------------------------+------------+----------------+-------------+---------+
  |  1   | chainspectre.com               | 10:00      |  $300          | $500        | 1.7x    |
  |  2   | tokentycoon.com                | 14:00      |  $750          | $1,200      | 1.6x    |
  +------+--------------------------------+------------+----------------+-------------+---------+

  Pick a launch (number or domain name): 1
  How much USDC.e to snipe with? $5.00
```

The bot then counts down to launch and fires the buy automatically.

---

## Automated Sniper (Advanced)

`schedule_sniper.py` runs as a background process and handles all launches automatically based on your criteria.

**Additional settings for the automated sniper** (configure in `.env`):

```env
SNIPE_AMOUNT_USD=5.00        # How much to spend per snipe
SNIPE_DAILY_LIMIT=50.00      # Max spend per day
SNIPE_MAX_INITIAL_FDV=500    # Skip tokens with Starting FDV above this
SNIPE_MIN_SPREAD=1.5         # Skip tokens with less than 1.5x bonding curve spread
DRY_RUN=false                # Set false to trade real funds
```

Run it:
```bash
python schedule_sniper.py
```

It will:
- Re-read the launch schedule every 5 minutes
- Spawn a dedicated thread for each eligible upcoming launch
- Start tight-polling 2 minutes before each launch
- Buy the instant the bonding curve activates
- Monitor positions for price targets (1.5x, 2x, 3x) and auto-sell tranches on the bonding curve
- Hold the final 25% as a moon bag through graduation

---

## One-Time Approval (Optional)

For the automated sniper, you can pre-approve the router to save a transaction during snipes:

```bash
python preapprove.py
```

> The interactive `snipe.py` handles approvals automatically per-launch.

---

## Recovery Tool

If a graduation sell fails (tokens get stuck in the router), run:

```bash
python recover_sell.py yourdomain.com
```

---

## Understanding the Numbers

| Term | Meaning |
|------|---------|
| **Starting FDV** | Floor price × total supply. The minimum the market cap can be. |
| **Bonding FDV** | Target FDV when the bonding curve is full. Token graduates to Uniswap V3 at this point. |
| **Spread** | Bonding FDV ÷ Starting FDV. Higher = more room for price appreciation before graduation. |
| **Graduation** | When the bonding curve fills completely. Token migrates to Uniswap V3 with real liquidity. |

**Why is the bonding curve safe to buy at?**
The price on the bonding curve can only go **up**. You buy at the current floor and every subsequent buyer pays more than you did. There is no slippage risk on the buy side.

---

## Security Notes

- **Your private key never leaves your machine.** It is loaded from `.env` at runtime and used only to sign transactions locally.
- **`.env` is in `.gitignore`** — it cannot be accidentally committed to GitHub.
- **Per-snipe approvals** — the bot approves each launchpad contract individually, not a blanket approval for all tokens.
- **DRY_RUN=true by default** — no real transactions until you explicitly opt in.
- Use a **dedicated wallet** with only the funds you intend to trade. Do not use your main wallet.

---

## Disclaimer

This software is provided for educational purposes. Cryptocurrency trading involves significant risk. Past performance of any token does not guarantee future results. You are solely responsible for your own financial decisions. This is not financial advice.

---

## Links

- [Doma Protocol](https://doma.xyz)
- [Doma Explorer](https://explorer.doma.xyz)
- [web3guides.com — Doma Guides](https://web3guides.com/doma)
- [GitHub Issues](https://github.com/sdmikecfc/trading-bot/issues)
