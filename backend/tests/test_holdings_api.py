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
    def test_legacy_required_name_supports_manual_and_csv_saves(self, client, temp_db):
        import db
        account = _create_account(client, account_type="hsa")
        conn = db.get_db()
        conn.execute("DROP TABLE holdings")
        conn.execute("""CREATE TABLE holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            name TEXT NOT NULL, description TEXT, shares REAL,
            market_value REAL NOT NULL DEFAULT 0,
            asset_class TEXT NOT NULL DEFAULT 'unclassified',
            expense_ratio REAL, cost_basis REAL, notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )""")
        conn.execute("INSERT INTO holdings (account_id, name, market_value) VALUES (?, 'Existing fund', 75)", (account['id'],))
        conn.commit()
        conn.close()
        db.init_portfolio_coach_tables()
        row = dict(account_id=account['id'], security_name='Manual fund', ticker='TEST',
                   market_value=123.45, asset_class='us_small_cap')
        response = client.post('/api/holdings', json=row)
        assert response.status_code == 200, response.text
        response = client.post('/api/holdings/import/commit', json=[{**row, 'security_name': 'Imported fund'}])
        assert response.status_code == 200, response.text
        assert response.json() == {'created': 0, 'updated': 1, 'skipped': []}
        db.init_portfolio_coach_tables()
        conn = db.get_db()
        saved = [tuple(r) for r in conn.execute('SELECT name, security_name, market_value FROM holdings ORDER BY id')]
        conn.close()
        assert saved == [('Existing fund', 'Existing fund', 75),
                         ('Manual fund', 'Imported fund', 123.45)]

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

    def test_externally_managed_holding_round_trips_and_can_be_toggled(self, client):
        acc = _create_account(client)
        created = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Managed balanced fund",
            "market_value": 10000, "asset_class": "us_large_cap",
            "management_mode": "externally_managed",
        })
        assert created.status_code == 200, created.text
        holding_id = created.json()["id"]
        assert client.get("/api/holdings").json()[0]["management_mode"] == "externally_managed"

        changed = client.patch(f"/api/holdings/{holding_id}/management-mode", json={
            "management_mode": "self_directed",
        })
        assert changed.status_code == 200, changed.text
        assert changed.json()["management_mode"] == "self_directed"
        assert client.patch(f"/api/holdings/{holding_id}/management-mode", json={
            "management_mode": "unknown",
        }).status_code == 422

    def test_grouped_holdings_exposes_reconciliation_and_latest_holding_date(self, client):
        acc = _create_account(client)
        created = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Fund", "market_value": 25000,
            "asset_class": "us_large_cap", "as_of_date": "2026-09-10",
        })
        assert created.status_code == 200, created.text
        group = client.get("/api/holdings/grouped").json()["groups"][0]
        assert group["account_balance"] == 100000
        assert group["holdings_total"] == 25000
        assert group["unreconciled_remainder"] == 75000
        assert group["holdings_as_of_date"] == "2026-09-10"

    def test_refreshing_a_valuation_preserves_the_holding_identity(self, client):
        acc = _create_account(client)
        created = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Index fund", "ticker": "IDX",
            "market_value": 1000, "asset_class": "us_large_cap", "management_mode": "externally_managed",
        }).json()
        refreshed = client.patch(f"/api/holdings/{created['id']}/valuation", json={
            "market_value": 1234.56, "as_of_date": "2026-09-13",
        })
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["market_value"] == 1234.56
        assert refreshed.json()["as_of_date"] == "2026-09-13"
        assert refreshed.json()["ticker"] == "IDX"
        assert refreshed.json()["management_mode"] == "externally_managed"


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
