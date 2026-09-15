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

    def test_update_and_delete_nonexistent_holding_return_404(self, client):
        # Regression (follow-up audit, 2026-09-14): same missing-rowcount
        # class of bug just fixed for accounts/kids, found again here --
        # update_holding only ever checked the account_id FK, never
        # whether holding_id itself existed.
        acc = _create_account(client)
        r = client.put("/api/holdings/999999", json={
            "account_id": acc["id"], "security_name": "Ghost", "market_value": 0, "asset_class": "us_large_cap",
        })
        assert r.status_code == 404
        assert client.delete("/api/holdings/999999").status_code == 404

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


class TestHoldingsCsvImport:
    """Item 2: CSV import updates shares/value/value_date on a matched
    holding while preserving classification, cost basis, notes,
    management mode, and tax lots unless the row explicitly supplies a
    replacement -- and shows (without deciding) when an imported value
    differs materially from a quote already checked this session."""

    def _preview(self, client, csv_text):
        return client.post("/api/holdings/import/preview", files={"file": ("h.csv", csv_text.encode(), "text/csv")})

    def test_a_refresh_only_csv_with_no_asset_class_column_matches_and_previews_as_an_update(self, client):
        acc = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Index Fund", "ticker": "IDX",
            "market_value": 1000, "shares": 10, "asset_class": "us_large_cap", "cost_basis": 900, "notes": "core",
        }).json()
        csv_text = f"account_id,ticker,security_name,shares,market_value\n{acc['id']},IDX,Index Fund,11,1250\n"
        r = self._preview(client, csv_text)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid_count"] == 1
        row = body["rows"][0]
        assert row["import_action"] == "update"
        assert row["matching_holding_id"] == holding["id"]
        assert row["asset_class"] is None

    def test_a_new_holding_row_without_asset_class_is_invalid(self, client):
        acc = _create_account(client)
        csv_text = f"account_id,ticker,security_name,shares,market_value\n{acc['id']},NEW,Brand New Fund,5,500\n"
        r = self._preview(client, csv_text)
        body = r.json()
        assert body["valid_count"] == 0
        assert body["invalid_count"] == 1
        assert "asset_class is required" in body["errors"][0]["message"]

    def test_commit_preserves_classification_cost_basis_and_notes_when_omitted(self, client):
        acc = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Index Fund", "ticker": "IDX",
            "market_value": 1000, "shares": 10, "asset_class": "us_bonds", "cost_basis": 900, "notes": "core",
        }).json()
        r = client.post("/api/holdings/import/commit", json=[{
            "account_id": acc["id"], "ticker": "IDX", "security_name": "Index Fund",
            "shares": 11, "market_value": 1250,
        }])
        assert r.status_code == 200, r.text
        assert r.json() == {"created": 0, "updated": 1, "skipped": []}
        updated = client.get("/api/holdings").json()[0]
        assert updated["market_value"] == 1250
        assert updated["shares"] == 11
        assert updated["asset_class"] == "us_bonds"
        assert updated["cost_basis"] == 900
        assert updated["notes"] == "core"

    def test_commit_replaces_classification_and_notes_when_explicitly_supplied(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Index Fund", "ticker": "IDX",
            "market_value": 1000, "shares": 10, "asset_class": "us_bonds", "cost_basis": 900, "notes": "core",
        })
        r = client.post("/api/holdings/import/commit", json=[{
            "account_id": acc["id"], "ticker": "IDX", "security_name": "Index Fund",
            "shares": 11, "market_value": 1250, "asset_class": "us_large_cap", "cost_basis": 1100, "notes": "reclassified",
        }])
        assert r.status_code == 200, r.text
        updated = client.get("/api/holdings").json()[0]
        assert updated["asset_class"] == "us_large_cap"
        assert updated["cost_basis"] == 1100
        assert updated["notes"] == "reclassified"

    def test_commit_sets_value_date_from_the_row_or_defaults_to_today(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Index Fund", "ticker": "IDX",
            "market_value": 1000, "asset_class": "us_large_cap",
        })
        r = client.post("/api/holdings/import/commit", json=[{
            "account_id": acc["id"], "ticker": "IDX", "security_name": "Index Fund",
            "market_value": 1250, "value_date": "2026-08-01",
        }])
        assert r.status_code == 200, r.text
        assert client.get("/api/holdings").json()[0]["as_of_date"] == "2026-08-01"

        r2 = client.post("/api/holdings/import/commit", json=[{
            "account_id": acc["id"], "ticker": "IDX", "security_name": "Index Fund", "market_value": 1300,
        }])
        assert r2.status_code == 200, r2.text
        from datetime import date
        assert client.get("/api/holdings").json()[0]["as_of_date"] == date.today().isoformat()

    def test_commit_records_a_statement_source_and_high_confidence(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Guessed Fund", "market_value": 1000,
            "asset_class": "us_large_cap", "data_source": "manual", "confidence": "low",
        })
        client.post("/api/holdings/import/commit", json=[{
            "account_id": acc["id"], "security_name": "Guessed Fund", "market_value": 1100,
        }])
        updated = client.get("/api/holdings").json()[0]
        assert updated["data_source"] == "statement"
        assert updated["confidence"] == "high"

    def test_commit_preserves_management_mode_and_tax_lots(self, client):
        acc = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Managed Fund", "market_value": 1000,
            "asset_class": "us_large_cap", "management_mode": "externally_managed",
        }).json()
        client.post("/api/tax-lots", json={
            "holding_id": holding["id"], "acquired_date": "2024-01-15", "shares": 5, "cost_basis": 900,
        })
        client.post("/api/holdings/import/commit", json=[{
            "account_id": acc["id"], "security_name": "Managed Fund", "market_value": 1250,
        }])
        updated = client.get("/api/holdings").json()[0]
        assert updated["management_mode"] == "externally_managed"
        assert client.get(f"/api/holdings/{holding['id']}/tax-lots").json()[0]["cost_basis"] == 900

    def test_commit_preserves_shares_when_omitted(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Index Fund", "ticker": "IDX",
            "market_value": 1000, "shares": 10, "asset_class": "us_large_cap",
        })
        client.post("/api/holdings/import/commit", json=[{
            "account_id": acc["id"], "ticker": "IDX", "security_name": "Index Fund", "market_value": 1100,
        }])
        assert client.get("/api/holdings").json()[0]["shares"] == 10

    def test_commit_preserves_a_confirmed_provider_identifier_when_ticker_is_unchanged(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Index Fund", "ticker": "IDX",
            "provider_identifier": "MOCK:IDX", "market_value": 1000, "asset_class": "us_large_cap",
        })
        client.post("/api/holdings/import/commit", json=[{
            "account_id": acc["id"], "ticker": "IDX", "security_name": "Index Fund", "market_value": 1100,
        }])
        assert client.get("/api/holdings").json()[0]["provider_identifier"] == "MOCK:IDX"

    def test_commit_clears_provider_identifier_when_ticker_explicitly_changes(self, client):
        acc = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Index Fund", "ticker": "IDX",
            "provider_identifier": "MOCK:IDX", "market_value": 1000, "asset_class": "us_large_cap",
        }).json()
        client.post("/api/holdings/import/commit", json=[{
            "account_id": acc["id"], "ticker": "NEWTIX", "security_name": "Index Fund",
            "market_value": 1100, "asset_class": "us_large_cap",
        }])
        holdings = client.get("/api/holdings").json()
        assert len(holdings) == 1
        assert holdings[0]["ticker"] == "NEWTIX"
        assert holdings[0]["provider_identifier"] is None

    def test_commit_still_requires_asset_class_for_a_brand_new_holding(self, client):
        acc = _create_account(client)
        r = client.post("/api/holdings/import/commit", json=[{
            "account_id": acc["id"], "security_name": "New Fund", "market_value": 500,
        }])
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 0
        assert "asset_class" in r.json()["skipped"][0]["reason"]

    def test_commit_cannot_corrupt_an_existing_holding_with_an_invalid_asset_class(self, client):
        """Commit is an API boundary too; CSV preview may not be bypassed."""
        acc = _create_account(client)
        original = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Index Fund", "ticker": "IDX",
            "market_value": 1000, "asset_class": "us_large_cap",
        }).json()
        r = client.post("/api/holdings/import/commit", json=[{
            "account_id": acc["id"], "security_name": "Index Fund", "ticker": "IDX",
            "market_value": 1200, "asset_class": "made_up_class",
        }])
        assert r.status_code == 200, r.text
        assert r.json()["updated"] == 0
        assert "not one of" in r.json()["skipped"][0]["reason"]
        saved = next(h for h in client.get("/api/holdings").json() if h["id"] == original["id"])
        assert saved["asset_class"] == "us_large_cap"
        assert saved["market_value"] == 1000

    def test_commit_rejects_negative_values_and_malformed_dates(self, client):
        acc = _create_account(client)
        base = {"account_id": acc["id"], "security_name": "Index Fund", "ticker": "IDX", "asset_class": "us_large_cap"}
        negative = client.post("/api/holdings/import/commit", json=[{**base, "market_value": -1}])
        assert negative.status_code == 422
        malformed_date = client.post("/api/holdings/import/commit", json=[{**base, "market_value": 1000, "value_date": "not-a-date"}])
        assert malformed_date.status_code == 422

    def test_preview_flags_a_material_difference_from_a_quote_checked_this_session(self, client):
        from security_provider import MockSecurityProvider, set_active_provider
        set_active_provider(MockSecurityProvider())
        acc = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Vanguard Total Stock Market ETF", "ticker": "VTI",
            "provider_identifier": "MOCK:VTI", "shares": 10, "market_value": 2000, "asset_class": "us_large_cap",
        }).json()
        # Check a quote this session (mock price 275.40 * 10 shares = 2754.00) so it lands in the cache.
        client.get(f"/api/holdings/{holding['id']}/quote-preview")
        csv_text = f"account_id,ticker,security_name,shares,market_value\n{acc['id']},VTI,Vanguard Total Stock Market ETF,10,1000\n"
        r = self._preview(client, csv_text)
        row = r.json()["rows"][0]
        assert "quote_comparison" in row
        assert row["quote_comparison"]["quote_implied_value"] == 2754.0
        assert row["quote_comparison"]["imported_value"] == 1000
        assert row["quote_comparison"]["deviation_pct"] > 5

    def test_preview_does_not_flag_a_small_difference_from_a_cached_quote(self, client):
        from security_provider import MockSecurityProvider, set_active_provider
        set_active_provider(MockSecurityProvider())
        acc = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Vanguard Total Stock Market ETF", "ticker": "VTI",
            "provider_identifier": "MOCK:VTI", "shares": 10, "market_value": 2000, "asset_class": "us_large_cap",
        }).json()
        client.get(f"/api/holdings/{holding['id']}/quote-preview")  # 2754.00 implied
        csv_text = f"account_id,ticker,security_name,shares,market_value\n{acc['id']},VTI,Vanguard Total Stock Market ETF,10,2760\n"
        r = self._preview(client, csv_text)
        assert "quote_comparison" not in r.json()["rows"][0]

    def test_preview_never_fetches_a_fresh_quote_for_comparison(self, client):
        """Comparing against a quote is only ever a review of what was
        ALREADY checked this session -- CSV preview must never itself
        trigger a provider call."""
        from security_provider import SecurityProvider, set_active_provider

        class ExplodingProvider(SecurityProvider):
            def search(self, query):
                return []

            def get_quote(self, provider_identifier):
                raise AssertionError("CSV preview must never call the provider")

        set_active_provider(ExplodingProvider())
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Some Fund", "ticker": "ABC",
            "provider_identifier": "ANY:1", "shares": 10, "market_value": 2000, "asset_class": "us_large_cap",
        })
        csv_text = f"account_id,ticker,security_name,shares,market_value\n{acc['id']},ABC,Some Fund,10,1000\n"
        r = self._preview(client, csv_text)
        assert r.status_code == 200, r.text
        from security_provider import MockSecurityProvider
        set_active_provider(MockSecurityProvider())


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

    def test_allocation_reconciliation_only_lists_included_accounts(self, client):
        invested = _create_account(client, account_type="taxable", name="Included Brokerage")
        _create_account(client, account_type="checking", name="Checking Reserve")
        client.post("/api/holdings", json={
            "account_id": invested["id"], "security_name": "Index fund", "market_value": 90000,
            "asset_class": "us_large_cap",
        })
        body = client.get("/api/portfolio/allocation").json()
        issues = body["health"]["unreconciled_accounts"]
        assert [item["account_name"] for item in issues] == ["Included Brokerage"]
        assert issues[0]["difference"] == 10000

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


