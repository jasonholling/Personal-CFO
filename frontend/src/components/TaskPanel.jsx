import { useState, useEffect } from 'react'
import axios from 'axios'

const SECTION_LABELS = {
  investments:             'Investments',
  financial_independence:  'Financial Independence',
  risk:                    'Risk Management',
  estate:                  'Estate Planning',
  education:               'Education',
}

const TYPE_COLORS = {
  annual:     'var(--accent)',
  calculated: 'var(--amber)',
  manual:     'var(--text3)',
}

const TYPE_LABELS = {
  annual:     'Annual',
  calculated: 'Auto',
  manual:     'Manual',
}

export default function TaskPanel({ section }) {
  const [tasks, setTasks]       = useState([])
  const [loading, setLoading]   = useState(true)
  const [showDone, setShowDone] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newDesc, setNewDesc]   = useState('')
  const [adding, setAdding]     = useState(false)
  const [syncing, setSyncing]   = useState(false)

  const load = () => {
    axios.get(`/api/tasks?section=${section}`)
      .then(r => { setTasks(r.data); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => { load() }, [section])

  const toggle = async (task) => {
    await axios.patch(`/api/tasks/${task.id}`, { completed: !task.completed })
    load()
  }

  const addTask = async () => {
    if (!newTitle.trim()) return
    await axios.post('/api/tasks', { section, title: newTitle.trim(), description: newDesc.trim() || null })
    setNewTitle('')
    setNewDesc('')
    setAdding(false)
    load()
  }

  const deleteTask = async (id) => {
    await axios.delete(`/api/tasks/${id}`)
    load()
  }

  const syncTasks = async () => {
    setSyncing(true)
    await axios.post('/api/tasks/sync')
    load()
    setSyncing(false)
  }

  const pending   = tasks.filter(t => !t.completed)
  const completed = tasks.filter(t => t.completed)

  const TaskRow = ({ task }) => (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 12, padding: '12px 0',
      borderBottom: '1px solid var(--border)',
      opacity: task.completed ? 0.55 : 1,
    }}>
      <button
        onClick={() => toggle(task)}
        style={{
          width: 20, height: 20, minWidth: 20, borderRadius: 4, marginTop: 1,
          background: task.completed ? 'var(--green)' : 'transparent',
          border: `1.5px solid ${task.completed ? 'var(--green)' : 'var(--border2)'}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          cursor: 'pointer', transition: 'all 0.15s', flexShrink: 0,
        }}
      >
        {!!task.completed && <span style={{ color: '#fff', fontSize: 11, fontWeight: 700 }}>✓</span>}
      </button>

      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{
            fontSize: 13, fontWeight: 500,
            textDecoration: task.completed ? 'line-through' : 'none',
            color: task.completed ? 'var(--text3)' : 'var(--text)',
          }}>
            {task.title}
          </span>
          <span style={{
            fontSize: 10, fontWeight: 600, letterSpacing: '0.05em',
            padding: '1px 6px', borderRadius: 10,
            color: TYPE_COLORS[task.task_type] || 'var(--text3)',
            background: 'var(--bg3)',
          }}>
            {TYPE_LABELS[task.task_type] || task.task_type}
          </span>
          {task.recurrence === 'annual' && (
            <span style={{ fontSize: 10, color: 'var(--text3)' }}>↻ yearly</span>
          )}
        </div>
        {task.description && (
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 3, lineHeight: 1.5 }}>
            {task.description}
          </div>
        )}
        {!!task.completed && task.completed_date && (
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>
            Completed {new Date(task.completed_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
          </div>
        )}
      </div>

      {task.task_type === 'manual' && !task.completed && (
        <button className="btn-danger" onClick={() => deleteTask(task.id)} style={{ flexShrink: 0 }}>✕</button>
      )}
    </div>
  )

  if (loading) return null

  return (
    <div className="card" style={{ marginTop: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>
            Tasks · {SECTION_LABELS[section]}
            {pending.length > 0 && (
              <span style={{
                marginLeft: 8, background: 'rgba(251,191,36,0.15)', color: 'var(--amber)',
                fontSize: 11, fontWeight: 700, padding: '1px 7px', borderRadius: 10,
              }}>
                {pending.length} open
              </span>
            )}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
            Check off tasks as you complete them
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-secondary" onClick={syncTasks} disabled={syncing} style={{ fontSize: 12 }}>
            {syncing ? '...' : '↻ Sync'}
          </button>
          <button className="btn-primary" onClick={() => setAdding(!adding)} style={{ fontSize: 12 }}>
            {adding ? 'Cancel' : '+ Add Task'}
          </button>
        </div>
      </div>

      {adding && (
        <div style={{ background: 'var(--bg3)', borderRadius: 8, padding: 14, marginBottom: 16 }}>
          <input
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            placeholder="Task title"
            style={{ marginBottom: 8 }}
            onKeyDown={e => e.key === 'Enter' && addTask()}
          />
          <input
            value={newDesc}
            onChange={e => setNewDesc(e.target.value)}
            placeholder="Description (optional)"
            style={{ marginBottom: 10 }}
          />
          <button className="btn-primary" onClick={addTask} style={{ fontSize: 12 }}>Add Task</button>
        </div>
      )}

      {tasks.length === 0 ? (
        <div style={{ color: 'var(--text2)', fontSize: 13, padding: '16px 0', textAlign: 'center' }}>
          No tasks yet — click Sync to generate automatic tasks, or add one manually.
        </div>
      ) : (
        <>
          {pending.length === 0 && (
            <div style={{ color: 'var(--green)', fontSize: 13, padding: '8px 0' }}>
              ✓ All tasks complete for this section
            </div>
          )}
          {pending.map(t => <TaskRow key={t.id} task={t} />)}

          {completed.length > 0 && (
            <>
              <button
                onClick={() => setShowDone(!showDone)}
                style={{
                  background: 'none', border: 'none', color: 'var(--text3)',
                  fontSize: 12, cursor: 'pointer', padding: '10px 0', width: '100%', textAlign: 'left',
                }}
              >
                {showDone ? '▾' : '▸'} {completed.length} completed task{completed.length !== 1 ? 's' : ''}
              </button>
              {showDone && completed.map(t => <TaskRow key={t.id} task={t} />)}
            </>
          )}
        </>
      )}
    </div>
  )
}
