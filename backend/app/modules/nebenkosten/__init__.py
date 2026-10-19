"""Betriebskostenabrechnung for a rented Eigentumswohnung: the deterministic core.

`model` is the data extracted from the documents, `calculate.settle` turns it into a settlement
using the allocability rules in `catalogue.yaml`, and `verify.verify` is its postcondition. No
model call and no I/O beyond reading the catalogue.
"""
