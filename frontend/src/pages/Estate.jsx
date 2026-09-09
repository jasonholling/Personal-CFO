import { useState, useEffect } from 'react'
import axios from 'axios'
import { usePersonNames } from '../hooks/usePersonNames'
import { useKids } from '../hooks/useKids'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(n))

// Seed templates only — every real value (dates, statuses, beneficiaries) is
// edited in place and persisted to localStorage per-browser, never to source.
const documentDefaults = (n) => [
  { id:'trust',    label:'Joint Revocable Living Trust',   date:'', status:'verify' },
  { id:'wills',    label:`Wills (${n.person1Name} & ${n.person2Name})`, date:'', status:'verify' },
  { id:'fpoa',     label:'Financial Power of Attorney',     date:'', status:'verify' },
  { id:'hcpoa',    label:'Health Care Power of Attorney',   date:'', status:'verify' },
  { id:'freeze',   label:'Credit Freeze (all 3 bureaus)',   date:'', status:'verify' },
]

// Kids-variable-count (2026-09-09): "contingent" used to hardcode
// "{kid1Name} & {kid2Name} equally" and a fixed kid1roth/kid2roth pair
// -- generalized to however many kids currently exist (0-5), joined
// with "&" the same way Education.jsx's namesList does. These are seed
// TEMPLATES only (see module comment above) -- once a household edits
// a row, it's persisted to localStorage and this function is never
// called again for that row, so an existing saved estate plan is
// unaffected by kids being added/removed/renamed later.
const kidsEqually = (kids) => {
  if (kids.length === 0) return ''
  if (kids.length === 1) return kids[0].name
  const names = kids.map(k => k.name)
  return `${names.slice(0, -1).join(', ')} & ${names[names.length - 1]} equally`
}

const accountDefaults = (n, kids) => [
  { id:'empower',  label:'401k',                    primary:`${n.person1Name} & ${n.person2Name} Trust`, contingent:kidsEqually(kids) },
  { id:'schwab',   label:'Brokerage',               primary:`${n.person1Name} & ${n.person2Name} Trust`, contingent:kidsEqually(kids) },
  { id:'person1roth',label:`${n.person1Name} Roth IRA`, primary:n.person2Name,           contingent:kidsEqually(kids) },
  { id:'person2ira',label:`${n.person2Name} Traditional IRA`, primary:n.person1Name,     contingent:kidsEqually(kids) },
  { id:'hsa',      label:'HSA',                     primary:n.person2Name,               contingent:'' },
  { id:'person1life',label:`${n.person1Name} Life Insurance`, primary:`${n.person1Name} & ${n.person2Name} Trust`, contingent:kidsEqually(kids) },
  { id:'person2life',label:`${n.person2Name} Life Insurance`, primary:n.person1Name,     contingent:kidsEqually(kids) },
  ...kids.map(k => ({ id:`kid${k.id}roth`, label:`${k.name} Roth IRA`, primary:`${n.person1Name} & ${n.person2Name}`, contingent:'' })),
]

const StatusBadge = ({ status }) => {
  const map = {
    executed:   { label:'Executed',        color:'var(--green)'  },
    verify:     { label:'Verify',          color:'var(--amber)'  },
    outdated:   { label:'Needs Update',    color:'var(--red)'    },
    pending:    { label:'Pending',         color:'var(--amber)'  },
  }
  const s = map[status] || map.verify
  return <span style={{ fontSize:11, fontWeight:600, color:s.color }}>● {s.label}</span>
}

