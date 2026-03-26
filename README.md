# Doma Protocol Sniper Bot

Automatically snipes token launches on the [Doma Protocol](https://doma.xyz) bonding curve — buying at the floor price the instant a token goes live.

Built and maintained by [@web3guides](https://web3guides.com) · [web3guides.com/doma](https://web3guides.com/doma) · [discord.gg/doma](https://discord.gg/doma)

---

## How It Works

Doma Protocol lets users fractionalize domain names into ERC-20 tokens. Each token launches on a **bonding curve** at a fixed floor price — the price can only go **up** from there. This bot:

1. Reads the public Doma launch schedule
2. Waits for your chosen token to go live
3. Buys instantly at the floor price (typically within 10–15 seconds of launch)

---

## Before You Start

You will need:

- **A dedicated wallet** — create a brand new one in MetaMask or Rabby just for this. Never use your main wallet.
- **USDC.e on Doma chain** — bridge via [Across Protocol](https://across.to) or [Optimism Superbridge](https://superbridge.app)
- **A tiny amount of ETH on Doma chain** — for gas. Keep at least 0.01 ETH. Gas is nearly free on Doma chain.

> Not sure how to bridge? Ask in the [Doma Discord](https://discord.gg/doma) — the community is helpful.

---

## Installation

### Step 1 — Install Python

Python is the programming language this bot runs on. You only need to do this once.

**Windows**
1. Go to [python.org/downloads](https://python.org/downloads)
2. Click the big yellow **Download Python** button
3. Run the installer — **important:** check the box that says **"Add Python to PATH"** before clicking Install
4. Click Install Now

**Mac**
1. Go to [python.org/downloads](https://python.org/downloads)
2. Download and run the installer
3. Follow the prompts

**Linux**
```bash
sudo apt install python3 python3-pip    # Ubuntu / Debian
sudo dnf install python3 python3-pip   # Fedora
```

To verify it worked, open a terminal and run:
```
python --version
```
You should see something like `Python 3.12.0`. Anything 3.11 or higher is fine.

---

### Step 2 — Download the Bot

**Option A — Git (recommended)**

If you have Git installed:
```bash
git clone https://github.com/sdmikecfc/trading-bot.git
cd trading-bot
```

**Option B — Download ZIP**
1. Click the green **Code** button at the top of this page
2. Click **Download ZIP**
3. Unzip it somewhere easy to find (e.g. your Desktop)
4. Open a terminal in that folder

---

### Step 3 — Install Dependencies

In your terminal, inside the trading-bot folder:

**Windows / Mac / Linux**
```bash
pip install -r requirements.txt
```

If `pip` isn't recognised on Mac/Linux, try:
```bash
pip3 install -r requirements.txt
```

---

### Step 4 — Run the Bot

**Windows — easiest way:**
Double-click `launch.bat` in the trading-bot folder. It checks everything and launches the bot automatically.

**Windows (terminal) / Mac / Linux:**
```bash
python snipe.py
```

On Mac/Linux if that doesn't work:
```bash
python3 snipe.py
```

---

## First Run — Wallet Setup

The first time you run the bot it walks you through three screens:

**Screen 1 — Welcome**
Introduces the tool and what it does.

**Screen 2 — Big Mike Tips**
Five things to know before you connect a wallet, written in plain English.

**Screen 3 — Connect your wallet**

The bot will explain exactly what it's about to do:

> *"We're going to create a private encrypted JSON file that is password protected. That way if someone ever gets access to the file, they won't be able to open it without your password."*

- You paste your private key or 12-word seed phrase — it's hidden as you type, never shown on screen
- You choose a password (8+ characters)
- The bot encrypts your key and saves it as `keystore.json` on your machine
- **Your key never leaves your computer.** It is never sent to any server or website.

Every run after that: enter your password and go straight to the launch table. That's it.

---

## Using the Bot

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

**Pick one launch** — type a number (e.g. `1`) or the domain name. Set your amount, the bot counts down and fires automatically at launch time.

**Type `all`** — snipes every launch in the next 24 hours. Set one per-launch amount, confirm the total, and the bot handles everything in parallel. Press Ctrl+C at any time to cancel all.

---

## Understanding the Table

| Column | What it means |
|--------|--------------|
| **Starting FDV** | The minimum market cap at launch. This is the price you're buying at. |
| **Bonding FDV** | The market cap when the bonding curve is full and the token graduates. |
| **Buyout** | Total USDC.e needed to fill the bonding curve completely. |
| **Spread** | Bonding FDV ÷ Starting FDV. Higher = more room to grow before graduation. Highlighted green at 1.5x or above. |

**Why is the bonding curve safe to buy at?**
The price can only go **up**. You buy at the floor and every buyer after you pays more. There is no slippage risk on the buy side.

---

## Automated Sniper (Advanced)

`schedule_sniper.py` runs continuously in the background and auto-snipes every eligible launch based on your own filters. For advanced users.

Configure in `.env`:

```env
SNIPE_AMOUNT_USD=5.00        # How much to spend per snipe
SNIPE_DAILY_LIMIT=50.00      # Max spend per day
SNIPE_MAX_INITIAL_FDV=500    # Skip tokens with Starting FDV above this
SNIPE_MIN_SPREAD=1.5         # Minimum bonding curve spread required
DRY_RUN=false                # Set to false to trade real funds
```

```bash
python schedule_sniper.py
```

---

## Security

- **Encrypted keystore** — your key is stored AES-256 encrypted (the same standard MetaMask uses). Nobody can open it without your password.
- **Key never transmitted** — all signing happens locally on your machine. Nothing is sent to any external server.
- **`keystore.json` is blocked from Git** — it cannot be accidentally uploaded to GitHub.
- **Dedicated wallet** — only fund it with what you plan to trade. Move tokens to your main wallet any time after purchase.

---

## Need Help?

Join the [Doma Discord](https://discord.gg/doma) — real people, quick answers.

---

## Disclaimer

This software is provided for educational purposes. Cryptocurrency trading involves significant risk. Past performance does not guarantee future results. You are solely responsible for your own financial decisions. This is not financial advice.

---

## Links

- [Doma Protocol](https://doma.xyz)
- [Doma Explorer](https://explorer.doma.xyz)
- [web3guides.com — Doma Guides](https://web3guides.com/doma)
- [Doma Discord](https://discord.gg/doma)
- [GitHub Issues](https://github.com/sdmikecfc/trading-bot/issues)
