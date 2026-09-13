# Coach recommendation validation

Synthetic public-API tests in `tests/test_coach_realistic_workflows.py`
exercise recorded holdings, investment policies, and account fund menus.
They use the isolated test database, not household data.

## Corrections

1. An explicitly empty fund menu no longer bypasses rebalance destination
   checks. No sale is recommended without a recorded eligible purchase.
   The internal formula-only interface still accepts `None`; production
   routes pass a dictionary, including an empty one.
2. A per-class dollar recommendation requires a single-class fund. A
   $1,000 bond action cannot name a 60/40 stock/bond fund and claim $1,000
   of new bond exposure. Balanced funds remain supported by the separate
   account fund-mix solver, which uses their complete exposure vectors.
3. Each planned sale reserves its associated purchases immediately.
   Two holdings cannot both sell against the same unfilled destination
   gap. In a $20,000 household with $16,000 stocks and $4,000 bonds,
   targets of 50% stocks / 30% bonds / 20% international and only a bond
   menu permit $2,000 in paired trades, not $4,000 of sales funding only
   $2,000 of purchases. The projected total remains $20,000.
4. Dollar gaps use balances rather than rounded displayed percentages.
   With $7,000 stocks / $4,000 bonds and a 50/50 policy, the trade is
   $1,500, not $1,500.40.

## Acceptance checks

- Exact same-account buy/sell amounts and named destinations across
  401(k), Roth IRA, and taxable accounts; independently replayed balances.
- Missing fund menu, multi-class-only menu, and multiple sell candidates
  with limited destination capacity.
- A cheaper fund wins before its account is excluded and cannot win after.
- A minimum investment prevents a buy and its funding sale; adding an
  eligible alternative allows them; blocking the class prevents them again.
- No production data mutations, automatic trades, or changes to planning
  return/tax assumptions.

## Limits

This is a bounded recommendation-correctness pass, not certification of
every account restriction or investment strategy. Multi-class funds are
not yet implemented as direct per-class dollar trades. A missing eligible
destination remains unresolved rather than being substituted with a fund
whose exposures do not implement the requested action.

## Verification

- Focused Coach, holdings, and fund-menu suites: 186 passed.
- Full backend suite: 1,666 passed, 1 skipped; 95.68% coverage.
- Sensitive-data check passed, including separate checks of the new test
  and documentation files before they were tracked.
- No frontend code changed; no frontend test run was needed for this patch.
