"""Shared reference vocabulary (a shared-services layer).

Importable by every feature: normalizing company, title and city
names, board job ids and JD fingerprints (normalize.py), and the
skills taxonomy loaded from Data/taxonomy/skills.json (catalog.py).
Pure functions over reference data - no I/O beyond reading Data/.
"""
