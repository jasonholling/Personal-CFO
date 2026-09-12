"""
/api/account-investment-options CRUD (codex/portfolio-coach-
recommendations). Via TestClient against an isolated temp db (see
conftest.py's `client`/`temp_db` fixtures — never the real cfo.db).

account_investment_options is a GENERAL account-specific investment-menu
model, not a 401(k)-only fund-menu model -- these tests deliberately use
a 401(k) (closed-menu) AND a Roth IRA (open-universe) account to prove
the same schema/endpoints serve both without special-casing either.
"""


def _create_account(client, account_type="401k", name="Test Account"):
    r = client.post("/api/accounts", json={
        "name": name, "account_type": account_type, "owner": "jason", "institution": "Test Institution",
        "balance": 10000,
    })
    assert r.status_code == 200, r.text
    return r.json()


class TestAccountInvestmentOptionsCrud:
    def test_fresh_install_has_zero_options(self, client):
        r = client.get("/api/account-investment-options")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_get_update_delete(self, client):
        acc = _create_account(client)
        created = client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Vanguard 500 Index", "ticker": "VFIAX",
            "asset_class": "us_large_cap", "expense_ratio": 0.0004,
            "available_for_new_contributions": True, "available_for_exchange": True,
        }).json()
        assert created["id"] is not None
        assert created["option_name"] == "Vanguard 500 Index"
        assert created["currently_owned"] is False

        listed = client.get("/api/account-investment-options").json()
        assert len(listed) == 1
        assert listed[0]["ticker"] == "VFIAX"

        updated = client.put(f"/api/account-investment-options/{created['id']}", json={
            "account_id": acc["id"], "option_name": "Vanguard 500 Index (closed to new money)",
            "ticker": "VFIAX", "asset_class": "us_large_cap",
            "available_for_new_contributions": False, "available_for_exchange": True,
        }).json()
        assert updated["available_for_new_contributions"] is False

        after_update = client.get("/api/account-investment-options").json()
        assert after_update[0]["available_for_new_contributions"] is False

        client.delete(f"/api/account-investment-options/{created['id']}")
        assert client.get("/api/account-investment-options").json() == []

    def test_filter_by_account_id(self, client):
        acc1 = _create_account(client, name="401k")
        acc2 = _create_account(client, account_type="roth_ira", name="Roth IRA")
        client.post("/api/account-investment-options", json={
            "account_id": acc1["id"], "option_name": "Fund A", "asset_class": "us_large_cap",
        })
        client.post("/api/account-investment-options", json={
            "account_id": acc2["id"], "option_name": "Fund B", "asset_class": "us_bonds",
        })
        acc1_only = client.get("/api/account-investment-options", params={"account_id": acc1["id"]}).json()
        assert len(acc1_only) == 1
        assert acc1_only[0]["option_name"] == "Fund A"

    def test_rejects_unknown_account_id(self, client):
        r = client.post("/api/account-investment-options", json={
            "account_id": 999999, "option_name": "Fund", "asset_class": "us_large_cap",
        })
        assert r.status_code == 400

    def test_rejects_unknown_asset_class(self, client):
        acc = _create_account(client)
        r = client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Fund", "asset_class": "not_a_real_class",
        })
        assert r.status_code == 422

    def test_ticker_optional_for_collective_trust(self, client):
        """Reference test #13: a no-ticker collective trust can be
        recorded and round-trips with ticker=None (never rejected or
        silently defaulted to a fabricated value)."""
        acc = _create_account(client)
        created = client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Stable Value Collective Trust",
            "ticker": None, "asset_class": "us_bonds",
        }).json()
        assert created["ticker"] is None
        listed = client.get("/api/account-investment-options").json()
        assert listed[0]["ticker"] is None

    def test_multi_asset_exposures_round_trip(self, client):
        """A target-date-style option's multi-class exposures list
        round-trips exactly (reference test #16's data model)."""
        acc = _create_account(client)
        exposures = [{"asset_class": "us_large_cap", "weight_pct": 60}, {"asset_class": "us_bonds", "weight_pct": 40}]
        created = client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Target Date 2050", "asset_class": "us_large_cap",
            "exposures": exposures,
        }).json()
        assert created["exposures"] == exposures
        listed = client.get("/api/account-investment-options").json()
        assert listed[0]["exposures"] == exposures

    def test_currently_owned_and_employer_match_flags_round_trip(self, client):
        acc = _create_account(client)
        created = client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Employer Stock Fund", "asset_class": "us_large_cap",
            "currently_owned": True, "employer_match_eligible": True,
        }).json()
        assert created["currently_owned"] is True
        assert created["employer_match_eligible"] is True
        listed = client.get("/api/account-investment-options").json()
        assert listed[0]["currently_owned"] is True
        assert listed[0]["employer_match_eligible"] is True

    def test_deleting_account_cascades_to_its_options(self, client):
        acc = _create_account(client)
        client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Fund", "asset_class": "us_large_cap",
        })
        client.delete(f"/api/accounts/{acc['id']}")
        assert client.get("/api/account-investment-options").json() == []

    def test_included_in_backup_export_and_restore(self, client):
        acc = _create_account(client)
        client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Fund", "ticker": "ABC", "asset_class": "us_large_cap",
        })
        payload = client.get("/api/backup/export").json()
        assert "account_investment_options" in payload["tables"]
        assert payload["tables"]["account_investment_options"][0]["ticker"] == "ABC"

        r = client.post("/api/backup/restore", params={"confirm": "true"},
                         files={"file": ("b.json", __import__("json").dumps(payload), "application/json")})
        assert r.status_code == 200, r.text
        assert len(client.get("/api/account-investment-options").json()) == 1


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


