"""Tests for estate_engine.py — federal estate tax exposure calculation."""
import pytest

from estate_engine import estate_tax_exposure, FEDERAL_ESTATE_EXEMPTION_PER_PERSON_2026


class TestEstateTaxExposure:
    def test_well_under_exemption_no_exposure(self):
        result = estate_tax_exposure(net_worth=3_000_000, life_insurance_death_benefit_total=1_500_000)
        assert result["exposure"] == 0
        assert result["gross_taxable_estate"] == 4_500_000

    def test_couple_gets_double_exemption(self):
        result = estate_tax_exposure(net_worth=1_000_000, filing_as_couple=True)
        assert result["exemption"] == FEDERAL_ESTATE_EXEMPTION_PER_PERSON_2026 * 2

    def test_individual_gets_single_exemption(self):
        result = estate_tax_exposure(net_worth=1_000_000, filing_as_couple=False)
        assert result["exemption"] == FEDERAL_ESTATE_EXEMPTION_PER_PERSON_2026

    def test_over_exemption_shows_real_exposure(self):
        exemption = FEDERAL_ESTATE_EXEMPTION_PER_PERSON_2026 * 2
        result = estate_tax_exposure(net_worth=exemption + 5_000_000, filing_as_couple=True)
        assert result["exposure"] == 5_000_000

    def test_life_insurance_counts_toward_taxable_estate(self):
        result = estate_tax_exposure(net_worth=1_000_000, life_insurance_death_benefit_total=2_000_000)
        assert result["gross_taxable_estate"] == 3_000_000

    def test_pct_of_exemption_used(self):
        exemption = FEDERAL_ESTATE_EXEMPTION_PER_PERSON_2026 * 2
        result = estate_tax_exposure(net_worth=exemption / 2, filing_as_couple=True)
        assert result["pct_of_exemption_used"] == pytest.approx(0.5, abs=0.01)

    def test_negative_net_worth_floors_at_zero(self):
        result = estate_tax_exposure(net_worth=-500000, life_insurance_death_benefit_total=0)
        assert result["gross_taxable_estate"] == 0
        assert result["exposure"] == 0

    def test_recommendation_mentions_nebraska_inheritance_tax_when_under_exemption(self):
        result = estate_tax_exposure(net_worth=3_000_000)
        assert "Nebraska" in result["recommendation"]

    def test_recommendation_mentions_estate_attorney_when_over_exemption(self):
        exemption = FEDERAL_ESTATE_EXEMPTION_PER_PERSON_2026 * 2
        result = estate_tax_exposure(net_worth=exemption + 5_000_000)
        assert "estate attorney" in result["recommendation"]
