"""MY EFT JOURNEY - replay an Escape from Tarkov raid on the map.

Data sources are only files the game writes by itself: in-game screenshot
filenames (position + rotation), the game's log folder (map, raid start/end)
and, later, post-raid screens read by OCR. Nothing touches the game process.
"""

__version__ = "0.1.0"
