"""Explicit mapping of champion diagnostics; unknown checks never become passes."""
def rejection_reasons(signal):
    checks = signal.metadata.get("checks", {})
    mapping = (("fast_above_slow", "MOMENTUM_FAIL"), ("rising", "MOMENTUM_FAIL"),
        ("multi_bar_persistent", "MOMENTUM_FAIL"), ("momentum_ok", "MOMENTUM_FAIL"),
        ("vwap_ok", "SIGNAL_BELOW_THRESHOLD"), ("vwap_extension_ok", "VWAP_EXTENSION_FAIL"),
        ("confirmations_ok", "CONFIRMATION_FAIL"), ("regime_ok", "REGIME_FAIL"))
    failed = tuple(dict.fromkeys(code for check,code in mapping if checks.get(check) is False))
    if failed:
        return failed
    if signal.reason in {"before entry window", "entry window closed", "weekend"}:
        return ("SESSION_NOT_AUTHORIZED",)
    if "not enough" in signal.reason or "insufficient" in signal.reason:
        return ("INSUFFICIENT_OBSERVATIONS",)
    return ("SIGNAL_BELOW_THRESHOLD",)
