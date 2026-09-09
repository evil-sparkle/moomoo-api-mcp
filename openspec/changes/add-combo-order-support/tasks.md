# Tasks: Add Combo Order Support

1.  **Bump the SDK dependency**
    - [x] Raise `moomoo-api` in `pyproject.toml` from `>=3.3.0` to `>=10.10.7008`
      (`place_combo_order` and `ComboLeg` are absent below this).
    - [x] Run `uv sync` and confirm the lockfile resolves.
    - [x] Confirm `OpenSecTradeContext.place_combo_order` and `moomoo.ComboLeg` import.

2.  **Add TradeService Tests**
    - [x] Extend `tests/test_services/test_trade_service.py` with tests for
      `place_combo_order`:
      - Successful two-leg submission returns the order record.
      - `ComboLeg` objects are built with the correct `code`, `trd_side`, `qty_ratio`.
      - Rejects fewer than two legs.
      - Rejects an invalid `trd_side`.
      - Rejects legs spanning different markets.
      - Rejects a non-positive `qty_ratio`.
      - Raises `RuntimeError` when the SDK returns a non-OK code.
      - Resolves `acc_id` via `_find_best_account` when defaulted.

3.  **Implement `TradeService.place_combo_order`**
    - [x] Add to `src/moomoo_mcp/services/trade_service.py`:
      - Accept `combo_legs: list[dict]`, `price`, `qty`, `order_type`,
        `time_in_force`, `trd_env`, `acc_id`, `remark`.
      - Validate legs before any gateway call (see spec deltas).
      - Map each dict onto a `ComboLeg` instance.
      - Reuse `_find_best_account` with the first leg's market when `acc_id` is 0.
      - Call `trd_ctx.place_combo_order`; raise `RuntimeError` on non-OK ret.

4.  **Implement the `place_combo_order` Tool**
    - [x] Add to `src/moomoo_mcp/tools/trading.py`, mirroring `place_order`'s shape.
    - [x] Docstring must state the REAL-environment confirmation requirement, that
      `price` is the **net** package price, and the leg dict format.

5.  **Expose the option strategy view (required by the closing workflow)**
    - [x] Add `show_option_strategy_view` to `TradeService.get_positions` and forward
      it to `position_list_query`.
    - [x] Add it to the `get_positions` tool in `src/moomoo_mcp/tools/account.py`,
      documenting that it yields the `position_id` needed to close a strategy.
    - [x] Tests: flag is forwarded to the SDK; flat view remains the default.
    - [x] Verify against a live gateway that the view returns COMBINED and LEG rows
      carrying `position_id`.
    - [x] Serialize `position_id` and `combo_id` as decimal strings at the tool
      boundary, so 64-bit values are not rounded by clients that parse JSON numbers
      as doubles. Conversion lives in the tool layer, not the service, so in-process
      Python callers keep exact ints.
    - [x] Tests: stringification, numpy ints from pandas, absent and null ids, input
      rows not mutated, and a retrieval-to-submission roundtrip through a simulated
      double-parsing client (including a test proving the raw ints do corrupt).

6.  **Verify**
    - [x] `uv run pytest` passes.
    - [x] `uv run ruff check` reports no *new* errors. Note: the repository has 110
      pre-existing errors on `main`, and this change neither adds to nor fixes them
      (verified by diffing ruff output against a clean worktree at HEAD). The same
      applies to `ruff format --check`, which would reformat 14 pre-existing files.
      Cleaning those up is out of scope here and belongs in its own change.
    - [x] Tool appears in the server's tool list at runtime.
    - [x] `openspec validate add-combo-order-support --strict` passes (run via bun).
      Five other changes and specs in this repository already fail the same check;
      they are untouched by this change.

7.  **Documentation**
    - [x] Add `place_combo_order` to the Trading section of `README.md`.
    - [x] Note the raised minimum OpenD/SDK version in the README prerequisites.