class TestTaxLotsAndQuotePreview:
    def test_tax_lots_are_scoped_to_a_holding_and_can_be_removed(self, client):
        account = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": account["id"], "security_name": "Index fund",
            "market_value": 1000, "asset_class": "us_large_cap",
        }).json()
        created = client.post("/api/tax-lots", json={
            "holding_id": holding["id"], "acquired_date": "2024-01-15",
            "shares": 5, "cost_basis": 900, "notes": "Initial purchase",
        })
        assert created.status_code == 200, created.text
        lot = created.json()
        stored = client.get(f"/api/holdings/{holding['id']}/tax-lots").json()
        assert len(stored) == 1
        assert {key: stored[0][key] for key in lot} == lot
        assert client.delete(f"/api/tax-lots/{lot['id']}").json() == {"deleted": lot["id"]}
        assert client.get(f"/api/holdings/{holding['id']}/tax-lots").json() == []

    def test_tax_lots_are_preserved_by_backup_restore(self, client):
        account = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": account["id"], "security_name": "Index fund",
            "market_value": 1000, "asset_class": "us_large_cap",
        }).json()
        client.post("/api/tax-lots", json={
            "holding_id": holding["id"], "acquired_date": "2024-01-15", "shares": 5, "cost_basis": 900,
        })
        payload = client.get("/api/backup/export").json()
        assert len(payload["tables"]["tax_lots"]) == 1
        restored = client.post("/api/backup/restore", params={"confirm": "true"},
                               files={"file": ("backup.json", __import__("json").dumps(payload), "application/json")})
        assert restored.status_code == 200, restored.text
        assert client.get(f"/api/holdings/{holding['id']}/tax-lots").json()[0]["cost_basis"] == 900

    def test_quote_preview_never_changes_a_holding_without_confirmation(self, client):
        from security_provider import MockSecurityProvider, set_active_provider
        set_active_provider(MockSecurityProvider())
        account = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": account["id"], "security_name": "Vanguard Total Stock Market ETF",
            "ticker": "VTI", "provider_identifier": "MOCK:VTI", "shares": 2,
            "market_value": 500, "asset_class": "us_large_cap",
        }).json()
        preview = client.get(f"/api/holdings/{holding['id']}/quote-preview")
        assert preview.status_code == 200, preview.text
        assert preview.json()["quote"]["price"] == 275.40
        assert preview.json()["implied_market_value"] == 550.80
        assert preview.json()["requires_confirmation"] is True

    def test_quote_preview_reports_source_liveness_and_offline_freshness(self, client):
        from security_provider import MockSecurityProvider, set_active_provider
        set_active_provider(MockSecurityProvider())
        account = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": account["id"], "security_name": "Vanguard Total Stock Market ETF",
            "ticker": "VTI", "provider_identifier": "MOCK:VTI", "shares": 2,
            "market_value": 500, "asset_class": "us_large_cap",
        }).json()
        preview = client.get(f"/api/holdings/{holding['id']}/quote-preview").json()
        assert preview["status"] == "ok"
        assert preview["source"] == "Built-in offline catalog"
        assert preview["is_live"] is False
        assert preview["freshness"] == "offline"
        assert preview["cached"] is False

    def test_second_quote_preview_in_one_session_is_served_from_cache(self, client):
        from security_provider import MockSecurityProvider, set_active_provider
        set_active_provider(MockSecurityProvider())
        account = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": account["id"], "security_name": "Vanguard Total Stock Market ETF",
            "ticker": "VTI", "provider_identifier": "MOCK:VTI", "shares": 2,
            "market_value": 500, "asset_class": "us_large_cap",
        }).json()
        first = client.get(f"/api/holdings/{holding['id']}/quote-preview").json()
        second = client.get(f"/api/holdings/{holding['id']}/quote-preview").json()
        assert first["cached"] is False
        assert second["cached"] is True

    def test_quote_preview_reports_a_specific_status_when_symbol_is_unrecognized(self, client):
        from security_provider import MockSecurityProvider, set_active_provider
        set_active_provider(MockSecurityProvider())
        account = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": account["id"], "security_name": "Old Delisted Thing",
            "ticker": "OLDCO", "provider_identifier": "MOCK:NOT_A_REAL_IDENTIFIER",
            "market_value": 500, "asset_class": "us_large_cap",
        }).json()
        preview = client.get(f"/api/holdings/{holding['id']}/quote-preview").json()
        assert preview["quote"] is None
        assert preview["status"] == "not_found"

    def test_quote_preview_reports_rate_limited_status_distinctly(self, client):
        from security_provider import SecurityProvider, QuoteResult, MockSecurityProvider, set_active_provider

        class RateLimitedProvider(SecurityProvider):
            def search(self, query):
                return []

            def get_quote(self, provider_identifier):
                return None

            def get_quote_with_status(self, provider_identifier):
                return QuoteResult(None, "rate_limited", "Throttled -- try again shortly.")

            @property
            def is_live(self):
                return True

        set_active_provider(RateLimitedProvider())
        account = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": account["id"], "security_name": "Some Fund",
            "ticker": "ABC", "provider_identifier": "ANY:1",
            "market_value": 500, "asset_class": "us_large_cap",
        }).json()
        preview = client.get(f"/api/holdings/{holding['id']}/quote-preview").json()
        assert preview["status"] == "rate_limited"
        assert preview["quote"] is None
        set_active_provider(MockSecurityProvider())

    def test_use_quote_value_records_a_high_confidence_provider_source(self, client):
        acc = _create_account(client)
        created = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Guessed Fund", "market_value": 1000,
            "asset_class": "us_large_cap", "data_source": "manual", "confidence": "low",
        }).json()
        refreshed = client.patch(f"/api/holdings/{created['id']}/valuation", json={
            "market_value": 1100, "as_of_date": "2026-09-13", "source": "quote",
        })
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["data_source"] == "provider_quote"
        assert refreshed.json()["confidence"] == "high"

    def test_manual_valuation_refresh_never_touches_data_source_or_confidence(self, client):
        acc = _create_account(client)
        created = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Guessed Fund", "market_value": 1000,
            "asset_class": "us_large_cap", "data_source": "manual", "confidence": "low",
        }).json()
        refreshed = client.patch(f"/api/holdings/{created['id']}/valuation", json={
            "market_value": 1100, "as_of_date": "2026-09-13",
        })
        assert refreshed.json()["data_source"] == "manual"
        assert refreshed.json()["confidence"] == "low"

    def test_valuation_update_rejects_an_unknown_source(self, client):
        acc = _create_account(client)
        created = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Fund", "market_value": 1000, "asset_class": "us_large_cap",
        }).json()
        r = client.patch(f"/api/holdings/{created['id']}/valuation", json={
            "market_value": 1100, "as_of_date": "2026-09-13", "source": "made_up",
        })
        assert r.status_code == 422
        assert client.get("/api/holdings").json()[0]["market_value"] == 1000


