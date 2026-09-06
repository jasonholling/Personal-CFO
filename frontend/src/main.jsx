import React from 'react'
import ReactDOM from 'react-dom/client'
import axios from 'axios'
import App from './App.jsx'
import './index.css'

// The passphrase/Touch ID session (see backend/auth.py) is an httpOnly
// cookie — every request needs to send it for the backend to recognize
// an unlocked session.
axios.defaults.withCredentials = true

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