export default function Estate() {
  const personNames = usePersonNames()
  const { kids, loading: kidsLoading } = useKids()
  const [docs,  setDocs]  = useState(() => {
    try { return JSON.parse(localStorage.getItem('estate_docs') || 'null') || documentDefaults(personNames) } catch { return documentDefaults(personNames) }
  })
  const [benes, setBenes] = useState(() => {
    try { return JSON.parse(localStorage.getItem('estate_benes') || 'null') || accountDefaults(personNames, []) } catch { return accountDefaults(personNames, []) }
  })

  // useKids() fetches asynchronously, so on first render (no
  // localStorage yet) the lazy initializer above ran before `kids`
  // arrived, producing a template with zero kid-Roth rows even for a
  // household that has kids. Once kids finishes loading, regenerate the
  // defaults -- but only if nothing's been saved yet (a household that
  // already edited/saved their beneficiary list must never have it
  // silently regenerated out from under them).
  useEffect(() => {
    if (kidsLoading) return
    if (localStorage.getItem('estate_benes')) return
    setBenes(accountDefaults(personNames, kids))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kidsLoading])
  const [tasks, setTasks] = useState([
    { id:1, text:'Review and update estate documents', done:false },
    { id:2, text:'Verify beneficiary designations on all accounts', done:false },
    { id:3, text:'Confirm credit freeze active at Equifax, Experian, TransUnion', done:false },
    { id:4, text:'Verify accounts and real estate titled in the trust', done:false },
  ])
  const [saved, setSaved] = useState(false)
  const [editDoc, setEditDoc]   = useState(null)
  const [editBene, setEditBene] = useState(null)
  const [taxExposure, setTaxExposure] = useState(null)

  useEffect(() => {
    axios.get('/api/estate/tax-exposure').then(r => setTaxExposure(r.data)).catch(() => {})
  }, [])

  const save = () => {
    localStorage.setItem('estate_docs',  JSON.stringify(docs))
    localStorage.setItem('estate_benes', JSON.stringify(benes))
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  const updateDoc = (id, field, val) => setDocs(d => d.map(x => x.id===id ? {...x, [field]:val} : x))
  const updateBene = (id, field, val) => setBenes(b => b.map(x => x.id===id ? {...x, [field]:val} : x))

  // Check if any doc is older than 5 years
  const needsReview = docs.some(d => {
    if (!d.date) return false
    const parts = d.date.split('/')
    if (parts.length < 3) return false
    const year = parseInt(parts[2])
    return (new Date().getFullYear() - year) >= 5
  })

  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:28 }}>
        <div>
          <h1 className="section-title">Estate Planning</h1>
          <p className="section-sub">Documents, beneficiaries, and action items</p>
        </div>
        <button className="btn-primary" onClick={save}>{saved ? '✓ Saved' : 'Save Changes'}</button>
      </div>

      {needsReview && (
        <div style={{ padding:'12px 16px', background:'rgba(251,191,36,0.08)', border:'1px solid rgba(251,191,36,0.2)', borderRadius:8, marginBottom:24, fontSize:13, color:'var(--amber)', fontWeight:500 }}>
          ⚠ One or more documents are 5+ years old — review recommended
        </div>
      )}

      {/* Estate Tax Exposure — computed from your real net worth, unlike
          the checklist below which is a status tracker only. */}
      {taxExposure && (
        <div className="card" style={{ marginBottom:20, borderTop:`3px solid ${taxExposure.exposure > 0 ? 'var(--red)' : 'var(--green)'}` }}>
          <div className="label" style={{ marginBottom:16 }}>Estate Tax Exposure</div>
          <div style={{
            padding:'12px 16px', borderRadius:8, fontSize:13, lineHeight:1.6, marginBottom:16,
            background: taxExposure.exposure > 0 ? 'rgba(248,113,113,0.08)' : 'rgba(52,211,153,0.08)',
          }}>
            {taxExposure.recommendation}
          </div>
          <div className="grid-3">
            <div>
              <div className="label">Taxable Estate</div>
              <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{fmt(taxExposure.gross_taxable_estate)}</div>
            </div>
            <div>
              <div className="label">Federal Exemption ({taxExposure.filing_as_couple ? 'Couple' : 'Individual'})</div>
              <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{fmt(taxExposure.exemption)}</div>
            </div>
            <div>
              <div className="label">Amount Over Exemption</div>
              <div style={{ fontSize:16, fontWeight:600, marginTop:4, color: taxExposure.exposure > 0 ? 'var(--red)' : 'var(--green)' }}>
                {fmt(taxExposure.exposure)}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Documents */}
      <div className="card" style={{ marginBottom:20 }}>
        <div style={{ fontWeight:700, fontSize:14, marginBottom:16, paddingBottom:10, borderBottom:'1px solid var(--border)' }}>Estate Documents</div>
        <table style={{ width:'100%', borderCollapse:'collapse', fontSize:13 }}>
          <thead>
            <tr style={{ borderBottom:'2px solid var(--border)' }}>
              {['Document','Last Updated','Status',''].map(h => (
                <th key={h} style={{ padding:'6px 8px', textAlign:'left', fontSize:11, color:'var(--text3)', fontWeight:600, textTransform:'uppercase', letterSpacing:'0.06em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {docs.map((doc, i) => (
              <tr key={doc.id} style={{ borderBottom:'1px solid var(--border)', background: i%2===0?'transparent':'var(--bg3)' }}>
                <td style={{ padding:'10px 8px', fontWeight:500 }}>{doc.label}</td>
                <td style={{ padding:'10px 8px' }}>
                  {editDoc === doc.id ? (
                    <input type="text" value={doc.date} onChange={e => updateDoc(doc.id,'date',e.target.value)}
                      placeholder="MM/DD/YYYY" style={{ width:120, fontSize:12 }}
                      onBlur={() => setEditDoc(null)} autoFocus />
                  ) : (
                    <span style={{ color: doc.date ? 'var(--text)' : 'var(--text3)' }}>{doc.date || 'Not set'}</span>
                  )}
                </td>
                <td style={{ padding:'10px 8px' }}>
                  <select value={doc.status} onChange={e => updateDoc(doc.id,'status',e.target.value)}
                    style={{ background:'transparent', border:'none', fontSize:11, color:'var(--text2)', cursor:'pointer' }}>
                    <option value="executed">Executed</option>
                    <option value="verify">Verify</option>
                    <option value="outdated">Needs Update</option>
                    <option value="pending">Pending</option>
                  </select>
                </td>
                <td style={{ padding:'10px 8px' }}>
                  <button onClick={() => setEditDoc(doc.id)} style={{ background:'none', border:'none', color:'var(--accent)', fontSize:12, cursor:'pointer' }}>
                    Edit date
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Beneficiaries */}
      <div className="card" style={{ marginBottom:20 }}>
        <div style={{ fontWeight:700, fontSize:14, marginBottom:4, paddingBottom:10, borderBottom:'1px solid var(--border)' }}>Beneficiary Designations</div>
        <div style={{ fontSize:12, color:'var(--text3)', marginBottom:16 }}>Click any field to edit. Changes are saved locally on your Mac.</div>
        <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
          <thead>
            <tr style={{ borderBottom:'2px solid var(--border)' }}>
              {['Account','Primary Beneficiary','Contingent Beneficiary'].map(h => (
                <th key={h} style={{ padding:'6px 8px', textAlign:'left', fontSize:11, color:'var(--text3)', fontWeight:600, textTransform:'uppercase', letterSpacing:'0.06em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {benes.map((b, i) => (
              <tr key={b.id} style={{ borderBottom:'1px solid var(--border)', background:i%2===0?'transparent':'var(--bg3)' }}>
                <td style={{ padding:'9px 8px', fontWeight:600, color:'var(--accent)' }}>{b.label}</td>
                <td style={{ padding:'9px 8px' }}>
                  <input type="text" value={b.primary}
                    onChange={e => updateBene(b.id,'primary',e.target.value)}
                    style={{ width:'100%', background:'transparent', border:'none', borderBottom:'1px solid var(--border)', borderRadius:0, fontSize:12, padding:'2px 0', color:'var(--text)' }} />
                </td>
                <td style={{ padding:'9px 8px' }}>
                  <input type="text" value={b.contingent}
                    onChange={e => updateBene(b.id,'contingent',e.target.value)}
                    style={{ width:'100%', background:'transparent', border:'none', borderBottom:'1px solid var(--border)', borderRadius:0, fontSize:12, padding:'2px 0', color:'var(--text)' }} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Action items */}
      <div className="card">
        <div style={{ fontWeight:700, fontSize:14, marginBottom:16, paddingBottom:10, borderBottom:'1px solid var(--border)' }}>Action Items</div>
        {tasks.map(t => (
          <div key={t.id} style={{ display:'flex', alignItems:'center', gap:12, padding:'8px 0', borderBottom:'1px solid var(--border)' }}>
            <input type="checkbox" checked={t.done}
              onChange={e => setTasks(ts => ts.map(x => x.id===t.id ? {...x, done:e.target.checked} : x))}
              style={{ width:16, height:16, accentColor:'var(--accent)', cursor:'pointer' }} />
            <span style={{ fontSize:13, color: t.done ? 'var(--text3)' : 'var(--text)', textDecoration: t.done ? 'line-through' : 'none' }}>{t.text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
