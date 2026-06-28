# Breakout — 52-week-high momentum (exploratory / WIP)

> **Status: research sandbox, not a shipped strategy.** This directory is an
> in-progress exploration, kept in the open as a record of method. The flagship,
> validated work in this repo is the **[SID Method](../sid_method/)** and its
> [QuantConnect validation](../../quantconnect/). Read that first.

A technical-only momentum system: enter on a closing break to a new 52-week high
(George & Hwang, 2004), S&P 500 / Russell 1000 universe, breadth-gated, with a
partial-at-1R + trailing exit. See [`../../docs/decisions/DECISION_LOG.md`](../../docs/decisions/DECISION_LOG.md)
for the v1 spec.

The many `*_sweep.py` / `*_ablation.py` scripts here are **parameter studies**,
not production code — they exist to pressure-test exits, universe choice, and
VCP/entry variants before anything is committed. Expect rough edges; nothing here
is wired for live or paper trading yet.

## Files

| File | Purpose |
|---|---|
| `main.py`, `backtest.py`, `signals.py` | core entry/exit logic + backtest loop |
| `universe.py`, `universe_r1k.py`, `data.py` | universe construction + data |
| `config.py` | parameters |
| `*_sweep.py`, `*_ablation.py`, `exit_variants.py` | exploratory parameter/exit studies |
| `correlation.py`, `instrumentation.py`, `output.py` | diagnostics + reporting |
