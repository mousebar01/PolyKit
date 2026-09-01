# Character evidence

`character-evidence/humanoid-proportions` provides a reference-free canonical
anatomy block for an 8-head figure. It can run standalone or attach the block
to a JSON spec's `preSpecAssessment.anatomy` field. Every generated value is
marked `source: "canon-table"`; fields without a sourced value remain listed in
`unsourced` rather than receiving an invented default.

The node refuses to apply the canon when the spec names a real reference image.
Those proportions must be measured from the reference through the image-evidence
nodes instead. Other head counts are also rejected until a complete, sourced
table exists, so interpolation cannot masquerade as measurement.

`character-evidence/hair-profile` validates the companion `hairProfile` schema:
scalp-bound `(u, v)` roots, hairline controls, flow-field coordinates, supported
representation tiers/primitives, and positive mass dimensions. Lock-tier values
are reported as derived/uncalibrated because the local reference has no
separated hair mesh. The node validates the profile only; it does not claim to
compile it into lock geometry.

`character-evidence/scalp-exposure` is the geometric hard channel for hair
review. Given a ring-stack skull and hair points, it samples the scalp with
area-weighted patches and marches along the true surface normal. Points inside
the skull do not count as coverage, floating points beyond reach do not count
either, and the report includes exposed samples, cap rings, longest runs, and
an explicitly uncalibrated hard threshold.
