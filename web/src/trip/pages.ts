import { lazy } from 'react'

// Trip planning pages, loaded only when opened so Leaflet stays out of the patient board's bundle.
export const TripPage = lazy(() => import('./TripPage.tsx'))
export const TripLivePage = lazy(() => import('./LivePage.tsx'))
export const CarSimPage = lazy(() => import('../carsim/CarSimPage.tsx'))
