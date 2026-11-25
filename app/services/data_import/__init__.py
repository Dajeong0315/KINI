"""Offline import pipeline for versioned food and allergen reference data.

Nothing in this package is called by a recommendation request.  Importers write
staging releases; only an explicit publish operation changes the local pointer.
"""
