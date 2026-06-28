#!/usr/bin/env python3
"""Generate the SID Method one-pager (HTML + SVG equity curve).

Equity series is the real QuantConnect backtest for the survivorship-free,
longs-only, trailing-exit configuration (2020-01-01 .. 2026-04-28), pulled via
the QC API (backtest cbd80923400f9ceb4dcb9aa8d1f4959d, project 33476516).
Render to PDF/PNG with headless Chrome (see build.sh).
"""
import datetime as dt

# Real equity close values ($100k start), uniform ~13-day sampling.
EQUITY = [
    100000, 100000, 99369.79, 97652.31, 97761.53, 93208.87, 92921.31, 92921.31,
    92921.31, 93370.73, 93132.14, 95254.25, 95628.78, 95639.62, 95637.03,
    96136.71, 96142.57, 96142.57, 95934.35, 94433.38, 95194.97, 99171.49,
    96136.22, 97384.38, 101791.84, 100781.96, 101429.32, 101563.98, 99878.92,
    99443.50, 99563.94, 98532.23, 98229.65, 99119.45, 102596.15, 105799.91,
    107061.71, 106206.75, 106557.88, 107360.42, 110209.05, 110807.92,
    110239.96, 107968.91, 108115.18, 105543.68, 107917.65, 107590.09,
    111177.04, 114174.02, 114173.66, 114883.36, 113640.40, 112702.49,
    115264.58, 119161.98, 107670.83, 107536.45, 107536.45, 107536.45,
    107536.45, 114948.28, 109815.25, 108426.68, 105986.17, 105934.59,
    105934.59, 105934.59, 105934.59, 105934.59, 116289.92, 118109.10,
    116829.94, 114963.58, 112264.22, 112357.68, 112357.87, 112357.87,
    122085.40, 119685.45, 117501.42, 116674.52, 116782.38, 118091.01,
    123144.09, 121250.13, 120467.82, 116709.58, 115279.35, 112731.06,
    118083.23, 115501.33, 117397.45, 121320.23, 125776.20, 125120.73,
    125741.15, 126198.99, 126206.71, 126206.71, 128068.62, 121568.23,
    118244.66, 118122.74, 118122.74, 114622.14, 121837.32, 121645.83,
    122809.03, 124385.69, 124743.96, 124407.22, 126201.11, 123532.87,
    124403.99, 124080.83, 122807.43, 121489.39, 122126.90, 127543.998,
    124579.01, 128145.88, 129972.23, 128692.91, 129544.18, 129075.54,
    130595.05, 130467.73, 129876.29, 134358.36, 135718.19, 133826.69,
    133874.68, 129254.23, 137323.52, 134363.80, 133492.77, 136350.20,
    146950.89, 146801.78, 148024.78, 146399.99, 146279.82, 146279.82,
    146279.82, 146279.82, 146178.40, 140207.03, 139760.07, 139752.62,
    141072.02, 137964.91, 136786.52, 141394.53, 146282.95, 144280.19,
    144293.28, 146128.66, 149357.67, 143396.25, 143924.02, 144169.45,
    156311.17, 156687.78, 155733.91, 157229.02, 156206.17, 155341.40,
    166889.70, 160696.38, 160496.78, 165389.85, 170986.11,
]

START = dt.date(2020, 1, 1)
END = dt.date(2026, 4, 28)

# ---- chart geometry ----
W, H = 1040, 300          # plot area
PAD_L, PAD_R = 64, 24
PAD_T, PAD_B = 24, 34
PW = W - PAD_L - PAD_R
PH = H - PAD_T - PAD_B

lo, hi = 90000, 175000
n = len(EQUITY)


def x(i):
    return PAD_L + PW * i / (n - 1)


def y(v):
    return PAD_T + PH * (1 - (v - lo) / (hi - lo))


