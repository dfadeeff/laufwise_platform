"""The property-management vertical: back-office workflows over rented property, computed from the
documents and checked before anyone sees them (ADR-0028).

`betriebskosten` is its first workflow, the annual service-charge settlement. Workflows that share
its nouns (a unit, a tenancy, an owners' association) belong in this module; one that shares none
belongs in a module of its own.
"""

from app.kernel import Module

MODULE = Module(
    key="immobilien",
    label="Property management",
    summary="Back-office workflows for rented property, starting with the annual "
    "Betriebskostenabrechnung: computed from your documents, every figure traced to its source.",
    pitch=(
        "Built for property management",
        "Every figure traced to a document.",
        "Every rule cited.",
    ),
    intro="Settlements computed from your documents and checked before anyone sees them.",
)
