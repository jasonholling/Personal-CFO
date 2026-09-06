"""
Estate tax exposure — turns Estate Planning from a pure document/
beneficiary checklist into an actual calculator against the federal
estate tax exemption threshold.
"""
from typing import Dict

# Federal estate/gift tax exemption, per person, 2026 — verify against the
# current-year IRS figure before relying on this for real estate planning;
# same caveat as every other annually-adjusted constant in this app.
FEDERAL_ESTATE_EXEMPTION_PER_PERSON_2026 = 15_000_000

# Nebraska charges an INHERITANCE tax (not an estate tax) on what heirs
# receive, separate from and in addition to any federal estate tax — rates
# and exemptions vary by the heir's relationship to the deceased (spouses
# are exempt; children/close relatives get a small exemption then a low
# rate; more distant heirs get taxed higher). This isn't modeled here —
# flagged in the recommendation since it's a real, lower-threshold
# exposure this federal-only calculator would otherwise miss entirely.


def estate_tax_exposure(net_worth: float, life_insurance_death_benefit_total: float = 0,
                         filing_as_couple: bool = True) -> Dict:
    """Life insurance death benefits count toward the taxable estate unless
    owned by an irrevocable life insurance trust (ILIT) — included here by
    default since that's the common case without one."""
    gross_taxable_estate = max(0, net_worth) + max(0, life_insurance_death_benefit_total)
    exemption = FEDERAL_ESTATE_EXEMPTION_PER_PERSON_2026 * (2 if filing_as_couple else 1)
    exposure = max(0, gross_taxable_estate - exemption)
    pct_of_exemption_used = (gross_taxable_estate / exemption) if exemption > 0 else 0

    if exposure > 0:
        recommendation = (
            f"Your taxable estate (${gross_taxable_estate:,.0f}, including life insurance) is about "
            f"${exposure:,.0f} over the ${exemption:,.0f} federal exemption "
            f"{'for a married couple (with portability)' if filing_as_couple else 'for an individual'} — "
            f"worth a conversation with an estate attorney about trusts or gifting strategies to reduce the "
            f"taxable estate. This federal exemption is scheduled to change over time, so revisit this annually."
        )
    else:
        recommendation = (
            f"Your taxable estate (${gross_taxable_estate:,.0f}, including life insurance) is well under the "
            f"${exemption:,.0f} federal exemption {'for a married couple' if filing_as_couple else 'for an individual'} "
            f"— using {pct_of_exemption_used*100:.1f}% of it. Federal estate tax isn't a near-term concern, "
            f"but check Nebraska's inheritance tax separately: it applies to what heirs receive (not the estate "
            f"itself), has a much lower threshold than the federal exemption, and isn't calculated here."
        )

    return {
        "net_worth": round(net_worth),
        "life_insurance_included": round(life_insurance_death_benefit_total),
        "gross_taxable_estate": round(gross_taxable_estate),
        "exemption": exemption,
        "filing_as_couple": filing_as_couple,
        "exposure": round(exposure),
        "pct_of_exemption_used": round(pct_of_exemption_used, 4),
        "recommendation": recommendation,
    }
