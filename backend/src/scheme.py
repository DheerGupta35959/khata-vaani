"""PM SVANidhi eligibility lookups backed by a hand-built local dataset.

The dataset (scheme_data.json) summarizes published PM SVANidhi guidelines -
it is NOT a live government API. See the README for sources.
"""

import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "scheme_data.json"

# Returned to the LLM when the dataset cannot be loaded. The model is
# instructed to relay the exact fallback line and never invent an answer.
DATA_UNAVAILABLE = (
    "ELIGIBILITY DATA IS UNAVAILABLE. Tell the shopkeeper exactly this: "
    '"I can\'t check eligibility right now, please try again shortly." '
    "Do not provide any eligibility assessment, loan tier, or document list."
)


def load_data() -> dict:
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def check_eligibility(
    profile: dict,
    *,
    vending_certificate: bool | None = None,
    previous_loans: int | None = None,
) -> str:
    """Assess eligibility against the local dataset.

    `profile` is the shopkeeper's saved khata facts (sales activity signals).
    Returns a JSON string for the LLM, or DATA_UNAVAILABLE on load failure.
    """
    try:
        data = load_data()
    except (OSError, ValueError):
        return DATA_UNAVAILABLE

    has_sales = bool(profile.get("usual_items_sold")) or bool(profile.get("customers"))

    tier = 1 if previous_loans is None else min(max(previous_loans, 0) + 1, 3)
    tier_info = data["loan_tiers"][tier - 1]

    if vending_certificate is False:
        assessment = (
            "Criteria appear NOT fully met: a Certificate of Vending or a Letter of "
            "Recommendation (LoR) from the Urban Local Body is required."
        )
    elif has_sales:
        assessment = (
            "Criteria appear likely met based on saved khata activity (active vending). "
            "Still confirm the vending certificate/LoR and notified State/UT."
        )
    else:
        assessment = (
            "Cannot confirm yet - active street vending and a vending certificate/LoR "
            "still need to be confirmed."
        )

    result = {
        "scheme": data["scheme"]["name"],
        "as_of_date": data["scheme"]["as_of_date"],
        "source": data["scheme"]["source"],
        "eligibility_assessment": assessment,
        "likely_tier": f"Tier {tier}: {tier_info['label']} - up to Rs. {tier_info['max_amount']:,}",
        "tier_requirement": tier_info["requirement"],
        "document_checklist": data["eligibility"]["documents"],
        "terms": data["terms"],
        "note": (
            "Say out loud, after giving the result: 'Based on PM SVANidhi guidelines as of "
            f"{data['scheme']['as_of_date']}, this is not a live government check. Final "
            "approval happens through the official channel, not through Khata-Vaani.'"
        ),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    r = json.loads(check_eligibility({}, previous_loans=None))
    assert r["likely_tier"].startswith("Tier 1")
    r2 = json.loads(
        check_eligibility({"usual_items_sold": ["hair oil"]}, previous_loans=2)
    )
    assert r2["likely_tier"].startswith("Tier 3") and "50,000" in r2["likely_tier"]
    r3 = json.loads(check_eligibility({}, vending_certificate=False))
    assert "NOT fully met" in r3["eligibility_assessment"]
    assert "document_checklist" in r3
    print("self-check OK")
