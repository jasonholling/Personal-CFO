import { useState, useRef } from 'react'
import axios from 'axios'

const fmt = (n) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n)

export default function QuickenImport({ onImportComplete }) {
  const [status, setStatus] = useState('idle') // idle | uploading | success | error
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef()

  const handleFile = async (file) => {
    if (!file) return
    if (!file.name.endsWith('.csv')) {
      setError('Please upload the Net Worth Summary CSV exported from Quicken.')
      setStatus('error')
      return
    }
    setStatus('uploading')
    setError(null)
    const form = new FormData()
    form.append('file', file)
    try {
      const r = await axios.post('/api/import/quicken', form, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      setResult(r.data)
      setStatus('success')
      if (onImportComplete) onImportComplete()
    } catch (e) {
      setError(e.response?.data?.detail || 'Import failed. Make sure you exported the Net Worth Summary report.')
      setStatus('error')
    }
  }

  const onDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    handleFile(e.dataTransfer.files[0])
  }

  return (
    <div className="card" style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>Import from Quicken</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
            Export Net Worth Summary as CSV from Quicken → Reports → Net Worth
          </div>
        </div>
        {status === 'success' && (
          <span className="tag tag-green">✓ Imported</span>
        )}
      </div>

      {status !== 'success' && (
        <div
          onDragOver={e => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          style={{
            border: `2px dashed ${dragging ? 'var(--accent)' : 'var(--border2)'}`,
            borderRadius: 'var(--radius-sm)',
            padding: '24px',
            textAlign: 'center',
            cursor: 'pointer',
            background: dragging ? 'rgba(79,156,249,0.06)' : 'transparent',
            transition: 'all 0.15s',
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".csv"
            style={{ display: 'none' }}
            onChange={e => handleFile(e.target.files[0])}
          />
          {status === 'uploading' ? (
            <div style={{ color: 'var(--text2)', fontSize: 13 }}>Importing...</div>
          ) : (
            <>
              <div style={{ fontSize: 24, marginBottom: 8 }}>↑</div>
              <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                Drop your Quicken CSV here or click to browse
              </div>
            </>
          )}
        </div>
      )}

      {status === 'error' && (
        <div style={{ marginTop: 12, color: 'var(--red)', fontSize: 13 }}>⚠ {error}</div>
      )}

      {status === 'success' && result && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginTop: 4 }}>
          {[
            ['Net Worth', fmt(result.net_worth)],
            ['Total Assets', fmt(result.total_assets)],
            ['Investments', fmt(result.investments)],
            ['Accounts', `${result.accounts_created} new · ${result.accounts_updated} updated`],
          ].map(([label, value]) => (
            <div key={label} style={{ background: 'var(--bg3)', borderRadius: 8, padding: '10px 14px' }}>
              <div className="label" style={{ marginBottom: 4 }}>{label}</div>
              <div style={{ fontWeight: 600, fontSize: 13 }}>{value}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
