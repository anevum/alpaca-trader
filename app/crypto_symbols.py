"""Normalize broker crypto pairs without changing equity symbols."""
from typing import Any


def normalized_crypto_row(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").upper()
    asset_class = str(row.get("asset_class") or "").lower()
    if asset_class and asset_class != "crypto":
        return row
    # Older broker snapshots can omit asset_class for these supported pairs.
    aliases = {"BTCUSD": "BTC/USD", "ETHUSD": "ETH/USD", "SOLUSD": "SOL/USD"}
    canonical = aliases.get(symbol, symbol)
    if asset_class == "crypto" and "/" not in canonical:
        for quote in ("USDT", "USDC", "USD"):
            if canonical.endswith(quote) and len(canonical) > len(quote):
                canonical = canonical[:-len(quote)] + "/" + quote
                break
    return {**row, "symbol": canonical} if canonical != symbol else row


def is_crypto_row(row: dict[str, Any]) -> bool:
    asset_class = str(row.get("asset_class") or "").lower()
    return asset_class == "crypto" or (
        not asset_class and "/" in str(normalized_crypto_row(row).get("symbol") or "")
    )
