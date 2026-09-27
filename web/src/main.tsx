import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import BoardPage from './board/BoardPage.tsx'
import ConsolePage from './console/ConsolePage.tsx'
import GazeTestPage from './gazetest/GazeTestPage.tsx'
import './index.css'

// Simple pathname routing: "/" patient board, "/console" caregiver console, "/gaze-test" the eye
// tracker test bench (docs/eye-tracking.md).
const path = window.location.pathname.replace(/\/+$/, '')
const Page = path === '/console' ? ConsolePage : path === '/gaze-test' ? GazeTestPage : BoardPage

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Page />
  </StrictMode>,
)
