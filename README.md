# Doma Protocol Sniper Bot

Automatically snipes token launches on the [Doma Protocol](https://doma.xyz) bonding curve — buying at the floor price the instant a token goes live.

Built and maintained by [@web3guides](https://web3guides.com) · Big Mike @sdmikecfc in Discord

---

## How It Works

Doma Protocol lets users fractionalize domain names into ERC-20 tokens. Each token launches on a **bonding curve** at a fixed floor price — the price can only go **up** from there. This bot:

1. Reads the public Doma launch schedule
2. Waits for your chosen token to go live
3. Buys instantly at the floor price (typically within 10–15 seconds of launch)

---

## Two Tools

| File | What it does |
|------|-------------|
| `snipe.py` | **Interactive** — guided setup, shows upcoming launches, you pick one (or all), it handles the rest. Start here. |
| `schedule_sniper.py` | **Automated** — runs continuously, auto-snipes every eligible launch based on your criteria. For advanced users. |

---

## Prerequisites

- **Python 3.11+** — [python.org/downloads](https://python.org/downloads)
- **A dedicated wallet** with USDC.e and a small amount of ETH on Doma chain
- **USDC.e on Doma chain** — bridge via [Across Protocol](https://across.to) or [Optimism Superbridge](https://superbridge.app)

> Gas on Doma chain is nearly free. Keep at least 0.01 ETH for comfortable headroom.

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/sdmikecfc/trading-bot.git
cd trading-bot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the sniper
python snipe.py
```

**Windows users:** double-click `launch.bat` instead of step 3. It checks your Python install and launches the bot automatically.

---

## First Run — Wallet Setup

When you run `snipe.py` for the first time, it walks you through a guided setup:

**Screen 1 — Welcome**
Introduces the tool and explains what's about to happen.

**Screen 2 — Big Mike Tips**
Five things to know before you connect a wallet — written in plain English, not tech jargon.

**Screen 3 — Wallet connection**
The bot creates a **private encrypted keystore file** on your machine. Here's what that means:

> *"We're going to create a private encrypted JSON file that is password protected. That way if someone ever gets access to the file, they won't be able to open it without your password."*

- Paste your private key or seed phrase — input is hidden, never shown on screen
- Choose a password (8+ characters)
- The bot encrypts your key with AES-256 (the same standard MetaMask uses internally) and saves it as `keystore.json`
- **Your key never leaves your machine.** It is never sent to any server, API, or website.

Every run after that: just enter your password and go straight to the launch table.

---

## Quick Start — Interactive Sniper

```bash
python snipe.py
```

After setup you'll see a table of upcoming launches:

```
  Upcoming launches — next 24 hours

  +------+--------------------------------+------------+----------------+-------------+---------+---------+
  |  #   | Domain                         | Launch UTC |  Starting FDV  | Bonding FDV | Buyout  | Spread  |
  +------+--------------------------------+------------+----------------+-------------+---------+---------+
  |  1   | chainspectre.com               | 10:00      |  $300          | $500        | $480    | 1.7x    |
  |  2   | tokentycoon.com                | 14:00      |  $750          | $1,200      | $1,100  | 1.6x    |
  +------+--------------------------------+------------+----------------+-------------+---------+---------+

  Pick a launch (number, domain name, or 'all'):
```

**Pick a single launch** — enter a number or domain name, set your amount, the bot counts down and fires automatically.

**Type `all`** — snipes every launch in the next 24 hours. Each launch runs in its own thread so none block the others. You set one per-launch amount, confirm the total, and the bot handles the rest. A results summary prints when everything finishes.

---

## Automated Sniper (Advanced)

`schedule_sniper.py` runs as a background process and handles all launches automatically based on your criteria.

Configure in `.env`:

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

## Understanding the Numbers

| Term | Meaning |
|------|---------|
| **Starting FDV** | Floor price × total supply. The minimum the market cap can be. |
| **Bonding FDV** | Target FDV when the bonding curve is full. Token graduates to Uniswap V3 at this point. |
| **Buyout** | Total USDC.e needed to fill the bonding curve completely. |
| **Spread** | Bonding FDV ÷ Starting FDV. Higher = more room for price appreciation before graduation. |
| **Graduation** | When the bonding curve fills completely. Token migrates to Uniswap V3 with real liquidity. |

**Why is the bonding curve safe to buy at?**
The price on the bonding curve can only go **up**. You buy at the current floor and every subsequent buyer pays more than you did. There is no slippage risk on the buy side.

---

## Security

- **Encrypted keystore** — your private key is stored AES-256 encrypted on your machine. Nobody can read it without your password.
- **Key never transmitted** — the bot signs transactions locally. Nothing is sent to any external server.
- **`keystore.json` is in `.gitignore`** — it cannot be accidentally committed or pushed to GitHub.
- **Per-snipe approvals** — the bot approves each launchpad contract individually, never a blanket approval.
- **Use a dedicated wallet** — create a fresh wallet just for sniping. Only fund it with what you plan to trade. You can move tokens to your main wallet any time after purchase.

---

## Disclaimer

This software is provided for educational purposes. Cryptocurrency trading involves significant risk. Past performance of any token does not guarantee future results. You are solely responsible for your own financial decisions. This is not financial advice.

---

## Links

- [Doma Protocol](https://doma.xyz)
- [Doma Explorer](https://explorer.doma.xyz)
- [web3guides.com — Doma Guides](https://web3guides.com/doma)
- [Doma Discord](https://discord.gg/doma)
- [GitHub Issues](https://github.com/sdmikecfc/trading-bot/issues)
