# Design notes

- [decimal-research-2026-10-28.md](decimal-research-2026-10-28.md) — database-first research for a lossless decimal type (SQL Server, Oracle, Sybase, PostgreSQL storage formats; 38-digit/24-byte recommendation; future work, after floats).
- [stdlib-eza-design-v7.md](stdlib-eza-design-v7.md) — approved v7 design for the CLI stdlib and `eza` core: runtime metadata slots, module APIs, E0/E1/E2 scope, gates and sequencing. Implementation is not started; the eza 0.23.3 reference pin and the P0 probes are the first decisions.
- [stdlib-eza-design-v7-review.md](stdlib-eza-design-v7-review.md) — independent APPROVE review of v7.
- [numeric-literals-decision-2026-10-29.md](numeric-literals-decision-2026-10-29.md) — user-approved numeric literal policy: exact decimal by default for fractional and exponent literals, `f`/`F` for binary float; floats at position 11, decimal after; not implemented.
- [interfaces-design.md](interfaces-design.md) — phase 8 conservative interfaces design: second-class interface values (parameters and locals), frame cells with runtime handle tokens, per-(type, interface) vtables, the `pure` contract for handle-rooted dispatch, symbol format 3, tests/mutants and implementation plan; design only, waits for methods to merge and promote 0.6.
