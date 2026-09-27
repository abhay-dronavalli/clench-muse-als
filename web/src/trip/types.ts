// Shapes of GET /api/geo/trip (core/geo/model.py). Only the fields the pages read.

export interface LatLng {
  latitude: number
  longitude: number
}

export interface RideProfile {
  route_preference: 'ROUTE_PREFERENCE_UNSPECIFIED' | 'ROUTE_PREFERENCE_FASTEST' | 'ROUTE_PREFERENCE_SMOOTHEST'
  uses_wheelchair: boolean
  needs_extra_boarding_time: boolean
}

export interface DropoffRequest {
  request_id: string
  point: LatLng
  description: string
  reason: string
  fallback_point: LatLng | null
}

export interface Factor {
  name: string
  label: string
  value: number | null
  used: number
  weight: number
  evidence: string
}

export interface Slope {
  max_grade_pct: number
  avg_grade_pct: number
  climb_m: number
  length_m: number
  samples: number
  source: string
  resolution_m: number | null
}

export interface Candidate {
  id: string
  rank: number
  score: number
  stop: LatLng
  entrance: LatLng
  heading_to_entrance_deg: number
  entrance_kind: string
  road_class: string
  road_name: string | null
  walk_m: number
  walk_kind: string
  walk_polyline: string
  slope: Slope | null
  factors: Factor[]
  unknown: string[]
  description: string
  reason: string
}

export interface Dropoff {
  destination: string
  destination_point: LatLng
  radius_m: number
  candidates: Candidate[]
  request: DropoffRequest | null
  osm_fetched_at: string
  notes: string[]
}

export interface Feature {
  kind: string
  point: LatLng
  detail: string
}

export interface RouteStats {
  id: string
  label: string
  duration_s: number
  distance_m: number
  summary: string
  polyline: string
  sharp_turns: number
  climb_m: number | null
  steepest_grade_pct: number | null
  elevation_source: string | null
  traffic_signals: number
  stop_signs: number
  traffic_calming: number
  traffic_calming_kinds: Record<string, number>
  rough_m: number
  surface_known_pct: number
  bridge_m: number
  comfort_cost: number
  features: Feature[]
}

export interface Tile {
  route_id: string
  kind: 'fastest' | 'smoothest' | 'fastest_and_smoothest' | 'alternative'
  label: string
  detail: string
  ride_profile: RideProfile | null
}

export interface Routes {
  pickup: string
  destination: string
  pickup_point: LatLng
  destination_point: LatLng
  routes: RouteStats[]
  fastest_id: string
  smoothest_id: string
  tiles: Tile[]
  osm_fetched_at: string
  notes: string[]
}

export interface Trip {
  generated_at: string
  attribution: string[]
  duration_note: string
  dropoff: Dropoff | null
  ride: Routes | null
  notes: string[]
}

// GET /api/geo/live/candidate/{id} and /api/geo/live/place (core/geo/live.py)
export interface LiveCandidate {
  candidate: Candidate
  attribution: string
  streetview: {
    status: string
    date: string | null
    copyright: string | null
    heading_deg?: number
    pano_to_stop_m?: number
    pano_to_target_m?: number
    image?: string
  } | null
  vision: {
    label: 'ramp' | 'level' | 'steps' | 'steps_with_accessible_route' | 'unclear'
    confidence: number
    cues: string[]
    note: string
  } | null
  errors: string[]
}

export interface LivePlace {
  attribution: string
  place_id?: string
  name?: string | null
  accessibility?: Record<string, boolean>
  error?: string
}
