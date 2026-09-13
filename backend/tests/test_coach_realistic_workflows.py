"""Public API checks with synthetic holdings; never use the live household DB."""
import pytest


def post(client, url, body):
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def household(client, account_type='401k'):
    account = post(client, '/api/accounts', dict(name='Workplace Plan', account_type=account_type,
                   owner='jason', institution='Test Provider', balance=10000))
    for name, asset_class, value in [('Stock Index', 'us_large_cap', 6000), ('Bond Index', 'us_bonds', 4000)]:
        post(client, '/api/holdings', dict(account_id=account['id'], security_name=name,
             asset_class=asset_class, market_value=value, cost_basis=value, confidence='high'))
    post(client, '/api/investment-policy', dict(target_us_large_cap_pct=50, target_us_bonds_pct=50))
    return account['id']


def option(client, account_id, name='Bond Fund', **extra):
    return post(client, '/api/account-investment-options', {
        'account_id': account_id, 'option_name': name, 'asset_class': 'us_bonds',
        'expense_ratio': 0.001, **extra})


@pytest.mark.parametrize('account_type', ['401k', 'roth_ira', 'taxable'])
def test_named_same_account_rebalance_conserves_money(client, account_type):
    aid = household(client, account_type)
    fund = option(client, aid)
    result = post(client, '/api/portfolio/rebalance', {'amount': 0})
    actions = result['rebalance_actions']
    assert [(a['action'], a['amount']) for a in actions] == [('sell', 1000), ('buy', 1000)]
    assert {a['account_id'] for a in actions} == {aid}
    assert actions[1]['destination']['option_id'] == fund['id']
    assert actions[1]['destination']['account_name'] == 'Workplace Plan'
    # Independently replay actual trades, rather than trusting the result summary.
    balances = {'us_large_cap': 6000, 'us_bonds': 4000}
    for action in actions:
        balances[action['asset_class']] += action['amount'] * (1 if action['action'] == 'buy' else -1)
    assert balances == {'us_large_cap': 5000, 'us_bonds': 5000}


def test_no_menu_does_not_propose_unfunded_or_unnamed_trades(client):
    household(client)
    result = post(client, '/api/portfolio/rebalance', {'amount': 0})
    assert result['rebalance_actions'] == []


def test_dollar_gap_uses_balances_not_rounded_display_percentages(client):
    aid = household(client)
    post(client, '/api/holdings', dict(account_id=aid, security_name='Extra Stock Fund',
         asset_class='us_large_cap', market_value=1000, cost_basis=1000, confidence='high'))
    option(client, aid)
    result = post(client, '/api/portfolio/rebalance', {'amount': 0})
    # $11,000 * 50% - $4,000 = $1,500, not $1,500.40 derived from 36.36%.
    assert [a['amount'] for a in result['rebalance_actions']] == [1500, 1500]


def test_two_sales_cannot_spend_the_same_destination_capacity(client):
    aid = household(client)
    post(client, '/api/holdings', dict(account_id=aid, security_name='Second Stock Fund',
         asset_class='us_large_cap', market_value=10000, cost_basis=10000, confidence='high'))
    post(client, '/api/investment-policy', dict(target_us_large_cap_pct=50,
         target_us_bonds_pct=30, target_international_developed_pct=20))
    option(client, aid)
    result = post(client, '/api/portfolio/rebalance', {'amount': 0})
    # $20k total: stocks exceed $10,000 target by $6,000, but the only
    # purchasable underweight is $2,000 of bonds. International has no menu.
    sales = sum(a['amount'] for a in result['rebalance_actions'] if a['action'] == 'sell')
    buys = sum(a['amount'] for a in result['rebalance_actions'] if a['action'] == 'buy')
    assert sales == buys == 2000
    assert result['projected_allocation']['total'] == 20000


def test_balanced_fund_is_not_a_pure_bond_destination(client):
    aid = household(client)
    option(client, aid, 'Balanced Fund', exposures=[
        {'asset_class': 'us_large_cap', 'weight_pct': 60},
        {'asset_class': 'us_bonds', 'weight_pct': 40},
    ])
    # $1,000 in this fund buys only $400 of bonds, not the claimed $1,000.
    result = post(client, '/api/portfolio/contribution-destination', {'amount': 1000})
    assert result['actions'][0]['destination'] is None
    assert post(client, '/api/portfolio/rebalance', {'amount': 0})['rebalance_actions'] == []


def test_excluded_cheaper_account_never_gets_new_money(client):
    aid = household(client)
    chosen = option(client, aid)
    excluded = post(client, '/api/accounts', dict(name='Excluded Reserve', account_type='taxable',
                    owner='joint', institution='Test Provider', balance=0))
    cheaper = option(client, excluded['id'], 'Unavailable Cheaper Fund', expense_ratio=0.0001)
    before = post(client, '/api/portfolio/contribution-destination', {'amount': 1000})
    assert before['actions'][0]['destination']['option_id'] == cheaper['id']
    post(client, '/api/investment-policy', dict(target_us_large_cap_pct=50, target_us_bonds_pct=50,
         excluded_accounts=[excluded['id']]))
    result = post(client, '/api/portfolio/contribution-destination', {'amount': 1000})
    assert sum(a['amount'] for a in result['actions']) == 1000
    assert result['actions'][0]['destination']['option_id'] == chosen['id']


def test_minimum_and_blocked_class_prevent_buy_and_its_funding_sale(client):
    aid = household(client)
    option(client, aid, minimum_investment=2000)
    assert post(client, '/api/portfolio/rebalance', {'amount': 0})['rebalance_actions'] == []
    option(client, aid, 'No Minimum Bond Fund')
    assert len(post(client, '/api/portfolio/rebalance', {'amount': 0})['rebalance_actions']) == 2
    post(client, '/api/investment-policy', dict(target_us_large_cap_pct=50, target_us_bonds_pct=50,
         account_constraints=[{'account_id': aid, 'excluded_asset_classes': ['us_bonds']}]))
    assert post(client, '/api/portfolio/contribution-destination', {'amount': 1000})['actions'][0]['destination'] is None
    assert post(client, '/api/portfolio/rebalance', {'amount': 0})['rebalance_actions'] == []
