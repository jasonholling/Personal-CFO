"""
/api/kids CRUD (kids-variable-count, 2026-09-09) — a household can have
0-5 kids, replacing the old fixed kid1/kid2 planning_inputs columns.
Via TestClient against an isolated temp db (see conftest.py's
`client`/`temp_db` fixtures — never the real cfo.db).
"""


class TestKidsCrud:
    def test_fresh_install_has_zero_kids(self, client):
        r = client.get("/api/kids")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_get_update_delete(self, client):
        created = client.post("/api/kids", json={"name": "Riley", "age": 9, "monthly_529": 150}).json()
        assert created["name"] == "Riley"
        assert created["age"] == 9
        assert created["monthly_529"] == 150
        assert created["id"] is not None

        listed = client.get("/api/kids").json()
        assert len(listed) == 1
        assert listed[0]["name"] == "Riley"

        updated = client.put(f"/api/kids/{created['id']}", json={
            "name": "Riley B.", "age": 10, "monthly_529": 200, "display_order": 0,
        }).json()
        assert updated["name"] == "Riley B."
        assert updated["age"] == 10

        after_update = client.get("/api/kids").json()
        assert after_update[0]["name"] == "Riley B."

        client.delete(f"/api/kids/{created['id']}")
        assert client.get("/api/kids").json() == []

    def test_kids_are_returned_in_display_order(self, client):
        first  = client.post("/api/kids", json={"name": "First", "age": 5}).json()
        second = client.post("/api/kids", json={"name": "Second", "age": 3}).json()
        # A new kid appends after whatever the current max display_order
        # is — the second kid must not jump ahead of the first.
        assert second["display_order"] > first["display_order"]
        listed = client.get("/api/kids").json()
        assert [k["name"] for k in listed] == ["First", "Second"]

        # Re-adding after a delete still appends past the survivor.
        client.delete(f"/api/kids/{first['id']}")
        third = client.post("/api/kids", json={"name": "Third", "age": 1}).json()
        assert third["display_order"] > second["display_order"]

    def test_can_have_up_to_five_kids(self, client):
        for i in range(5):
            r = client.post("/api/kids", json={"name": f"Kid {i}", "age": i})
            assert r.status_code == 200, r.text
        assert len(client.get("/api/kids").json()) == 5

    def test_sixth_kid_rejected(self, client):
        for i in range(5):
            client.post("/api/kids", json={"name": f"Kid {i}", "age": i})
        r = client.post("/api/kids", json={"name": "One Too Many", "age": 1})
        assert r.status_code == 400
        assert len(client.get("/api/kids").json()) == 5

    def test_deleting_a_kid_does_not_delete_their_accounts(self, client):
        """Deleting a kid record is deliberately non-destructive — an
        account still owned by that kid's f"kid_{id}" key keeps its
        balance and stays correctly excluded from the parents' own net
        worth/retirement totals (is_kid_owner only checks the "kid_"
        prefix, not whether a kids row still exists), it just no longer
        surfaces under any kid's own projection."""
        kid = client.post("/api/kids", json={"name": "Riley", "age": 9}).json()
        client.post("/api/accounts", json={
            "name": "529", "account_type": "529", "owner": f"kid_{kid['id']}",
            "institution": "", "balance": 5000, "notes": None,
        })
        client.delete(f"/api/kids/{kid['id']}")
        accounts = client.get("/api/accounts").json()
        assert any(a["owner"] == f"kid_{kid['id']}" and a["balance"] == 5000 for a in accounts)
        nw = client.get("/api/net-worth").json()
        assert nw["kids_assets"] == 5000  # still excluded from the parents' own totals


class TestZeroToFiveKidsFeedProjections:
    """The whole point of kids-variable-count: 0 kids must not error or
    silently assume 2, and up to 5 kids must each get their own
    independent goal/kid entry — regression coverage that the old
    fixed-2 assumption is actually gone everywhere it used to live
    (education, kids, insurance)."""

    def _seed(self, client, sample_inputs, sample_accounts):
        r = client.put("/api/planning-inputs", json=sample_inputs)
        assert r.status_code == 200, r.text
        for a in sample_accounts:
            payload = {k: v for k, v in a.items() if k != "id"}
            client.post("/api/accounts", json=payload)

    def test_zero_kids_gives_empty_education_and_kids_projections(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        assert client.get("/api/projections/education").json()["goals"] == []
        assert client.get("/api/projections/kids").json()["kids"] == []

    def test_zero_kids_gives_zero_college_funding_gap_on_insurance(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        insurance = client.get("/api/projections/insurance").json()
        assert insurance["jason"]["college_funding"] == 0
        assert insurance["justin"]["college_funding"] == 0

    def test_five_kids_each_get_an_independent_goal(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        for i in range(5):
            client.post("/api/kids", json={"name": f"Kid {i}", "age": 5 + i, "monthly_529": 50 + i * 10})
        goals = client.get("/api/projections/education").json()["goals"]
        assert len(goals) == 5
        assert {g["current_age"] for g in goals} == {5, 6, 7, 8, 9}
        assert {g["monthly_contribution"] for g in goals} == {50, 60, 70, 80, 90}
        # Every goal's stable "child" key is unique -- no collisions.
        assert len({g["child"] for g in goals}) == 5

        kids = client.get("/api/projections/kids").json()["kids"]
        assert len(kids) == 5
        assert {k["child"] for k in kids} == {g["child"] for g in goals}

    def test_one_kid_is_not_treated_specially(self, client, sample_inputs, sample_accounts):
        """A single kid must behave like any other count -- no leftover
        two-kid-shaped code path silently expecting a second entry."""
        self._seed(client, sample_inputs, sample_accounts)
        client.post("/api/kids", json={"name": "Only", "age": 6, "monthly_529": 75})
        goals = client.get("/api/projections/education").json()["goals"]
        assert len(goals) == 1
        assert goals[0]["child_name"] == "Only"
        assert goals[0]["current_age"] == 6
