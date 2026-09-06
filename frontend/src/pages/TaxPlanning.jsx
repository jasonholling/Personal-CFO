import { useState, useMemo, useEffect } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n)
const fmtK = (n) => isPrivacyMode() ? MASK_CURRENCY : (Math.abs(n) >= 1000000 ? `$${(n/1000000).toFixed(2)}M` : `$${(n/1000).toFixed(0)}K`)

// 2026 MFJ brackets (IRS Rev. Proc. 2025-32). Previous figures here
// (23850/96950/206700/... and STD_DED 30000, LTCG 96700/600050) were
// actually 2025's, mislabeled 2026 — caught by external audit 2026-09-05.
// Keep in sync with backend/retirement_tools_engine.py's
// ORDINARY_BRACKETS_MFJ_2026/STD_DEDUCTION_MFJ_2026.
const ORDINARY_2026 = [
  { rate:0.10, max:24800   },
  { rate:0.12, max:100800  },
  { rate:0.22, max:211400  },
  { rate:0.24, max:403550  },
  { rate:0.32, max:512450  },
  { rate:0.35, max:768700  },
  { rate:0.37, max:Infinity},
]
const LTCG_2026 = [
  { rate:0.00, max:98900   },
  { rate:0.15, max:613700  },
  { rate:0.20, max:Infinity},
]
const STD_DED  = 32200
const NIIT_THR = 250000
// Non-itemizer cash-charity deduction (OBBBA, effective 2026) — cash gifts
// to a qualifying charity are deductible up to this amount even when
// taking the standard deduction. Only applies to the "cash to DAF" mode,
// not stock. Verify the exact cap against the current-year IRS figure.
const NON_ITEMIZER_CASH_CAP = 2000

function marginalRate(income, brackets) {
  for (const b of brackets) if (income < b.max) return b.rate
  return brackets[brackets.length-1].rate
}

function calcLTCGTax(ordIncome, ltcg) {
  let tax = 0, rem = ltcg
  for (const b of LTCG_2026) {
    if (ordIncome >= b.max) continue
    const room = b.max - Math.max(ordIncome, b.max === Infinity ? ordIncome : LTCG_2026[LTCG_2026.indexOf(b)-1]?.max ?? 0)
    const taxable = Math.min(rem, room)
    tax += taxable * b.rate
    rem -= taxable
    if (rem <= 0) break
  }
  return tax
}

function roomBefore20(ordIncome) {
  return Math.max(0, LTCG_2026[1].max - ordIncome)
}

const DAF_OPTIONS = [
  { id:'none',   label:'No DAF' },
  { id:'cash',   label:'Donate Cash to DAF' },
  { id:'stock',  label:'Donate Stock to DAF ★' },
]

