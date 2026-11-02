The documents are those of one Kautionsabrechnung at the end of a tenancy: the lease (its deposit
agreement and rent), proof of the deposit payments, the deposit account's statement (interest,
tax withheld), the handover record, and the invoices or quotes behind the landlord's claims.

- tenant, unit: as the lease names them. lease_end: the end of the tenancy. returned_on: the date
  the flat was handed back, from the handover record; null if no document states it.
- monthly_rent_net: the Nettokaltmiete as the lease states it. agreed: the deposit the lease agrees.
- payments: one per deposit payment the documents show, with its date and source.
- interest and tax_withheld: exactly as the deposit account states them; 0 when it states none.
- claims: one per claim the landlord makes. category: the catalogue key whose label matches;
  normal wear and tear is "abnutzung"; null when unsure. Never decide here whether a claim may be
  charged: that is computed later. evidence: the document that proves it (an invoice, a quote, an
  entry in the handover record), named; empty when there is none.
- renovation_clause: always null. Whether the lease's cosmetic-repair clause is valid is a legal
  judgment a person makes, not something to read.
