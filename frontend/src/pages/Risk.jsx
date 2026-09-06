import { useState, useEffect } from 'react'
import axios from 'axios'
import TaskPanel from '../components/TaskPanel'
import { usePersonNames } from '../hooks/usePersonNames'
import { maskDigitsInText } from '../utils/privacy'

const EMPTY_POLICY  = { who: '', policy_type: '', benefit: '', premium: '', notes: '' }
const EMPTY_PROPERTY = { item: '', coverage: '', renewal: '' }

export default function Risk() {
  const { person1Name, person2Name, kid1Name, kid2Name } = usePersonNames()
  const [policies, setPolicies]   = useState([])
  const [properties, setProperties] = useState([])
  const [policyForm, setPolicyForm]     = useState(null)
  const [propertyForm, setPropertyForm] = useState(null)

  const loadPolicies   = () => axios.get('/api/insurance-policies').then(r => setPolicies(r.data))
  const loadProperties = () => axios.get('/api/property-policies').then(r => setProperties(r.data))
  useEffect(() => { loadPolicies(); loadProperties() }, [])

  const savePolicy = async () => {
    if (policyForm.id) await axios.put(`/api/insurance-policies/${policyForm.id}`, policyForm)
    else await axios.post('/api/insurance-policies', policyForm)
    setPolicyForm(null)
    loadPolicies()
  }
  const deletePolicy = async (id) => { await axios.delete(`/api/insurance-policies/${id}`); loadPolicies() }

  const saveProperty = async () => {
    if (propertyForm.id) await axios.put(`/api/property-policies/${propertyForm.id}`, propertyForm)
    else await axios.post('/api/property-policies', propertyForm)
    setPropertyForm(null)
    loadProperties()
  }
  const deleteProperty = async (id) => { await axios.delete(`/api/property-policies/${id}`); loadProperties() }

  const whoOptions = [person1Name, person2Name, kid1Name, kid2Name, 'Joint', 'Trust']

  return (
    <div>
      <div style={{ marginBottom: 32 }}>
        <h1 className="section-title">Risk Management</h1>
        <p className="section-sub">Life, disability, LTC, property, and umbrella coverage</p>
      </div>

      <div className="card" style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <div className="label">Life & Other Insurance Policies</div>
          <button className="btn-secondary" onClick={() => setPolicyForm(EMPTY_POLICY)}>+ Add Policy</button>
        </div>

        {policyForm && (
          <div className="card" style={{ marginBottom: 16, background: 'var(--bg2)' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
              <select value={policyForm.who} onChange={e => setPolicyForm({ ...policyForm, who: e.target.value })}>
                <option value="">Who?</option>
                {whoOptions.map(w => <option key={w} value={w}>{w}</option>)}
              </select>
              <input placeholder="Policy type (e.g. Life — Term)" value={policyForm.policy_type} onChange={e => setPolicyForm({ ...policyForm, policy_type: e.target.value })} />
              <input placeholder="Benefit (e.g. $500,000)" value={policyForm.benefit} onChange={e => setPolicyForm({ ...policyForm, benefit: e.target.value })} />
              <input placeholder="Premium (e.g. $955/yr)" value={policyForm.premium} onChange={e => setPolicyForm({ ...policyForm, premium: e.target.value })} />
              <input placeholder="Notes" value={policyForm.notes} onChange={e => setPolicyForm({ ...policyForm, notes: e.target.value })} style={{ gridColumn: '1 / -1' }} />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn-primary" onClick={savePolicy}>Save</button>
              <button className="btn-secondary" onClick={() => setPolicyForm(null)}>Cancel</button>
            </div>
          </div>
        )}

        {policies.length === 0 && !policyForm && (
          <div style={{ fontSize: 13, color: 'var(--text2)', padding: '16px 0' }}>
            No policies yet. Add your life, disability, and LTC coverage above — it's stored only in your local database, never in the app's source code.
          </div>
        )}

        {policies.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  {['Who', 'Policy', 'Benefit', 'Premium', 'Notes', ''].map(h => (
                    <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontSize: 11, fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text3)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {policies.map((p) => (
                  <tr key={p.id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '10px 12px' }}><span className="tag tag-blue">{p.who}</span></td>
                    <td style={{ padding: '10px 12px', fontSize: 13 }}>{p.policy_type}</td>
                    <td style={{ padding: '10px 12px', fontSize: 13, fontWeight: 500 }}>{maskDigitsInText(p.benefit)}</td>
                    <td style={{ padding: '10px 12px', fontSize: 13, color: 'var(--text2)' }}>{maskDigitsInText(p.premium)}</td>
                    <td style={{ padding: '10px 12px', fontSize: 12, color: 'var(--text2)' }}>{p.notes}</td>
                    <td style={{ padding: '10px 12px', textAlign: 'right' }}>
                      <button className="btn-secondary" style={{ marginRight: 6 }} onClick={() => setPolicyForm(p)}>Edit</button>
                      <button className="btn-secondary" onClick={() => deletePolicy(p.id)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="divider" />
        <div style={{ fontSize: 12, color: 'var(--text2)' }}>
          Update premiums and benefit amounts as policies renew.
        </div>
      </div>

      <div className="card" style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <div className="label">Property & Liability Coverage</div>
          <button className="btn-secondary" onClick={() => setPropertyForm(EMPTY_PROPERTY)}>+ Add Coverage</button>
        </div>

        {propertyForm && (
          <div className="card" style={{ marginBottom: 16, background: 'var(--bg2)' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
              <input placeholder="Item (e.g. Primary home)" value={propertyForm.item} onChange={e => setPropertyForm({ ...propertyForm, item: e.target.value })} />
              <input placeholder="Renewal (e.g. 2/21 annually)" value={propertyForm.renewal} onChange={e => setPropertyForm({ ...propertyForm, renewal: e.target.value })} />
              <input placeholder="Coverage details" value={propertyForm.coverage} onChange={e => setPropertyForm({ ...propertyForm, coverage: e.target.value })} style={{ gridColumn: '1 / -1' }} />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn-primary" onClick={saveProperty}>Save</button>
              <button className="btn-secondary" onClick={() => setPropertyForm(null)}>Cancel</button>
            </div>
          </div>
        )}

        {properties.length === 0 && !propertyForm && (
          <div style={{ fontSize: 13, color: 'var(--text2)', padding: '16px 0' }}>
            No property/liability coverage yet. Add your home, auto, and umbrella policies above.
          </div>
        )}

        {properties.map((p) => (
          <div key={p.id} style={{ marginBottom: 14, paddingBottom: 14, borderBottom: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <div style={{ fontWeight: 500, fontSize: 13 }}>{p.item}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 3 }}>{maskDigitsInText(p.coverage)}</div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0, marginLeft: 12 }}>
                <span className="tag tag-amber">Renews {p.renewal}</span>
                <button className="btn-secondary" onClick={() => setPropertyForm(p)}>Edit</button>
                <button className="btn-secondary" onClick={() => deleteProperty(p.id)}>Delete</button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <TaskPanel section="risk" />
    </div>
  )
}
