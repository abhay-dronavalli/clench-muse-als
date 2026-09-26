import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import BoardPage from './board/BoardPage.tsx'
import ConsolePage from './console/ConsolePage.tsx'
import './index.css'

// Two pages, simple pathname routing: "/" patient board, "/console" caregiver console.
const path = window.location.pathname.replace(/\/+$/, '')
const Page = path === '/console' ? ConsolePage : BoardPage

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Page />
  </StrictMode>,
)
