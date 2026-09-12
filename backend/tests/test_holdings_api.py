"""
/api/holdings CRUD (codex/portfolio-coach-recommendations). Via
TestClient against an isolated temp db (see conftest.py's
`client`/`temp_db` fixtures — never the real cfo.db).

Covers external review finding #4 (2026-09-12, commit 4812d84): a real
owned Holding had nowhere to persist a multi-asset `exposures` list --
the calculation engine supported it, but only synthetic dicts passed
directly to the pure function ever exercised it. This file proves the
field round-trips through the real API/DB and actually feeds
holdings_engine's allocation math end to end.
"""


def _create_account(client, account_type="taxable", name="Test Account"):
    r = client.post("/api/accounts", json={
        "name": name, "account_type": account_type, "owner": "jason", "institution": "Test Institution",
        "balance": 100000,
    })
    assert r.status_code == 200, r.text
    return r.json()


def _save_policy(client, **overrides):
    body = {
        "target_us_large_cap_pct": 60, "target_us_mid_cap_pct": 0, "target_us_small_cap_pct": 0,
        "target_international_developed_pct": 0, "target_emerging_markets_pct": 0,
        "target_us_bonds_pct": 40, "target_international_bonds_pct": 0, "target_cash_pct": 0,
        "target_real_estate_pct": 0, "target_alternatives_pct": 0, "drift_band_pct": 5,
    }
    body.update(overrides)
    r = client.post("/api/investment-policy", json=body)
    assert r.status_code == 200, r.text
    return r.json()


class TestHoldingsCrud:
    def test_fresh_install_has_zero_holdings(self, client):
        assert client.get("/api/holdings").json() == []

    def test_create_get_update_delete(self, client):
        acc = _create_account(client)
        created = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Vanguard 500 Index", "ticker": "VFIAX",
            "market_value": 50000, "asset_class": "us_large_cap", "expense_ratio": 0.0004, "cost_basis": 40000,
        }).json()
        assert created["id"] is not None
        assert created["exposures"] == []

        listed = client.get("/api/holdings").json()
        assert len(listed) == 1
        assert listed[0]["ticker"] == "VFIAX"

        updated = client.put(f"/api/holdings/{created['id']}", json={
            "account_id": acc["id"], "security_name": "Vanguard 500 Index", "ticker": "VFIAX",
            "market_value": 55000, "asset_class": "us_large_cap",
        }).json()
        assert updated["market_value"] == 55000

        client.delete(f"/api/holdings/{created['id']}")
        assert client.get("/api/holdings").json() == []

    def test_rejects_unknown_account_id(self, client):
        r = client.post("/api/holdings", json={
            "account_id": 999999, "security_name": "Fund", "market_value": 1000, "asset_class": "us_large_cap",
        })
        assert r.status_code == 400

    def test_rejects_negative_market_value(self, client):
        acc = _create_account(client)
        r = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Fund", "market_value": -100, "asset_class": "us_large_cap",
        })
        assert r.status_code == 422


class TestHoldingExposuresRoundTrip:
    """Reference tests #12/#16's holdings-side counterpart, now
    reachable through the real API/DB, not just a synthetic dict."""

    def test_multi_asset_exposures_persist_and_return(self, client):
        acc = _create_account(client)
        exposures = [{"asset_class": "us_large_cap", "weight_pct": 60}, {"asset_class": "us_bonds", "weight_pct": 40}]
        created = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Target Date 2050", "market_value": 100000,
            "asset_class": "us_large_cap", "exposures": exposures,
        }).json()
        assert created["exposures"] == exposures

        listed = client.get("/api/holdings").json()
        assert listed[0]["exposures"] == exposures

        grouped = client.get("/api/holdings/grouped").json()
        assert grouped["groups"][0]["holdings"][0]["exposures"] == exposures

    def test_exposures_update_round_trips(self, client):
        acc = _create_account(client)
        created = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Fund", "market_value": 10000, "asset_class": "us_large_cap",
        }).json()
        assert created["exposures"] == []
        new_exposures = [{"asset_class": "us_large_cap", "weight_pct": 70}, {"asset_class": "real_estate", "weight_pct": 30}]
        client.put(f"/api/holdings/{created['id']}", json={
            "account_id": acc["id"], "security_name": "Fund", "market_value": 10000,
            "asset_class": "us_large_cap", "exposures": new_exposures,
        })
        assert client.get("/api/holdings").json()[0]["exposures"] == new_exposures

    def test_multi_exposure_holding_feeds_real_allocation_math_end_to_end(self, client):
        """A target-date-style REAL holding (not a synthetic dict) is
        correctly split across asset classes in /api/portfolio/
        allocation's own current_allocation, via the exact same
        holding_exposure_weights() the pure-function tests exercise."""
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Target Date 2050", "market_value": 100000,
            "asset_class": "us_large_cap",
            "exposures": [{"asset_class": "us_large_cap", "weight_pct": 60}, {"asset_class": "us_bonds", "weight_pct": 40}],
        })
        body = client.get("/api/portfolio/allocation").json()
        assert body["current_allocation"]["by_class"]["us_large_cap"] == 60000
        assert body["current_allocation"]["by_class"]["us_bonds"] == 40000

    def test_included_in_backup_export_and_restore(self, client):
        acc = _create_account(client)
        exposures = [{"asset_class": "us_large_cap", "weight_pct": 50}, {"asset_class": "us_bonds", "weight_pct": 50}]
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Balanced Fund", "market_value": 20000,
            "asset_class": "us_large_cap", "exposures": exposures,
        })
        payload = client.get("/api/backup/export").json()
        r = client.post("/api/backup/restore", params={"confirm": "true"},
                         files={"file": ("b.json", __import__("json").dumps(payload), "application/json")})
        assert r.status_code == 200, r.text
        assert client.get("/api/holdings").json()[0]["exposures"] == exposures