class TestHoldingsQfxImport:
    """QFX/OFX statement import (2026-09-14) -- keeps one custodian
    account's holdings and balance in sync from a single downloaded
    file. New positions default to asset_class='unclassified' (a
    statement never carries one); a matched/refresh row is untouched,
    exactly like the CSV import path."""

    SAMPLE_QFX = """OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX>
  <INVSTMTMSGSRSV1>
    <INVSTMTTRNRS>
      <INVSTMTRS>
        <DTASOF>20260911120000.000</DTASOF>
        <INVACCTFROM>
          <ACCTID>10596750.583014-01</ACCTID>
        </INVACCTFROM>
        <INVPOSLIST>
          <POSMF>
            <INVPOS>
              <SECID>
                <UNIQUEID>20602V101</UNIQUEID>
                <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
              </SECID>
              <UNITS>1900.474034</UNITS>
              <UNITPRICE>197.36</UNITPRICE>
              <MKTVAL>375077.55</MKTVAL>
              <DTPRICEASOF>20260911000000.000</DTPRICEASOF>
            </INVPOS>
          </POSMF>
          <POSMF>
            <INVPOS>
              <SECID>
                <UNIQUEID>09258N802</UNIQUEID>
                <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
              </SECID>
              <UNITS>2971.541261</UNITS>
              <UNITPRICE>23.18</UNITPRICE>
              <MKTVAL>68880.33</MKTVAL>
              <DTPRICEASOF>20260911000000.000</DTPRICEASOF>
            </INVPOS>
          </POSMF>
        </INVPOSLIST>
        <INV401KBAL>
          <TOTAL>443957.88</TOTAL>
        </INV401KBAL>
      </INVSTMTRS>
    </INVSTMTTRNRS>
  </INVSTMTMSGSRSV1>
  <SECLISTMSGSRSV1>
    <SECLIST>
      <MFINFO>
        <SECINFO>
          <SECID>
            <UNIQUEID>20602V101</UNIQUEID>
            <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
          </SECID>
          <SECNAME>Vanguard Institutional 500 Index Trust</SECNAME>
          <TICKER>20602V101</TICKER>
        </SECINFO>
      </MFINFO>
      <MFINFO>
        <SECINFO>
          <SECID>
            <UNIQUEID>09258N802</UNIQUEID>
            <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
          </SECID>
          <SECNAME>BlackRock Advantage Small Cap Core K</SECNAME>
          <TICKER>BDSKX</TICKER>
        </SECINFO>
      </MFINFO>
    </SECLIST>
  </SECLISTMSGSRSV1>
</OFX>
"""

    def _preview(self, client, account_id, qfx_text=None):
        return client.post(
            "/api/holdings/import/qfx/preview",
            files={"file": ("statement.qfx", (qfx_text or self.SAMPLE_QFX).encode(), "application/octet-stream")},
            data={"account_id": account_id},
        )

    def test_new_positions_default_to_unclassified_and_are_valid(self, client):
        acc = _create_account(client)
        r = self._preview(client, acc["id"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid_count"] == 2
        assert body["create_count"] == 2
        assert body["update_count"] == 0
        for row in body["rows"]:
            assert row["import_action"] == "create"
            assert row["asset_class"] == "unclassified"
            assert row["account_id"] == acc["id"]

    def test_account_balance_as_of_date_and_file_account_id_pass_through(self, client):
        acc = _create_account(client)
        body = self._preview(client, acc["id"]).json()
        assert body["account_balance"] == 443957.88
        assert body["as_of_date"] == "2026-09-11"
        assert body["account_id_in_file"] == "10596750.583014-01"

    def test_matched_position_previews_as_an_update_and_keeps_existing_classification(self, client):
        acc = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "BlackRock Advantage Small Cap Core K", "ticker": "BDSKX",
            "market_value": 60000, "shares": 2900, "asset_class": "us_small_cap",
        }).json()
        body = self._preview(client, acc["id"]).json()
        row = next(r for r in body["rows"] if r["ticker"] == "BDSKX")
        assert row["import_action"] == "update"
        assert row["matching_holding_id"] == holding["id"]
        assert row["asset_class"] is None  # untouched -- commit will keep "us_small_cap"

    def test_commit_reuses_the_generic_endpoint_unchanged(self, client):
        acc = _create_account(client)
        body = self._preview(client, acc["id"]).json()
        commit_rows = [{
            "account_id": r["account_id"], "ticker": r["ticker"], "security_name": r["security_name"],
            "shares": r["shares"], "market_value": r["market_value"], "asset_class": r["asset_class"],
            "value_date": r["value_date"],
        } for r in body["rows"]]
        r = client.post("/api/holdings/import/commit", json=commit_rows)
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["created"] == 2
        holdings = client.get("/api/holdings").json()
        assert {h["ticker"] for h in holdings} == {"20602V101", "BDSKX"}
        assert round(sum(h["market_value"] for h in holdings), 2) == 443957.88

    def test_unknown_account_id_returns_404(self, client):
        r = self._preview(client, 999999)
        assert r.status_code == 404

    def test_non_ofx_file_reports_a_clear_error_with_no_positions(self, client):
        acc = _create_account(client)
        r = self._preview(client, acc["id"], qfx_text="not a QFX file")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rows"] == []
        assert len(body["errors"]) == 1
        assert "QFX/OFX" in body["errors"][0]["message"]


