# Supply & Demand

Planned strategy — not yet implemented. See [`RESEARCH.md`](./RESEARCH.md) for the design brief.

## Summary

Zone-based strategy using Sam Seiden's RBR/DBD/DBR/RBD classification layered on top of the `smartmoneyconcepts` Python library (swing points, order blocks, fair value gaps). Entry on confirmation candle at fresh zone touch; stop beyond distal zone edge; exit at prior swing or 2–3R.

**Target metrics:** 45–55% WR at 2R → ~0.5R expectancy per trade. Complementary to SID Method (different setup conditions, likely low signal correlation).

**Estimated build:** 2–3 weeks to production-validated daily zone system.

## Planned files (not yet built)

- `config.py` — zone tuning parameters (ATR multiples, freshness cap, confirmation candle rules)
- `zones.py` — Seiden classifier (RBR/DBD/DBR/RBD) + freshness tracking on top of `smc` primitives
- `zone_signals.py` — emits zone-touch signals consumable by the shared backtest framework
- `backtest.py` — entry/exit/sizing (mirrors sid_method structure)
- `daily_scanner.py` — daily zone scanner
