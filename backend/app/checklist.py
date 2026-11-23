"""The fixed review checklist, with the retrieval settings for each item.

Each item has:
- `query`: text that is embedded for vector search (describes what the clause says);
- `keywords`: regexes for the exact phrases lawyers use for this clause (keyword search);
- `question`: what the model must decide;
- `include_opening`: also give the model the first chunks (parties are named at the top).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ChecklistItem:
    key: str
    label: str
    query: str
    keywords: tuple[str, ...]
    question: str
    include_opening: bool = False


CHECKLIST: tuple[ChecklistItem, ...] = (
    ChecklistItem(
        key="parties",
        label="Parties",
        query="This agreement is entered into by and between the company and the client, the parties to this agreement",
        keywords=(r"\bby and between\b", r"\bbetween\b", r"\bparties\b", r'\((the )?["“]\w+["”]\)', r"\bhereinafter\b", r"\binc\.|\bllc\b|\bltd\b|\blimited\b|\bpvt\b"),
        question="Who are the parties to this contract (legal names and roles)?",
        include_opening=True,
    ),
    ChecklistItem(
        key="term",
        label="Start date and term",
        query="The effective date of this agreement and the term or duration for which it remains in force",
        keywords=(r"\beffective date\b", r"\bcommence", r"\bterm of\b", r"\bterm\b", r"\bduration\b", r"\bperiod of\b", r"\bexpire", r"\bdated\b"),
        question="When does the contract start, and how long does it last?",
        include_opening=True,
    ),
    ChecklistItem(
        key="payment",
        label="Payment terms",
        query="Fees, price, invoices and when payment is due, late payment interest",
        keywords=(r"\bpayment", r"\bfees?\b", r"\binvoice", r"\bpaid\b", r"\bdue\b", r"\bprice\b", r"\bcompensation\b", r"\bnet \d+\b", r"\$|₹|\binr\b|\busd\b|\beur\b"),
        question="What must be paid, how much, and when?",
    ),
    ChecklistItem(
        key="termination",
        label="Termination",
        query="Either party may terminate this agreement upon written notice or for material breach",
        keywords=(r"\bterminat", r"\bnotice\b", r"\bbreach\b", r"\bfor convenience\b", r"\bcancel"),
        question="How and when can each party end the contract (notice period, causes)?",
    ),
    ChecklistItem(
        key="liability",
        label="Limitation of liability",
        query="Limitation of liability: aggregate liability shall not exceed; no liability for indirect or consequential damages",
        keywords=(r"\blimitation of liability\b", r"\bliabilit", r"\bconsequential\b", r"\bindirect\b", r"\bshall not exceed\b", r"\baggregate\b", r"\bin no event\b"),
        question="Is liability limited or capped, and how (cap amount, excluded damages)?",
    ),
    ChecklistItem(
        key="confidentiality",
        label="Confidentiality",
        query="Confidential information must be kept secret and not disclosed to third parties",
        keywords=(r"\bconfidential", r"\bnon-disclosure\b", r"\bdisclos", r"\bproprietary\b", r"\btrade secret"),
        question="What confidentiality obligations apply, and for how long?",
    ),
    ChecklistItem(
        key="governing_law",
        label="Governing law",
        query="This agreement shall be governed by and construed in accordance with the laws of the state or country; jurisdiction of courts",
        keywords=(r"\bgoverned by\b", r"\bgoverning law\b", r"\blaws of\b", r"\bjurisdiction\b", r"\bcourts? of\b", r"\bvenue\b", r"\barbitration\b"),
        question="Which law governs the contract, and which courts or forum handle disputes?",
    ),
    ChecklistItem(
        key="auto_renewal",
        label="Auto-renewal",
        query="The agreement automatically renews for successive renewal terms unless either party gives notice of non-renewal",
        keywords=(r"\bautomatic(ally)? renew", r"\bauto-renew", r"\brenew", r"\bsuccessive\b", r"\bnon-renewal\b", r"\bevergreen\b", r"\bextend"),
        question="Does the contract renew automatically? On what terms, and how can renewal be stopped?",
    ),
)

ITEMS_BY_KEY = {item.key: item for item in CHECKLIST}