class TestHoldingsSchwabCsvImport:
    """Schwab "Positions" CSV export import (2026-09-14) -- the
    brokerage-account counterpart to the QFX importer above. A brand
    new security defaults to asset_class='unclassified'; the file's own
    cash row defaults to 'cash' (unambiguous) on a create only, never on
    an update, so an existing classification is never clobbered."""

    SAMPLE_CSV = (
        '"Positions for account Sample Account ...999 as of 08:23 PM ET, 2026/09/14"\n'
        '\n'
        '"Symbol","Description","Qty (Quantity)","Price","Price Chng $ (Price Change $)",'
        '"Price Chng % (Price Change %)","Mkt Val (Market Value)","Day Chng $ (Day Change $)",'
        '"Day Chng % (Day Change %)","Cost Basis","Gain $ (Gain/Loss $)","Gain % (Gain/Loss %)",'
        '"Ratings","Reinvest?","Reinvest Capital Gains?","% of Acct (% of Account)","Asset Type",\n'
        '"ABCD","SAMPLE WIDGET CO","100","50.00","0.10","0.2%","$5,000.00","$10.00","0.2%",'
        '"$4,000.00","$1,000.00","25%","-","No","N/A","80%","Equity",\n'
        '"EFGH","SAMPLE BOND ETF","10","100.00","0.00","0%","$1,000.00","$0.00","0%",'
        '"$950.00","$50.00","5.26%","--","No","N/A","16%","ETFs & Closed End Funds",\n'
        '"Cash & Cash Investments","--","--","--","--","--","$250.00","$0.00","0%","--","--","--",'
        '"--","--","--","4%","Cash and Money Market",\n'
        '"Positions Total","","--","--","--","--","$6,250.00","$10.00","0.16%","$4,950.00",'
        '"$1,300.00","26.26%","--","--","--","--","--",\n'
    )

    def _preview(self, client, account_id, csv_text=None):
        return client.post(
            "/api/holdings/import/schwab-csv/preview",
            files={"file": ("positions.csv", (csv_text or self.SAMPLE_CSV).encode(), "text/csv")},
            data={"account_id": account_id},
        )

    def test_new_positions_default_to_unclassified_and_cash_defaults_to_cash(self, client):
        acc = _create_account(client)
        r = self._preview(client, acc["id"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid_count"] == 3
        assert body["create_count"] == 3
        by_ticker = {row["ticker"]: row for row in body["rows"]}
        assert by_ticker["ABCD"]["asset_class"] == "unclassified"
        assert by_ticker["ABCD"]["cost_basis"] == 4000.0
        assert by_ticker["CASH"]["asset_class"] == "cash"
        assert by_ticker["CASH"]["account_id"] == acc["id"]

    def test_account_balance_and_label_pass_through(self, client):
        acc = _create_account(client)
        body = self._preview(client, acc["id"]).json()
        assert body["account_balance"] == 6250.0
        assert body["as_of_date"] == "2026-09-14"
        assert body["account_label"] == "Sample Account ...999"

    def test_matched_cash_position_previews_as_an_update_and_keeps_existing_classification(self, client):
        acc = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Cash & Cash Investments", "ticker": "CASH",
            "market_value": 200, "asset_class": "cash",
        }).json()
        body = self._preview(client, acc["id"]).json()
        row = next(r for r in body["rows"] if r["ticker"] == "CASH")
        assert row["import_action"] == "update"
        assert row["matching_holding_id"] == holding["id"]
        assert row["asset_class"] is None  # untouched -- commit will keep "cash"

    def test_cash_row_matches_a_differently_labeled_existing_cash_holding(self, client):
        """Regression (2026-09-14, real production bug): a manually
        entered cash holding with no ticker and a different name than
        this importer's own "CASH"/"Cash & Cash Investments" convention
        must still be recognized as the same holding -- otherwise every
        re-import creates a second cash row and silently double-counts
        the account's cash."""
        acc = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Cash & Money Market", "ticker": None,
            "market_value": 200, "asset_class": "cash",
        }).json()
        body = self._preview(client, acc["id"]).json()
        row = next(r for r in body["rows"] if r["ticker"] == "CASH")
        assert row["import_action"] == "update"
        assert row["matching_holding_id"] == holding["id"]
        commit_rows = [{
            "account_id": r["account_id"], "ticker": r["ticker"], "security_name": r["security_name"],
            "shares": r["shares"], "market_value": r["market_value"], "cost_basis": r["cost_basis"],
            "asset_class": r["asset_class"], "value_date": r["value_date"],
        } for r in body["rows"]]
        client.post("/api/holdings/import/commit", json=commit_rows)
        cash_holdings = [h for h in client.get("/api/holdings").json() if h["asset_class"] == "cash"]
        assert len(cash_holdings) == 1  # updated in place, never duplicated
        assert cash_holdings[0]["id"] == holding["id"]

    def test_commit_reuses_the_generic_endpoint_unchanged(self, client):
        acc = _create_account(client)
        body = self._preview(client, acc["id"]).json()
        commit_rows = [{
            "account_id": r["account_id"], "ticker": r["ticker"], "security_name": r["security_name"],
            "shares": r["shares"], "market_value": r["market_value"], "cost_basis": r["cost_basis"],
            "asset_class": r["asset_class"], "value_date": r["value_date"],
        } for r in body["rows"]]
        r = client.post("/api/holdings/import/commit", json=commit_rows)
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 3
        holdings = client.get("/api/holdings").json()
        assert {h["ticker"] for h in holdings} == {"ABCD", "EFGH", "CASH"}
        assert round(sum(h["market_value"] for h in holdings), 2) == 6250.0

    def test_unknown_account_id_returns_404(self, client):
        r = self._preview(client, 999999)
        assert r.status_code == 404

    def test_file_with_no_symbol_header_reports_a_clear_error(self, client):
        acc = _create_account(client)
        r = self._preview(client, acc["id"], csv_text="not,a,schwab,export\n1,2,3,4\n")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rows"] == []
        assert len(body["errors"]) == 1
        assert "Positions export" in body["errors"][0]["message"]