export default function TaxPlanning() {
  const [dafBalance, setDafBalance] = useState(0)

  // Taxable brokerage positions — read from your real Accounts (type
  // 'taxable') instead of fictional placeholder tickers. Cost basis isn't
  // tracked anywhere in the app yet (accounts store a single balance, not
  // per-lot basis), so that one field still has to be entered here each
  // visit — everything else (which accounts exist, current balance) is
  // real and never re-typed.
  const [stocks, setStocks] = useState([])
  const [basis, setBasis]   = useState({}) // { accountId: costBasisDollars }

  useEffect(() => {
    axios.get('/api/net-worth').then(r => {
      setDafBalance(r.data.daf || 0)
    }).catch(() => {})
    axios.get('/api/accounts').then(r => {
      const taxable = r.data.filter(a => a.account_type === 'taxable' && a.balance > 0)
      setStocks(taxable)
    }).catch(() => {})
    // Pre-fill W2/RSU from Settings instead of starting at 0 every visit —
    // both already exist in planning_inputs, still editable here.
    axios.get('/api/planning-inputs').then(r => {
      if (r.data.w2_salary) setW2(r.data.w2_salary)
      if (r.data.annual_rsu_value) setRsu(r.data.annual_rsu_value)
    }).catch(() => {})
  }, [])

  // Income
  const [w2,      setW2]      = useState(0)
  const [bonus,   setBonus]   = useState(0)
  const [rsu,     setRsu]     = useState(0)
  const [rental,  setRental]  = useState(0)
  const [vending, setVending] = useState(0)
  const [depreciation, setDepreciation] = useState(0)
  const [priorGains, setPrior] = useState(0)

  // Dollar amount to harvest from each taxable account, keyed by account id
  const [sell, setSell] = useState({})

  // DAF
  const [dafMode,   setDafMode]   = useState('none')
  const [dafAmount, setDafAmount] = useState(0)

  // ── Tax math ────────────────────────────────────────────────────────────────
  const calc = useMemo(() => {
    const grossIncome   = w2 + bonus + rsu + Math.max(0, rental) + vending + Math.min(0, rental)

    // Harvest from sliders — dollar amount to sell from each real taxable
    // account. gainFraction can go negative (an unrealized loss) — earlier
    // this was floored at 0, which meant a loss position could never be
    // represented at all. Gains and losses across all positions net
    // against each other first (same as the IRS treats it), only the
    // leftover after netting is capped/carried per the rules below.
    const harvestDetails = stocks.map(st => {
      const sellAmount   = sell[st.id] || 0
      const costBasis    = basis[st.id] || 0
      const gainFraction = st.balance > 0 ? (1 - costBasis / st.balance) : 0
      const gain         = sellAmount * gainFraction
      const proceeds     = sellAmount
      return { ...st, sellAmount, gain, proceeds }
    })

    const harvestGainGross = harvestDetails.reduce((s,h) => s + Math.max(0, h.gain), 0)
    const harvestLossGross = harvestDetails.reduce((s,h) => s + Math.max(0, -h.gain), 0)
    const harvestNet        = harvestGainGross - harvestLossGross

    // Net capital result for the year: prior realized gains + this year's
    // net harvest. Losses offset gains dollar-for-dollar first; anything
    // still negative after that offsets up to $3,000/yr of ORDINARY
    // income (a real AGI reduction, not just a capital-gains one), and
    // whatever's left beyond that carries forward to future years.
    const netCapitalResult     = priorGains + harvestNet
    const ordinaryLossOffset   = netCapitalResult < 0 ? Math.min(3000, -netCapitalResult) : 0
    const lossCarryforward     = netCapitalResult < 0 ? Math.max(0, -netCapitalResult - 3000) : 0
    const totalLTCG            = netCapitalResult  // kept for the DAF/harvest-gain math below, can be negative

    const agi = w2 + bonus + rsu + rental + vending - depreciation - ordinaryLossOffset  // rental loss + depreciation + harvested losses reduce income

    // Charitable deduction — choose standard vs. itemized rather than
    // stacking the charitable gift on top of the standard deduction.
    // Charitable is the only itemizable deduction this app models (no
    // mortgage interest/SALT tracked), so real-world itemizing may win at a
    // lower gift amount than this shows if those apply to you too.
    const cashDafDed     = dafMode === 'cash'  ? dafAmount : 0
    const stockDafDed    = dafMode === 'stock' ? dafAmount : 0
    const itemizedTotal  = cashDafDed + stockDafDed
    const itemizing      = itemizedTotal > STD_DED
    // Non-itemizers can still deduct a limited amount of cash charity
    // (OBBBA, 2026) — stock gifts get no equivalent break without itemizing.
    const nonItemizerCashDed = !itemizing && dafMode === 'cash'
      ? Math.min(cashDafDed, NON_ITEMIZER_CASH_CAP) : 0
    const totalDed       = itemizing ? itemizedTotal : STD_DED + nonItemizerCashDed
    const taxableOrd    = Math.max(0, agi - totalDed)

    const margOrd       = marginalRate(taxableOrd, ORDINARY_2026)
    const currentLTCGRate = marginalRate(taxableOrd, LTCG_2026)
    const room20        = roomBefore20(taxableOrd)
    const niitApplies   = agi > NIIT_THR

    const harvestProceeds = harvestDetails.reduce((s,h) => s + h.proceeds, 0)

    // If donating stock, those gains are avoided (only makes sense for
    // appreciated positions — donating a losing position instead of
    // selling it yourself forfeits the loss deduction, so this only ever
    // draws from the gain side). dafAmount's gain-proportion uses the same
    // gain/proceeds ratio as the harvested-gain positions.
    const gainsDonated    = dafMode === 'stock' && harvestGainGross > 0
      ? Math.min(harvestGainGross, dafAmount * (harvestGainGross / harvestProceeds || 0))
      : 0
    const taxableLTCG     = Math.max(0, totalLTCG - gainsDonated)

    const ltcgTax         = calcLTCGTax(taxableOrd, taxableLTCG)
    // NIIT is 3.8% of the LESSER of net investment income or the amount AGI
    // exceeds the threshold — not a flat 3.8% of all gains once over the
    // threshold. taxableLTCG is this page's only modeled investment income
    // (no interest/dividends tracked), so it stands in for NII here.
    const niitBase        = niitApplies ? Math.min(taxableLTCG, agi - NIIT_THR) : 0
    const niitTax         = Math.max(0, niitBase) * 0.038
    const totalGainsTax   = ltcgTax + niitTax

    // DAF benefit
    // The actual incremental deduction from donating is totalDed minus what
    // you'd take anyway (STD_DED) — not the full gift amount, since below
    // the standard deduction (and outside the non-itemizer cash cap) a
    // charitable gift buys zero additional deduction.
    const dafOrdSavings   = Math.max(0, totalDed - STD_DED) * margOrd
    const dafGainSavings  = dafMode === 'stock' ? gainsDonated * (currentLTCGRate + (niitApplies ? 0.038 : 0)) : 0
    const totalDafBenefit = dafOrdSavings + dafGainSavings

    const netProceeds     = harvestProceeds - totalGainsTax

    const overBracket     = Math.max(0, harvestGainGross - room20)
    const verdict = harvestGainGross === 0 ? null
      : overBracket > 0 ? 'over'
      : currentLTCGRate === 0 ? 'zero'
      : 'good'

    // Tax-loss harvesting: what the harvested losses are actually worth —
    // the ordinary-income offset at your marginal rate, plus whatever
    // gains tax they avoided by netting against gains above.
    const lossTaxSavings = ordinaryLossOffset * margOrd

    return {
      agi, taxableOrd, margOrd, currentLTCGRate, room20, niitApplies,
      harvestGain: harvestGainGross, harvestLoss: harvestLossGross, harvestProceeds,
      totalLTCG, totalGainsTax, ltcgTax, niitTax,
      ordinaryLossOffset, lossCarryforward, lossTaxSavings,
      netProceeds, overBracket, dafOrdSavings, dafGainSavings, totalDafBenefit,
      harvestDetails, verdict,
    }
  }, [w2, bonus, rsu, rental, vending, depreciation, priorGains, sell, basis, stocks, dafMode, dafAmount])

  const fillTo15 = (accountId) => {
    const st = stocks.find(s => s.id === accountId)
    if (!st) return
    const costBasis    = basis[accountId] || 0
    const gainFraction = st.balance > 0 ? Math.max(0, 1 - costBasis / st.balance) : 0
    if (gainFraction <= 0) return
    const currentHarvest = stocks
      .filter(s => s.id !== accountId)
      .reduce((sum, s) => {
        const sAmt = sell[s.id] || 0
        const sBasis = basis[s.id] || 0
        const sFrac = s.balance > 0 ? Math.max(0, 1 - sBasis / s.balance) : 0
        return sum + Math.max(0, sAmt * sFrac)
      }, 0)
    const room = Math.max(0, calc.room20 - currentHarvest - priorGains)
    const maxSellAmount = room / gainFraction
    setSell(s => ({ ...s, [accountId]: Math.min(maxSellAmount, st.balance) }))
  }

  const verdictBg    = calc.verdict === 'good' || calc.verdict === 'zero' ? 'rgba(52,211,153,0.08)' : calc.verdict === 'over' ? 'rgba(248,113,113,0.08)' : 'var(--bg3)'
  const verdictBorder= calc.verdict === 'good' || calc.verdict === 'zero' ? 'rgba(52,211,153,0.25)' : calc.verdict === 'over' ? 'rgba(248,113,113,0.25)' : 'var(--border)'
  const verdictColor = calc.verdict === 'good' || calc.verdict === 'zero' ? 'var(--green)' : calc.verdict === 'over' ? 'var(--red)' : 'var(--text2)'

  const verdictText = calc.verdict === 'zero'
    ? `✓ Excellent — all gains at 0% tax. You have ${fmt(calc.room20)} of room at 0% LTCG.`
    : calc.verdict === 'good'
    ? `✓ Good move — all gains taxed at 15%. Derisking ${fmt(calc.harvestProceeds)} of concentrated stock at a reasonable tax cost.`
    : calc.verdict === 'over'
    ? `⚠ Selling too much — ${fmt(calc.overBracket)} of gains will be taxed at 20% instead of 15%. Reduce shares or add a DAF contribution.`
    : 'Enter shares to sell above to see your analysis.'

  return (
    <div>
      <div style={{ marginBottom:28 }}>
        <h1 className="section-title">Tax Planning</h1>
        <p className="section-sub">2026 · Married Filing Jointly · Capital gains harvest optimizer</p>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:24 }}>

        {/* LEFT COLUMN */}
        <div>
          {/* Income */}
          <div className="card" style={{ marginBottom:16 }}>
            <div style={{ fontWeight:600, fontSize:14, marginBottom:16 }}>Your 2026 Income</div>
            {[
              { label:'W2 Salary',         val:w2,      set:setW2      },
              { label:'Bonus',             val:bonus,   set:setBonus   },
              { label:'RSU Vesting',       val:rsu,     set:setRsu     },
              { label:'J2 Properties',     val:rental,  set:setRental, hint:'negative = loss' },
              { label:'J2 Vending',        val:vending, set:setVending },
              { label:'J2 Properties Depreciation', val:depreciation, set:setDepreciation, hint:'Non-cash deduction · reduces ordinary income' },
              { label:'Prior gains this year', val:priorGains, set:setPrior, hint:'LTCG already realized' },
            ].map(f => (
              <div key={f.label} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'8px 0', borderBottom:'1px solid var(--border)' }}>
                <div>
                  <div style={{ fontSize:13 }}>{f.label}</div>
                  {f.hint && <div style={{ fontSize:10, color:'var(--text3)' }}>{f.hint}</div>}
                </div>
                <input type="number" value={f.val} onChange={e => f.set(parseFloat(e.target.value)||0)}
                  style={{ width:110, textAlign:'right', fontSize:13 }} />
              </div>
            ))}

            {/* Tax position summary */}
            <div style={{ marginTop:16, padding:12, background:'var(--bg3)', borderRadius:8 }}>
              <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
                <span style={{ fontSize:12, color:'var(--text2)' }}>Adjusted Gross Income</span>
                <span style={{ fontWeight:600, fontSize:13 }}>{fmt(calc.agi)}</span>
              </div>
              <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
                <span style={{ fontSize:12, color:'var(--text2)' }}>Taxable Ordinary Income</span>
                <span style={{ fontWeight:600, fontSize:13 }}>{fmt(calc.taxableOrd)}</span>
              </div>
              <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
                <span style={{ fontSize:12, color:'var(--text2)' }}>Your LTCG rate</span>
                <span style={{ fontWeight:700, fontSize:14, color: calc.currentLTCGRate === 0 ? 'var(--green)' : calc.currentLTCGRate === 0.15 ? 'var(--amber)' : 'var(--red)' }}>
                  {(calc.currentLTCGRate*100).toFixed(0)}%
                </span>
              </div>
              <div style={{ display:'flex', justifyContent:'space-between' }}>
                <span style={{ fontSize:12, color:'var(--text2)' }}>Room before 20% bracket</span>
                <span style={{ fontWeight:700, fontSize:13, color:'var(--green)' }}>{fmt(calc.room20)}</span>
              </div>
              {calc.niitApplies && (
                <div style={{ marginTop:6, fontSize:11, color:'var(--red)' }}>⚠ NIIT (3.8%) applies — AGI over $250k</div>
              )}
            </div>
          </div>

          {/* DAF */}
          <div className="card">
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:4 }}>
              <div style={{ fontWeight:600, fontSize:14 }}>Donor Advised Fund</div>
              {dafBalance > 0 && <div style={{ fontSize:12, color:'var(--green)', fontWeight:600 }}>Balance: {fmt(dafBalance)}</div>}
            </div>
            <div style={{ fontSize:12, color:'var(--text2)', marginBottom:12 }}>
              Charitable giving reduces your tax burden
            </div>
            <div style={{ display:'flex', gap:6, marginBottom:12, flexWrap:'wrap' }}>
              {DAF_OPTIONS.map(o => (
                <button key={o.id}
                  className={dafMode===o.id ? 'btn-primary' : 'btn-secondary'}
                  style={{ fontSize:12 }}
                  onClick={() => setDafMode(o.id)}
                >{o.label}</button>
              ))}
            </div>
            {dafMode !== 'none' && (
              <>
                <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:8 }}>
                  <span style={{ fontSize:13 }}>{dafMode === 'stock' ? 'FMV of stock donated' : 'Cash amount'}</span>
                  <input type="number" value={dafAmount} onChange={e => setDafAmount(parseFloat(e.target.value)||0)}
                    style={{ width:110, textAlign:'right' }} />
                </div>
                {dafMode === 'stock' && (
                  <div style={{ fontSize:12, color:'var(--green)', padding:'8px 10px', background:'rgba(52,211,153,0.08)', borderRadius:6 }}>
                    ★ Donating stock directly: you pay zero capital gains on donated shares AND get a full FMV deduction
                  </div>
                )}
                {calc.totalDafBenefit > 0 && (
                  <div style={{ marginTop:10, fontSize:12, color:'var(--text2)', lineHeight:1.8 }}>
                    {dafMode === 'cash' && <>Tax savings: <b style={{ color:'var(--green)' }}>{fmt(calc.dafOrdSavings)}</b> ({(calc.margOrd*100).toFixed(0)}% of {fmt(dafAmount)})</>}
                    {dafMode === 'stock' && (
                      <>
                        <div>Gains tax avoided: <b style={{ color:'var(--green)' }}>{fmt(calc.dafGainSavings)}</b></div>
                        <div>Income deduction: <b style={{ color:'var(--green)' }}>{fmt(calc.dafOrdSavings)}</b> ({(calc.margOrd*100).toFixed(0)}% of {fmt(dafAmount)})</div>
                        <div style={{ borderTop:'1px solid var(--border)', paddingTop:4, marginTop:4 }}>
                          Total DAF benefit: <b style={{ color:'var(--green)', fontSize:13 }}>{fmt(calc.totalDafBenefit)}</b>
                        </div>
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* RIGHT COLUMN */}
        <div>
          {/* Stock sliders */}
          <div className="card" style={{ marginBottom:16 }}>
            <div style={{ fontWeight:600, fontSize:14, marginBottom:4 }}>Amount to Sell</div>
            <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>
              Your real taxable brokerage accounts — enter each one's cost basis (not tracked elsewhere), then slide to pick how much to harvest this year.
            </div>

            {stocks.length === 0 ? (
              <div style={{ fontSize:13, color:'var(--text2)' }}>
                No taxable brokerage accounts found in Accounts — add one there to harvest gains from it here.
              </div>
            ) : stocks.map(st => {
              const costBasis    = basis[st.id] || 0
              const gainFraction = st.balance > 0 ? Math.max(0, 1 - costBasis / st.balance) : 0
              const sellAmount   = sell[st.id] || 0
              const gain         = sellAmount * gainFraction
              const pctSold      = st.balance > 0 ? Math.round(sellAmount / st.balance * 100) : 0
              return (
                <div key={st.id} style={{ marginBottom:20 }}>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:6 }}>
                    <div>
                      <span style={{ fontWeight:700, color:'var(--accent)', fontSize:14 }}>{st.name}</span>
                      <span style={{ fontSize:12, color:'var(--text3)', marginLeft:8 }}>{fmt(st.balance)} balance</span>
                    </div>
                    <div style={{ textAlign:'right' }}>
                      <div style={{ fontSize:13, fontWeight:600 }}>{fmt(sellAmount)} ({pctSold}%)</div>
                      {sellAmount > 0 && (
                        <div style={{ fontSize:11, color: gain < 0 ? 'var(--red)' : 'var(--amber)' }}>
                          {gain < 0 ? `loss: ${fmt(gain)}` : `gain: ${fmt(gain)}`}
                        </div>
                      )}
                    </div>
                  </div>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:8 }}>
                    <span style={{ fontSize:12, color:'var(--text2)' }}>Cost basis ($)</span>
                    <input type="number" value={costBasis} onChange={e => setBasis(b => ({ ...b, [st.id]: parseFloat(e.target.value) || 0 }))}
                      style={{ width:110, textAlign:'right', fontSize:12 }} placeholder="0" />
                  </div>
                  <input
                    type="range" min={0} max={st.balance} value={sellAmount}
                    onChange={e => setSell(s => ({ ...s, [st.id]: parseInt(e.target.value) }))}
                    style={{ width:'100%', accentColor:'var(--accent)', cursor:'pointer' }}
                  />
                  <div style={{ display:'flex', justifyContent:'space-between', fontSize:10, color:'var(--text3)', marginTop:2 }}>
                    <span>$0</span>
                    <button onClick={() => fillTo15(st.id)}
                      style={{ background:'none', border:'none', color:'var(--accent)', fontSize:11, cursor:'pointer', padding:0 }}>
                      Fill to 15% bracket →
                    </button>
                    <span>All {fmt(st.balance)}</span>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Results */}
          <div className="card" style={{ marginBottom:16 }}>
            <div style={{ fontWeight:600, fontSize:14, marginBottom:16 }}>Your Numbers</div>
            {[
              { label:'Total proceeds from sales',   value:fmt(calc.harvestProceeds),    color:'var(--text)',  bold:false },
              { label:'Capital gains realized',       value:fmt(calc.totalLTCG),          color:'var(--amber)', bold:false },
              { label:`Tax owed (${(calc.currentLTCGRate*100).toFixed(0)}% LTCG${calc.niitApplies?' + 3.8% NIIT':''})`, value:fmt(calc.totalGainsTax), color:'var(--red)', bold:false },
              { label:'DAF tax benefit',              value:calc.totalDafBenefit > 0 ? `+ ${fmt(calc.totalDafBenefit)}` : '—', color:'var(--green)', bold:false },
              null,
              { label:'Net proceeds after tax',       value:fmt(calc.netProceeds),        color:'var(--green)', bold:true  },
              { label:'Effective tax rate on gains',  value:calc.totalLTCG > 0 ? `${((calc.totalGainsTax / calc.totalLTCG)*100).toFixed(1)}%` : '—', color:'var(--text2)', bold:false },
            ].map((row, i) => row === null ? (
              <div key={i} style={{ height:1, background:'var(--border)', margin:'8px 0' }} />
            ) : (
              <div key={row.label} style={{ display:'flex', justifyContent:'space-between', padding:'7px 0', borderBottom:'1px solid var(--border)', fontSize:13 }}>
                <span style={{ color:'var(--text2)', fontWeight: row.bold ? 600 : 400 }}>{row.label}</span>
                <span style={{ color:row.color, fontWeight: row.bold ? 700 : 500 }}>{row.value}</span>
              </div>
            ))}
          </div>

          {/* Verdict */}
          <div style={{ padding:16, background:verdictBg, border:`1px solid ${verdictBorder}`, borderRadius:12, fontSize:13, color:verdictColor, fontWeight:500, lineHeight:1.6 }}>
            {verdictText}
            {calc.verdict === 'good' && calc.room20 > calc.harvestGain && (
              <div style={{ marginTop:8, fontSize:12, color:'var(--text2)' }}>
                You have {fmt(calc.room20 - calc.harvestGain)} more room at 15% this year.
              </div>
            )}
          </div>

          {/* Tax-loss harvesting — same sliders above, a position where
              cost basis is entered higher than the account's current
              balance shows up here instead of in the gain-harvest math. */}
          {calc.harvestLoss > 0 && (
            <div className="card" style={{ marginTop:16, borderTop:'3px solid var(--red)' }}>
              <div style={{ fontWeight:600, fontSize:14, marginBottom:12 }}>Tax-Loss Harvesting</div>
              <div style={{ display:'flex', justifyContent:'space-between', padding:'7px 0', borderBottom:'1px solid var(--border)', fontSize:13 }}>
                <span style={{ color:'var(--text2)' }}>Losses harvested</span>
                <span style={{ color:'var(--red)', fontWeight:600 }}>{fmt(calc.harvestLoss)}</span>
              </div>
              <div style={{ display:'flex', justifyContent:'space-between', padding:'7px 0', borderBottom:'1px solid var(--border)', fontSize:13 }}>
                <span style={{ color:'var(--text2)' }}>Offsets gains first, then ordinary income (up to $3,000/yr)</span>
                <span style={{ color:'var(--green)', fontWeight:600 }}>{fmt(calc.ordinaryLossOffset)}</span>
              </div>
              {calc.lossCarryforward > 0 && (
                <div style={{ display:'flex', justifyContent:'space-between', padding:'7px 0', borderBottom:'1px solid var(--border)', fontSize:13 }}>
                  <span style={{ color:'var(--text2)' }}>Carries forward to future years</span>
                  <span style={{ fontWeight:600 }}>{fmt(calc.lossCarryforward)}</span>
                </div>
              )}
              <div style={{ display:'flex', justifyContent:'space-between', padding:'7px 0', fontSize:13 }}>
                <span style={{ color:'var(--text2)', fontWeight:600 }}>Estimated tax savings</span>
                <span style={{ color:'var(--green)', fontWeight:700 }}>{fmt(calc.lossTaxSavings)}</span>
              </div>
              <div style={{ marginTop:10, padding:'8px 10px', background:'rgba(251,191,36,0.08)', borderRadius:6, fontSize:11, color:'var(--amber)' }}>
                ⚠ Wash-sale rule: don't buy the same or a substantially identical security within 30 days before or after selling at a loss, or the loss deduction is disallowed.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
