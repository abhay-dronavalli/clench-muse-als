"""Trip planning from public map data: ride comfort routing (Layer 1) and accessible drop-off (Layer 2).

Everything here is computed from API responses (Google Routes, Elevation, Places, Street View
metadata; OpenStreetMap through Overpass). No figure is ever made up: a value the data does not
give is None ("unknown") and is named as unknown wherever it is shown.
"""
