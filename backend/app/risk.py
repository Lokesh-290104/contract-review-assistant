"""Bonus: simple "needs attention" rules on top of the verified findings.

They are plain rules a business owner can read, not AI guesses.
"""

import re

_DAYS = re.compile(r"\(?(\d{1,3})\)?\s*(?:\(\w+\)\s*)?(?:calendar\s+|business\s+)?days", re.I)
_MONEY_OR_CAP = re.compile(r"[$₹€£]\s?\d|\b\d[\d,]*(\.\d+)?\s*(usd|inr|eur|gbp|rupees|dollars)\b|fees? (paid|payable)|amounts? paid", re.I)


def _max_days(text: str) -> int | None:
    days = [int(n) for n in _DAYS.findall(text)]
    return max(days) if days else None


def risk_flag(item_key: str, status: str, quote: str | None) -> str | None:
    q = quote or ""
    if status == "unclear":
        return "Could not be confirmed from the contract text; check this clause by hand."

    if item_key == "liability":
        if status == "not_found":
            return "No limitation of liability found: liability may be unlimited."
        if re.search(r"\bunlimited\b", q, re.I):
            return "Liability is described as unlimited."
        if not _MONEY_OR_CAP.search(q):
            return "Liability is limited, but no cap amount is stated in this clause."
    if item_key == "auto_renewal" and status == "found":
        return "Renews automatically: note the deadline for sending a non-renewal notice."
    if item_key == "termination":
        if status == "not_found":
            return "No termination clause found: it is unclear how either side can exit."
        days = _max_days(q)
        if days and days > 90:
            return f"Long notice period ({days} days) to end the contract."
    if item_key == "payment":
        if status == "not_found":
            return "No payment terms found."
        days = _max_days(q)
        if days and days > 60:
            return f"Long payment period ({days} days)."
    if item_key == "confidentiality" and status == "not_found":
        return "No confidentiality clause: shared information is not protected."
    if item_key == "governing_law" and status == "not_found":
        return "No governing law stated: disputes may be harder to resolve."
    return None
