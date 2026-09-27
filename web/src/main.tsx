import { StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import BoardPage from './board/BoardPage.tsx'
import ConsolePage from './console/ConsolePage.tsx'
import GazeTestPage from './gazetest/GazeTestPage.tsx'
import { TripLivePage, TripPage } from './trip/pages'
import './index.css'

// Simple pathname routing: "/" patient board, "/console" caregiver console, "/gaze-test" the eye
// tracker test bench (docs/eye-tracking.md), "/trip" and "/trip/live" trip planning (core/geo).
const path = window.location.pathname.replace(/\/+$/, '')
const Page = path === '/console' ? ConsolePage : path === '/gaze-test' ? GazeTestPage : path === '/trip' ? TripPage : path === '/trip/live' ? TripLivePage : BoardPage

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Suspense fallback={null}>
      <Page />
    </Suspense>
  </StrictMode>,
)
