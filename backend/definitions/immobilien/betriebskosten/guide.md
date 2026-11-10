The documents are those of one Betriebskostenabrechnung for one rented flat: the owners'
association's Hausgeldabrechnung for the flat (Unterlage 1), and usually a Grundsteuerbescheid, a
heating bill from the Messdienst and an extract of the lease.

- statement_from, statement_to: the period the Hausgeldabrechnung itself names in its heading or
  text ("für 2024" = 2024-01-01 to 2024-12-31), even when it differs from the period to settle;
  null when it names none. Never copy the settlement period into it.
- rent_increase.local_rent_per_sqm: only the figure a document states; empty when none does.
- positions: one per cost row of the Hausgeldabrechnung, in its order; never the total row. Add a
  position for every cost billed to the flat in a separate document (e.g. the Grundsteuer), with
  weg_key "direkt". id: "U<document number>-<row number, two digits>", e.g. U1-01, U2-01.
  label: the row's text without remarks in brackets. source: the document, e.g. "Unterlage 1".
- category: the catalogue key whose label matches the row; null when unsure. Never decide here
  whether a cost is passed on to the tenant: that is computed later.
- excluded: when a row says it contains an amount for something else (e.g. "darin enthalten
  78,00 € für Kleinreparaturen"), record that amount with its category; the row's amount stays
  the full amount.
- weg_key: only when the document says how the association split that row ("Umlage nach
  Fläche" = flaeche, a Messdienst bill or consumption = verbrauch); otherwise null.
- tenancies: every tenant who rented the flat during the period. start and end exactly as the
  documents state them, even when they lie outside the period (a Mietbeginn in 2021 stays
  2021-03-01); null when a document does not state one (a tenancy that continues has no end).
  The billing period is never a tenancy date. monthly_advance as stated. advances_paid only when
  the documents state what was actually paid; otherwise null. A vacant month is not a tenancy.
- lease.key: "flaeche" when the lease splits by Wohnfläche, "mea" by Miteigentumsanteile.
  named_other_costs: only costs the lease names explicitly; a general reference to § 2 BetrKV
  names none.
- heating: when there is a heating row. position_id: that row's id. base_share: the
  Grundkosten share (40 % -> 0.40; "50/50 Verbrauch/Fläche" -> 0.50). readings: one per meter
  reading the heating bill states, with the share of consumption up to it, in date order, the
  last one at the end of the period. No readings when the bill states none. hot_water_share:
  only when the bill states which part of the consumption costs of a period without a reading
  per tenant is hot water (30 % -> 0.30); otherwise null.
- today: the working day the task or cover letter names ("Heute ist der 15.11.2026"); null when
  none is named. Never today's date of the system.
- rent_increase: only when the landlord wants the rent raised to the local rent (§ 558 BGB);
  otherwise null. tenant, area_sqm (the flat's floor area), local_rent_per_sqm (the Mietspiegel's
  figure for the flat, €/m²), delivered (the day the request is to reach the tenant). rents: every
  change of the rent the documents list, in date order: start, rent (the net cold rent without any
  surcharge), surcharge (a surcharge under § 559 or § 560 BGB in force from then, else 0.00) and
  basis ("vertrag" for the lease, "558" for an increase to the local rent, "559" for a
  modernisation surcharge). When a row gives the rent including a surcharge, rent is that rent
  less the surcharge. Which cap applies is not read here: the assessment decides it.
- A notice that raises a cost after the period (a changed property-tax notice, say) is a position
  of its own, with weg_key "direkt" and that notice as its source; the original notice stays its
  own position. When the documents give a unit only as a total the administrator has already
  checked, that total is one position with category "vorgeprueft".
- When the documents cover several units, read only the one the case is about: its positions,
  its tenancy, its advances.
