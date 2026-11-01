import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { AppShell } from './components/AppShell'
import './styles/app.css'

const container = document.getElementById('root')
if (!container) {
  throw new Error('#root missing from index.html')
}

createRoot(container).render(
  <StrictMode>
    <AppShell />
  </StrictMode>
)