class TestAccountInvestmentOptionsCompare:
    """"Which mix best implements my household policy?" workflow."""

    def test_defaults_to_saved_policy_targets(self, client):
        acc = _create_account(client)
        client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Stock Index", "asset_class": "us_large_cap", "expense_ratio": 0.0003,
        })
        client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Bond Index", "asset_class": "us_bonds", "expense_ratio": 0.0005,
        })
        _save_policy(client)
        r = client.post("/api/account-investment-options/compare", json={"account_id": acc["id"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert sum(m["pct"] for m in body["mix"]) == 100
        assert body["unavailable_classes"] == []

    def test_no_policy_and_no_explicit_targets_errors(self, client):
        acc = _create_account(client)
        r = client.post("/api/account-investment-options/compare", json={"account_id": acc["id"]})
        assert r.status_code == 400

    def test_explicit_target_weights_override_policy(self, client):
        acc = _create_account(client)
        client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Bond Index", "asset_class": "us_bonds", "expense_ratio": 0.0005,
        })
        r = client.post("/api/account-investment-options/compare", json={
            "account_id": acc["id"], "target_weights": {"us_bonds": 100},
        })
        assert r.status_code == 200, r.text
        assert r.json()["mix"][0]["option_name"] == "Bond Index"

    def test_only_recorded_eligible_options_considered(self, client):
        """Reference tests #9/#10: an option closed to new money is not
        offered even though it's recorded for this account."""
        acc = _create_account(client, account_type="401k")
        client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Closed Fund", "asset_class": "us_large_cap",
            "available_for_new_contributions": False,
        })
        r = client.post("/api/account-investment-options/compare", json={
            "account_id": acc["id"], "target_weights": {"us_large_cap": 100},
        })
        body = r.json()
        assert body["mix"] == []
        assert body["unavailable_classes"] == ["us_large_cap"]
        assert body["is_closed_menu_account"] is True

    def test_unknown_account_404s(self, client):
        r = client.post("/api/account-investment-options/compare", json={
            "account_id": 999999, "target_weights": {"us_large_cap": 100},
        })
        assert r.status_code == 404
