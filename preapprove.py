"""
Pre-approval script — run once per wallet before your first live trade.

Sets a max ERC-20 allowance for USDC.e → router. This is a one-time setup.
After running this, the bot won't need to issue separate approval transactions.

Usage:
    python preapprove.py
"""

import sys

from eth_account import Account
from web3 import Web3

import config

ERC20_ABI = [
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "string"}]},
]

MAX_UINT256 = 2 ** 256 - 1


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


def main():
    w3 = Web3(Web3.HTTPProvider(config.RPC_URL))
    if not w3.is_connected():
        print(f"[ERROR] Cannot connect to {config.RPC_URL}")
        sys.exit(1)

    wallet, private_key = load_wallet()
    usdce_addr = Web3.to_checksum_address(config.USDCE_ADDRESS)
    router_addr = Web3.to_checksum_address(config.ROUTER_ADDRESS)

    usdce = w3.eth.contract(address=usdce_addr, abi=ERC20_ABI)

    try:
        symbol = usdce.functions.symbol().call()
    except Exception:
        symbol = "USDC.e"

    current_allowance = usdce.functions.allowance(wallet, router_addr).call()
    print(f"Wallet:           {wallet}")
    print(f"Token:            {symbol} ({usdce_addr})")
    print(f"Router:           {router_addr}")
    print(f"Current allowance: {current_allowance}")

    if current_allowance >= MAX_UINT256 // 2:
        print("[OK] Allowance already set to max. Nothing to do.")
        return

    print(f"\nApproving {symbol} → router for max amount...")

    if config.DRY_RUN:
        print("[DRY RUN] Would send approval transaction — set DRY_RUN=false to execute.")
        return

    gas_price = w3.eth.gas_price
    nonce = w3.eth.get_transaction_count(wallet)

    try:
        gas_est = usdce.functions.approve(router_addr, MAX_UINT256).estimate_gas({"from": wallet})
        gas_limit = int(gas_est * 1.5)
    except Exception:
        gas_limit = 80_000

    tx = usdce.functions.approve(router_addr, MAX_UINT256).build_transaction({
        "from": wallet,
        "nonce": nonce,
        "gas": gas_limit,
        "gasPrice": gas_price,
        "chainId": config.CHAIN_ID,
    })

    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Approval tx sent: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt.status == 1:
        print("[OK] Approval confirmed. You can now run the bot live.")
    else:
        print("[ERROR] Approval transaction failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
