"""Counterfactual capital expression. Existing safe sizing is an independent cap."""
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from .adaptive_policy import d


@dataclass(frozen=True)
class CapitalDecision:
    notional: Decimal
    risk_throttle: Decimal
    binding_caps: tuple[str, ...]
    factors: object
    allow_margin: bool = False
    entry_authority: bool = False


def govern_capital(*, base_safe_notional, cash, allocation_multiplier=1,
                   gross_envelope_fraction=1, hard_gross_envelope=0, existing_gross_exposure=0,
                   regime_factor=1, health_factor=1, drawdown_factor=1, evidence_factor=1,
                   hard_caps=None, allow_margin=False):
    if allow_margin is not False:
        raise ValueError("margin authority prohibited")
    factors = {k: d(v) for k,v in {"regime": regime_factor, "health": health_factor, "drawdown": drawdown_factor,
                                   "evidence": evidence_factor, "allocation": allocation_multiplier,
                                   "gross_fraction": gross_envelope_fraction}.items()}
    if any(not 0 <= value <= 1 for value in factors.values()):
        raise ValueError("capital factor outside [0,1]")
    base, available_cash, envelope, exposure = map(d, (base_safe_notional, cash, hard_gross_envelope, existing_gross_exposure))
    if any(v < 0 for v in (base, available_cash, envelope, exposure)):
        raise ValueError("negative capital input")
    throttle = min(factors[k] for k in ("regime", "health", "drawdown", "evidence"))
    caps = {"existing_safe_sizing": base, "cash": available_cash,
            "policy": base*factors["allocation"]*throttle,
            "effective_gross": max(Decimal(0), envelope*factors["gross_fraction"]-exposure)}
    for key, value in (hard_caps or {}).items():
        if d(value) < 0:
            raise ValueError("negative hard cap")
        caps[key] = d(value)
    final = min(caps.values())
    return CapitalDecision(final, throttle, tuple(k for k,v in caps.items() if v == final), MappingProxyType(factors))
