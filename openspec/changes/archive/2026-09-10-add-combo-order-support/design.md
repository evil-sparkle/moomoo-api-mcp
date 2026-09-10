# Design: Combo (Multi-Leg) Order Support

## Dependency bump

`place_combo_order` and `ComboLeg` are absent from the resolved `moomoo-api`
9.06.5608; the current constraint is `moomoo-api>=3.3.0`. They are present in
10.10.7008.

Raising the floor to `>=10.10.7008` is preferred over a narrow pin:

- OpenD and the SDK share a version line, and moomoo's own download page states a
  *minimum OpenD version* per SDK release. Keeping the SDK at or above the gateway
  version avoids protocol mismatches.
- The bump is additive for existing callers — no existing method signature used by
  this server changes between these versions.

Risk: users pinned to an older OpenD would need to upgrade the gateway too. This is
noted in the tasks so the README prerequisite section is updated alongside.

## Leg representation at the tool boundary

The SDK takes a list of `ComboLeg` objects with attributes set post-construction
(`ComboLeg()` takes no arguments). MCP tool arguments must be JSON-serializable, so
the tool accepts a list of plain dicts and the service maps them onto `ComboLeg`:

```python
{"code": "US.XYZ260101C100000", "trd_side": "SELL", "qty_ratio": 1}
```

`ComboLeg` also carries `position_id` and `pred_side`.

`position_id` **is** exposed, and must be: the API reference marks it *"required when
closing a position"*, sourced from the option strategy view of the position list. An
initial draft of this design assumed it was unnecessary for equity options; querying a
live account disproved that. The strategy view returns a `COMBINED` row for the
strategy plus a `LEG` row per leg, each with its own `position_id`, and legs that show
`can_sell_qty` of 0 in the flat position view are sellable in the strategy view —
which indicates closing is expected to go through the combo path rather than leg by
leg. A combo tool that could not carry `position_id` would therefore be unable to do
the main thing it exists for.

`pred_side` remains unexposed; it applies to prediction-market combos, which this
server does not otherwise support.

## Validation, and where it belongs

Validation lives in the service, before the gateway call, so failures are fast and
carry actionable messages rather than opaque protocol errors:

- At least two legs — a one-leg "combo" should use `place_order`.
- Each leg has a non-empty `code`, a `trd_side` of `BUY`/`SELL`, and a positive
  integer `qty_ratio`.
- All legs resolve to the same market prefix. Cross-market combos are not a thing
  the gateway supports, and catching it here gives a clearer error.

Smart account selection reuses `_find_best_account` with the market of the first
leg, matching `place_order`'s behaviour.

## Net price semantics

`price` is the **net** price of the package, not a per-leg price. The API reference
says only that a price must be supplied even for market and auction types, where "any
value is acceptable".

The sign convention for debit versus credit packages is **not documented and remains
unverified.** An earlier draft of this design asserted that both are expressed as a
positive number with direction implied by the legs' sides. That was an assumption, not
a finding, and the unit tests cannot settle it because they mock submission. It has
been removed rather than restated more softly, because a confident-sounding convention
is exactly what would cause an agent to misprice a real order.

The tool docstring therefore states that no convention should be assumed and directs
callers to verify against the platform's own ticket for the same strategy. Settling
this properly requires either clarification from moomoo or a non-marketable live order
observed against the app.

## Safety posture

The tool docstring carries the same confirmation requirement as `place_order`, and
states it more strongly: a combo order moves several positions at once, and an agent
that misconstructs the legs can convert a defined-risk position into an undefined-risk
one. Validation deliberately refuses ambiguous input rather than guessing a repair.