pts = [(x(i), y(v)) for i, v in enumerate(EQUITY)]
line = "M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
area = (f"M {pts[0][0]:.1f},{y(lo):.1f} L "
        + " L ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        + f" L {pts[-1][0]:.1f},{y(lo):.1f} Z")

# y gridlines at 100k, 125k, 150k, 175k
ygrid = ""
for v in (100000, 125000, 150000, 175000):
    yy = y(v)
    ygrid += f'<line class="grid" x1="{PAD_L}" y1="{yy:.1f}" x2="{PAD_L+PW}" y2="{yy:.1f}"/>'
    ygrid += f'<text class="ytick" x="{PAD_L-10}" y="{yy+4:.1f}">${v//1000}k</text>'

# x year ticks
total_days = (END - START).days
xgrid = ""
for yr in range(2020, 2027):
    d = dt.date(yr, 1, 1)
    frac = (d - START).days / total_days
    if frac < 0 or frac > 1:
        continue
    xx = PAD_L + PW * frac
    xgrid += f'<line class="grid" x1="{xx:.1f}" y1="{PAD_T}" x2="{xx:.1f}" y2="{PAD_T+PH}"/>'
    xgrid += f'<text class="xtick" x="{xx:.1f}" y="{PAD_T+PH+22:.1f}">{yr}</text>'

last_x, last_y = pts[-1]

HTML = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  @page {{ size: 8.5in 11in; margin: 0; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html,body {{ width: 8.5in; }}
  body {{
    font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
    color: #16202c; background: #fff;
    padding: 0.62in 0.66in 0.5in;
    -webkit-font-smoothing: antialiased;
  }}
  .eyebrow {{ font-size: 10.5px; letter-spacing: .16em; text-transform: uppercase;
    color: #6b7a8d; font-weight: 700; }}
  h1 {{ font-size: 25px; line-height: 1.16; letter-spacing: -.012em;
    margin: 7px 0 6px; font-weight: 800; }}
  .sub {{ font-size: 12.5px; color: #44515f; line-height: 1.5; max-width: 7.1in; }}
  .sub b {{ color: #16202c; font-weight: 700; }}
  .rule {{ height: 2px; background: #16202c; margin: 13px 0 0; }}
  .row {{ display: flex; gap: 16px; }}
  .stat {{ flex: 1; padding: 11px 0 9px; border-bottom: 1px solid #e6eaef; }}
  .stat .k {{ font-size: 22px; font-weight: 800; letter-spacing: -.01em; }}
  .stat .l {{ font-size: 9.5px; color: #6b7a8d; text-transform: uppercase;
    letter-spacing: .07em; font-weight: 600; margin-top: 2px; }}
  .green {{ color: #1a7f4b; }}
  .sec {{ font-size: 10.5px; letter-spacing: .14em; text-transform: uppercase;
    color: #16202c; font-weight: 800; margin: 18px 0 8px; }}
  .chartwrap {{ border: 1px solid #e6eaef; border-radius: 7px; padding: 10px 12px 6px; }}
  .chart-cap {{ display:flex; justify-content:space-between; align-items:baseline;
    font-size: 11px; color:#6b7a8d; margin: 2px 2px 4px; }}
  .chart-cap b {{ color:#16202c; font-size: 12px; }}
  svg {{ display:block; width: 100%; height: auto; }}
  .grid {{ stroke: #eef1f4; stroke-width: 1; }}
  .ytick {{ fill:#8a97a6; font-size: 10px; text-anchor: end; }}
  .xtick {{ fill:#8a97a6; font-size: 10px; text-anchor: middle; }}
  .eqline {{ fill:none; stroke:#16202c; stroke-width: 2; stroke-linejoin: round; }}
  .eqarea {{ fill: rgba(22,32,44,.06); stroke:none; }}
  .cols {{ display:flex; gap: 22px; }}
  .col {{ flex: 1; }}
  .col h4 {{ font-size: 12px; font-weight: 800; margin: 0 0 5px; }}
  .col p, .col li {{ font-size: 11.3px; color:#44515f; line-height: 1.5; }}
  .col ul {{ list-style: none; }}
  .col li {{ position: relative; padding-left: 14px; margin-bottom: 4px; }}
  .col li:before {{ content:""; position:absolute; left:0; top:7px; width:5px;
    height:5px; background:#16202c; border-radius:50%; }}
  .note {{ font-size: 10.5px; color:#7a8694; line-height:1.5; margin-top: 4px; }}
  .footer {{ margin-top: 17px; padding-top: 11px; border-top: 2px solid #16202c;
    display:flex; justify-content:space-between; align-items:flex-start; gap:18px; }}
  .footer .lead {{ font-size: 12px; color:#16202c; line-height:1.5; max-width: 5.4in; }}
  .footer .lead b {{ font-weight: 800; }}
  .footer .meta {{ font-size: 10px; color:#8a97a6; text-align:right; line-height:1.6;
    white-space:nowrap; }}
  .stack {{ font-size: 10.5px; color:#6b7a8d; margin-top: 3px; font-weight:600;
    letter-spacing:.02em; }}
</style></head><body>

  <div class="eyebrow">Algorithmic Trading · Work in Progress</div>
  <h1>Automating a discretionary swing-trading method</h1>
  <div class="sub">A hand-traded RSI/MACD mean-reversion method (the &ldquo;SID
    Method,&rdquo; reported at a <b>~76% win rate</b>) ported to code and validated
    honestly. <b>I own the strategy, the test design, and every judgment call;
    I use Claude Code as the implementation &amp; automation engine</b> &mdash;
    it wrote the Python and built a one-command deploy pipeline.
    <span class="stack">Python · QuantConnect/LEAN · GitHub · Claude Code</span>
  </div>
  <div class="rule"></div>

  <div class="row">
    <div class="stat"><div class="k green">+70.5%</div><div class="l">Net return, 6.3 yrs</div></div>
    <div class="stat"><div class="k">8.8%</div><div class="l">CAGR</div></div>
    <div class="stat"><div class="k">12.4%</div><div class="l">Max drawdown</div></div>
    <div class="stat"><div class="k">0.30</div><div class="l">Sharpe</div></div>
    <div class="stat"><div class="k green">+4.5%</div><div class="l">2022 vs SPY &minus;18%</div></div>
  </div>

  <div class="sec">Equity curve &mdash; honesty benchmark (survivorship-free)</div>
  <div class="chartwrap">
    <div class="chart-cap">
      <span><b>Survivorship-free universe</b> · longs-only · trailing exit · 1% risk/trade</span>
      <span>2020 &ndash; 2026 · QuantConnect/LEAN · realistic IBKR fills</span>
    </div>
    <svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet">
      {ygrid}
      {xgrid}
      <path class="eqarea" d="{area}"/>
      <path class="eqline" d="{line}"/>
      <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.2" fill="#1a7f4b"/>
    </svg>
  </div>
  <div class="note">This is the <b>deliberately unbiased</b> test: the method run on a
    point-in-time, survivorship-free universe rather than the author&rsquo;s curated
    watchlist &mdash; it isolates edge that comes from <i>the method</i>, not from
    hindsight ticker selection. It held up through the 2022 bear market (+4.5% vs SPY &minus;18%).</div>

  <div style="margin-top:11px;padding:10px 13px;border-left:3px solid #16202c;background:#f5f7f9;border-radius:0 5px 5px 0;font-size:11px;color:#2a3744;line-height:1.5;">
    <b>Key finding &mdash; the short side is dead.</b> Running the identical method on
    <b>both sides</b> drops it from <b>+70.5% to &minus;16.0%</b> (max drawdown 40%):
    shorting overbought equities has negative expectancy, matching the published
    mean-reversion literature. Long-only isn&rsquo;t cherry-picking &mdash; it&rsquo;s the result of testing both.
  </div>

  <div class="sec">How it&rsquo;s built &amp; validated</div>
  <div class="cols">
    <div class="col">
      <h4>I drive</h4>
      <ul>
        <li>Encode the published checklist 1:1 &mdash; RSI 30/70 entry, RSI+MACD confirmation, &gt;14-day earnings filter, whole-number swing stop, RSI-50 exit.</li>
        <li>Design the validation: train/test holdout, survivorship-free universe, and cross-checks against the author&rsquo;s own logged trades.</li>
        <li>Decide what to test next &mdash; which filter to ablate, which assumption to stress.</li>
      </ul>
    </div>
    <div class="col">
      <h4>Claude Code executes</h4>
      <ul>
        <li><b>Implementation</b> &mdash; turns the method into a parameterized QuantConnect algorithm (one compile drives every variant).</li>
        <li><b>Automation</b> &mdash; a one-command deploy (<b>make deploy</b> &rarr; push · compile · backtest · ship) plus a CI&rsquo;d unit-test suite.</li>
      </ul>
    </div>
    <div class="col">
      <h4>Honest result</h4>
      <ul>
        <li><b>Short leg has no edge:</b> trading both sides cuts +70% to &minus;16% &mdash; a documented mean-reversion asymmetry &mdash; so the deployable config is <b>long-only</b>. A finding, not a filter.</li>
        <li>Win rate on faithful runs <b>~55&ndash;58%</b> vs his reported <b>~76%</b>; the gap is hand discretion (one daily &ldquo;top pick,&rdquo; chart reads, early exits), not a coding error.</li>
        <li><b>Next:</b> forward paper-trade, then the options overlay he actually trades.</li>
      </ul>
    </div>
  </div>

  <div class="footer">
    <div class="lead">The point isn&rsquo;t the headline return &mdash; it&rsquo;s the
      <b>process</b>: a proven manual method, encoded faithfully, tested against its own
      bias, and wired into a repeatable deploy pipeline. <b>I bring the strategy and the
      skepticism; Claude Code is the build engine.</b></div>
    <div class="meta">Sydney Watkins<br>github.com/sydneywatk/trader<br>QuantConnect/LEAN · 2020&ndash;2026</div>
  </div>

</body></html>"""

with open("sid_onepager.html", "w") as f:
    f.write(HTML)
print("wrote sid_onepager.html")
