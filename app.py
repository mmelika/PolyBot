import dash
from dash import dcc, html, Input, Output, State, ctx
import plotly.graph_objs as go
from datetime import datetime
import os

import config
import database
import trader

os.makedirs("data", exist_ok=True)
database.init_db(config.DB_PATH)

_startup_settings = database.get_settings(config.DB_PATH)
for _mode in ("paper", "real"):
    _cap = _startup_settings["paper_starting_capital"] if _mode == "paper" else _startup_settings["real_starting_capital"]
    if not database.get_portfolio_snapshots(config.DB_PATH, limit=1, mode=_mode):
        database.snapshot_portfolio(config.DB_PATH, _cap, _cap, _mode)

if not database.get_app_state(config.DB_PATH, "trading_mode"):
    database.set_app_state(config.DB_PATH, "trading_mode", config.TRADING_MODE)

trader.start(config.DB_PATH)

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "Polymarket AI Agent"


def fmt_currency(v): return f"${v:,.2f}"
def fmt_pct(v): return f"{v:+.2f}%"
def fmt_price(v): return f"{v:.4f}"
def pnl_class(v): return "pnl-positive" if v >= 0 else "pnl-negative"
def pnl_sign(v): return f"+${v:.2f}" if v >= 0 else f"-${abs(v):.2f}"
def max_profit(size_usd, entry_price):
    return size_usd * (1 - entry_price) / entry_price


def _settings_field(input_id, label, description, placeholder):
    return html.Div(className="settings-field", children=[
        html.Div(label, className="settings-label"),
        html.Div(description, className="settings-desc"),
        dcc.Input(
            id=input_id,
            type="number",
            placeholder=str(placeholder),
            className="settings-input",
            debounce=False,
            min=0,
        ),
    ])


app.layout = html.Div([
    dcc.Interval(id="interval", interval=5000, n_intervals=0),
    html.Div([
        html.Div([
            html.Span([
                "Polymarket ",
                html.Span("AI Agent", style={"color": "#a78bfa"}),
            ], className="topbar-title"),
            html.Span(id="status-pill", children="● RUNNING", className="status-running"),
        ], style={"display": "flex", "alignItems": "center"}),
        html.Div([
            html.Span("⚙", id="settings-gear-btn", n_clicks=0, className="btn-gear", title="Settings"),
            html.Span(id="mode-btn", n_clicks=0, className="btn-paper",
                      children="PAPER MODE", style={"cursor": "pointer", "marginRight": "16px"}),
            html.Span(id="reset-btn", n_clicks=0, className="btn-reset",
                      children="RESET", style={"cursor": "pointer", "marginRight": "16px"}),
            html.Span(id="refresh-text", style={"fontSize": "12px", "color": "#6b7280", "marginRight": "16px"}),
            html.Span(id="clock", style={"fontSize": "12px", "color": "#6b7280"}),
        ], style={"display": "flex", "alignItems": "center"}),
    ], className="topbar"),
    html.Div([
        html.Div(id="stats-row", style={"display": "flex", "gap": "12px", "marginBottom": "16px"}),
        html.Div([
            html.Div([
                html.Div(className="section-card", children=[
                    html.Div(className="section-header", children=[
                        html.Span("Open Positions", className="section-title"),
                        html.Span(id="open-positions-badge", className="badge"),
                    ]),
                    html.Div(id="open-positions-table"),
                ]),
                html.Div(className="section-card", children=[
                    html.Div(className="section-header", children=[
                        html.Span("Portfolio Value", className="section-title"),
                    ]),
                    dcc.Graph(id="portfolio-chart", config={"displayModeBar": False}),
                ]),
                html.Div(className="section-card", children=[
                    html.Div(className="section-header", children=[
                        html.Span("Performance by Category", className="section-title"),
                    ]),
                    html.Div(id="perf-by-category"),
                ]),
            ], style={"flex": "1.2"}),
            html.Div([
                html.Div(className="section-card", children=[
                    html.Div(className="section-header", children=[
                        html.Span("Recent Trades", className="section-title"),
                        html.Span(id="recent-trades-badge", className="badge"),
                    ]),
                    html.Div(id="recent-trades-table"),
                ]),
                html.Div(className="section-card", children=[
                    html.Div(className="section-header", children=[
                        html.Span("Latest Gemini Analysis", className="section-title"),
                    ]),
                    html.Div(id="gemini-reasoning"),
                ]),
                html.Div(className="section-card", children=[
                    html.Div(className="section-header", children=[
                        html.Span("Passed On", className="section-title"),
                        html.Span(id="passed-on-badge", className="badge"),
                    ]),
                    html.Div(id="passed-on-table"),
                ]),
            ], style={"flex": "1"}),
        ], style={"display": "flex", "gap": "16px"}),
    ], style={"padding": "16px"}),
    html.Div(id="reset-dummy", style={"display": "none"}),
    # Settings modal (hidden by default)
    html.Div(
        id="settings-modal",
        className="modal-overlay",
        style={"display": "none"},
        children=[
            html.Div(className="modal-box", children=[
                html.Div(className="modal-header", children=[
                    html.Div("⚙  Settings", className="modal-title"),
                    html.Span("✕", id="modal-close-btn", n_clicks=0, className="modal-close-btn"),
                ]),
                html.Div(className="modal-body", children=[
                    html.Div("CAPITAL", className="modal-section-label"),
                    html.Div(className="modal-row", children=[
                        _settings_field("input-paper-capital", "Paper Balance ($)",
                            "Virtual money for practice trading. Changes take effect on the next trade.", 5000),
                        _settings_field("input-real-capital", "Real Balance ($)",
                            "Your actual Polymarket wallet balance. Used to size real trades.", 5000),
                    ]),
                    html.Div("RISK MANAGEMENT", className="modal-section-label"),
                    html.Div(className="modal-row", children=[
                        _settings_field("input-min-advantage", "Minimum Advantage (%)",
                            "Only trade when the AI sees at least this % better odds than the market price. Higher = fewer, more selective trades.", 8),
                        _settings_field("input-max-position", "Max Position Size ($)",
                            "The largest single trade in dollars.", 20),
                    ]),
                    html.Div(className="modal-row", children=[
                        _settings_field("input-max-deployed", "Max Deployed (%)",
                            "Never put more than this % of your balance into open trades at once.", 80),
                        _settings_field("input-scan-interval", "Scan Interval (minutes)",
                            "How often the bot scans for new opportunities.", 10),
                    ]),
                    html.Div("MARKET FILTERS", className="modal-section-label"),
                    html.Div(className="modal-row", children=[
                        _settings_field("input-min-volume", "Min Market Volume ($)",
                            "Skip markets with less than this amount in total trading volume. Thin markets are less reliable.", 1000),
                        _settings_field("input-long-term-days", "Long-term Cutoff (days)",
                            "Treat a market as long-term if it closes more than this many days from now.", 7),
                    ]),
                    html.Div(className="modal-row", children=[
                        _settings_field("input-long-term-prob", "Long-term Min Probability (%)",
                            "For long-term markets, only trade if the AI gives at least this % probability.", 80),
                        html.Div(style={"flex": "1"}),
                    ]),
                ]),
                html.Div(className="modal-footer", children=[
                    html.Span("Cancel", id="modal-cancel-btn", n_clicks=0, className="btn-modal-cancel"),
                    html.Span("Save Settings", id="modal-save-btn", n_clicks=0, className="btn-modal-save"),
                ]),
            ]),
        ]),
])


def prob_color(prob):
    if prob is None:
        return "#52525b"
    if prob >= 0.70:
        return "#22c55e"
    if prob >= 0.40:
        return "#fbbf24"
    return "#ef4444"


def _table(headers, rows):
    return html.Table(
        style={"width": "100%", "borderCollapse": "collapse", "fontSize": "12px"},
        children=[
            html.Thead(html.Tr([
                html.Th(h, style={"color": "#6b7280", "fontWeight": "600", "textAlign": "left",
                                  "padding": "6px 8px", "borderBottom": "1px solid #1f2937", "fontSize": "11px"})
                for h in headers
            ])),
            html.Tbody(rows),
        ]
    )


def render_open_positions(trades):
    if not trades:
        return html.Div("No open positions", className="empty-state")
    rows = []
    for t in trades:
        pnl = t.get("pnl", 0)
        outcome_cls = "pill-yes" if t["outcome"] == "YES" else "pill-no"
        prob = t.get("gemini_probability")
        prob_str = f"{prob:.0%}" if prob is not None else "—"
        rows.append(html.Tr([
            html.Td(t["question"], className="market-cell", title=t["question"], style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(html.Span(t["outcome"], className=outcome_cls), style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(prob_str, className="prob-value", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": prob_color(t.get("gemini_probability"))}),
            html.Td(fmt_currency(t["size_usd"]), className="mono", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#a1a1aa"}),
            html.Td(fmt_price(t["entry_price"]), className="mono", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#a1a1aa"}),
            html.Td(fmt_price(t["current_price"]), className="mono", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#a1a1aa"}),
            html.Td(pnl_sign(pnl), className=pnl_class(pnl), style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td((t.get("closes_at") or "")[:10], style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#52525b", "fontSize": "11px"}),
        ]))
    return _table(["MARKET", "OUTCOME", "PROB", "SIZE", "ENTRY", "CURRENT", "P&L", "CLOSES"], rows)


def render_recent_trades(trades):
    if not trades:
        return html.Div("No trades yet", className="empty-state")
    rows = []
    for t in trades[:20]:
        created = t.get("created_at", "")
        time_str = created[11:16] if len(created) >= 16 else created[:10]
        date_str = created[:10] if len(created) >= 10 else ""
        edge_pct = f"{t.get('edge', 0):.1%}" if t.get("edge") else "—"
        outcome_cls = "pill-yes" if t["outcome"] == "YES" else "pill-no"
        prob = t.get("gemini_probability")
        prob_str = f"{prob:.0%}" if prob is not None else "—"
        status_cls = "pill-pending" if t.get("status") == "PENDING" else "pill-filled"
        rows.append(html.Tr([
            html.Td([
                html.Div(date_str, style={"color": "#52525b", "fontSize": "10px"}),
                html.Div(time_str, style={"color": "#a1a1aa", "fontSize": "12px"}),
            ], style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(t["question"], className="market-cell", title=t["question"], style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(html.Span(t["side"], className="pill-buy"), style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(html.Span(t["outcome"], className=outcome_cls), style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(prob_str, className="prob-value", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": prob_color(t.get("gemini_probability"))}),
            html.Td(fmt_currency(t["size_usd"]), className="mono", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#a1a1aa"}),
            html.Td(fmt_price(t["entry_price"]), className="mono", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#a1a1aa"}),
            html.Td(edge_pct, className="mono", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#a78bfa"}),
            html.Td(html.Span(t["status"], className=status_cls), style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
        ]))
    return _table(["TIME", "MARKET", "SIDE", "OUTCOME", "PROB", "SIZE", "PRICE", "ADVANTAGE", "STATUS"], rows)


def render_portfolio_chart(snapshots, baseline):
    if not snapshots:
        snapshots = [{"timestamp": datetime.now().isoformat(), "total_value": baseline}]

    xs = [s["timestamp"] for s in snapshots]
    ys = [s["total_value"] for s in snapshots]

    # Color based on current value vs starting capital
    last_val = ys[-1] if ys else baseline
    is_positive = last_val >= baseline
    line_color = "#22c55e" if is_positive else "#ef4444"
    fill_color = "rgba(34,197,94,0.15)" if is_positive else "rgba(239,68,68,0.12)"

    # Y-axis range: zoom into ±15% around the baseline so changes are visible
    spread = max(abs(v - baseline) for v in ys) if len(ys) > 1 else baseline * 0.05
    spread = max(spread, baseline * 0.02)  # at least 2% spread so chart isn't flat
    y_min = baseline - spread * 1.4
    y_max = baseline + spread * 1.4

    fig = go.Figure()

    # Invisible baseline trace — the fill on the next trace fills TO this one
    fig.add_trace(go.Scatter(
        x=xs,
        y=[baseline] * len(xs),
        mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0),
        showlegend=False,
        hoverinfo="skip",
    ))

    # Portfolio value trace, fills to the baseline trace above
    fig.add_trace(go.Scatter(
        x=xs,
        y=ys,
        mode="lines",
        line=dict(color=line_color, width=2),
        fill="tonexty",
        fillcolor=fill_color,
        hovertemplate="$%{y:,.2f}<extra></extra>",
        showlegend=False,
    ))

    # Dotted baseline reference line
    fig.add_hline(
        y=baseline,
        line=dict(color="rgba(255,255,255,0.12)", width=1, dash="dot"),
    )

    fig.update_layout(
        paper_bgcolor="#111114",
        plot_bgcolor="#111114",
        font=dict(color="#52525b", size=10, family="Inter, sans-serif"),
        margin=dict(l=52, r=12, t=8, b=32),
        height=220,
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#18181b",
            bordercolor="#27272a",
            font=dict(color="#fff", size=11),
        ),
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            color="#3f3f46",
            tickfont=dict(size=10),
            showline=False,
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=False,
            color="#3f3f46",
            tickfont=dict(size=10),
            tickprefix="$",
            showline=False,
            range=[y_min, y_max],
        ),
    )
    return fig


def render_perf_by_category(perf):
    if not perf:
        return html.Div("No closed trades yet", className="empty-state")
    rows = []
    for cat, stats in perf.items():
        pnl = stats["total_pnl"]
        rows.append(html.Div([
            html.Span(cat.capitalize(), style={"flex": "1", "color": "#ffffff", "fontSize": "13px", "fontWeight": "500"}),
            html.Span(f"{stats['total']} trades", style={"color": "#52525b", "fontSize": "11px", "marginRight": "16px"}),
            html.Span(f"{stats['win_rate']*100:.0f}% WR", style={"color": "#a78bfa", "fontSize": "12px", "fontWeight": "600", "marginRight": "16px"}),
            html.Span(pnl_sign(pnl), className=pnl_class(pnl), style={"fontSize": "12px", "fontFamily": "'Roboto Mono', monospace"}),
        ], className="perf-row"))
    return html.Div(rows)


def render_gemini_reasoning(trades):
    for t in trades:
        if t.get("gemini_reasoning"):
            return html.Div([
                html.Div(t["question"], className="reasoning-question"),
                html.Div(t["gemini_reasoning"], className="reasoning-body"),
            ])
    return html.Div("No analysis yet", className="empty-state")


def render_passed_on(skipped):
    if not skipped:
        return html.Div("No skipped markets yet", className="empty-state")
    rows = []
    for s in skipped[:20]:
        prob = s.get("probability")
        prob_str = f"{prob:.0%}" if prob is not None else "—"
        edge = s.get("edge")
        edge_str = f"{edge:.1%}" if edge is not None else "—"
        conf = s.get("confidence") or "—"
        outcome_cls = "pill-yes" if s.get("side") == "YES" else "pill-no"
        reason = s.get("skip_reason") or "—"
        reason_color = "#ef4444" if any(k in reason for k in ("confidence", "contested")) else "#fbbf24"
        rows.append(html.Tr([
            html.Td(s["question"], className="market-cell", title=s["question"],
                    style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(html.Span(s.get("side", "—"), className=outcome_cls),
                    style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(prob_str, className="prob-value",
                    style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)",
                           "color": prob_color(prob)}),
            html.Td(edge_str, className="mono",
                    style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)",
                           "color": "#a78bfa"}),
            html.Td(conf, style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)",
                                  "color": "#a1a1aa", "fontSize": "11px"}),
            html.Td(reason, style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)",
                                    "color": reason_color, "fontSize": "11px"}),
        ]))
    return _table(["MARKET", "OUTCOME", "PROB", "EDGE", "CONF", "WHY NOT PICKED"], rows)


@app.callback(
    Output("mode-btn", "children"),
    Output("mode-btn", "className"),
    Input("mode-btn", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_mode(n_clicks):
    current = database.get_app_state(config.DB_PATH, "trading_mode", "paper")
    new_mode = "real" if current == "paper" else "paper"
    database.set_app_state(config.DB_PATH, "trading_mode", new_mode)
    return ("REAL MODE", "btn-real") if new_mode == "real" else ("PAPER MODE", "btn-paper")


@app.callback(
    Output("reset-dummy", "children"),
    Input("reset-btn", "n_clicks"),
    prevent_initial_call=True,
)
def reset_paper(_n_clicks):
    s = database.get_settings(config.DB_PATH)
    database.reset_paper_trading(config.DB_PATH, s["paper_starting_capital"])
    return ""


@app.callback(
    Output("input-paper-capital", "value"),
    Output("input-real-capital", "value"),
    Output("input-min-advantage", "value"),
    Output("input-max-position", "value"),
    Output("input-max-deployed", "value"),
    Output("input-scan-interval", "value"),
    Output("input-min-volume", "value"),
    Output("input-long-term-days", "value"),
    Output("input-long-term-prob", "value"),
    Input("settings-gear-btn", "n_clicks"),
    prevent_initial_call=True,
)
def populate_settings_form(_n):
    s = database.get_settings(config.DB_PATH)
    return (
        s["paper_starting_capital"],
        s["real_starting_capital"],
        round(s["min_advantage"] * 100, 1),
        s["max_position_size"],
        round(s["max_deployed_pct"] * 100, 0),
        s["scan_interval_minutes"],
        s["min_market_volume"],
        s["long_term_days"],
        round(s["long_term_min_prob"] * 100, 0),
    )


@app.callback(
    Output("settings-modal", "style"),
    Input("settings-gear-btn", "n_clicks"),
    Input("modal-close-btn", "n_clicks"),
    Input("modal-cancel-btn", "n_clicks"),
    Input("modal-save-btn", "n_clicks"),
    State("input-paper-capital", "value"),
    State("input-real-capital", "value"),
    State("input-min-advantage", "value"),
    State("input-max-position", "value"),
    State("input-max-deployed", "value"),
    State("input-scan-interval", "value"),
    State("input-min-volume", "value"),
    State("input-long-term-days", "value"),
    State("input-long-term-prob", "value"),
    prevent_initial_call=True,
)
def handle_settings_modal(
    _gear, _close, _cancel, _save,
    paper_cap, real_cap, min_adv, max_pos,
    max_dep, scan_int, min_vol, lt_days, lt_prob,
):
    SHOW = {"display": "flex"}
    HIDE = {"display": "none"}

    if ctx.triggered_id == "settings-gear-btn":
        return SHOW

    if ctx.triggered_id == "modal-save-btn":
        raw = {
            "paper_starting_capital": (float(paper_cap) if paper_cap is not None else None),
            "real_starting_capital": (float(real_cap) if real_cap is not None else None),
            "min_advantage": (float(min_adv) / 100.0 if min_adv is not None else None),
            "max_position_size": (float(max_pos) if max_pos is not None else None),
            "max_deployed_pct": (float(max_dep) / 100.0 if max_dep is not None else None),
            "scan_interval_minutes": (int(float(scan_int)) if scan_int is not None else None),
            "min_market_volume": (float(min_vol) if min_vol is not None else None),
            "long_term_days": (int(float(lt_days)) if lt_days is not None else None),
            "long_term_min_prob": (float(lt_prob) / 100.0 if lt_prob is not None else None),
        }
        settings = {k: v for k, v in raw.items() if v is not None}
        if settings:
            old = database.get_settings(config.DB_PATH)
            database.save_settings(config.DB_PATH, settings)
            # Apply new paper capital immediately if no open positions
            mode = database.get_app_state(config.DB_PATH, "trading_mode", "paper")
            if mode == "paper" and "paper_starting_capital" in settings:
                new_cap = settings["paper_starting_capital"]
                if new_cap != old["paper_starting_capital"]:
                    if not database.get_open_trades(config.DB_PATH, "paper"):
                        database.reset_paper_trading(config.DB_PATH, new_cap)
        return HIDE

    return HIDE


@app.callback(
    Output("stats-row", "children"),
    Output("open-positions-table", "children"),
    Output("open-positions-badge", "children"),
    Output("recent-trades-table", "children"),
    Output("recent-trades-badge", "children"),
    Output("portfolio-chart", "figure"),
    Output("perf-by-category", "children"),
    Output("gemini-reasoning", "children"),
    Output("passed-on-table", "children"),
    Output("passed-on-badge", "children"),
    Output("status-pill", "children"),
    Output("status-pill", "className"),
    Output("clock", "children"),
    Output("refresh-text", "children"),
    Input("interval", "n_intervals"),
)
def refresh(_n):
    mode = database.get_app_state(config.DB_PATH, "trading_mode", "paper")
    settings = database.get_settings(config.DB_PATH)
    starting_capital = (
        settings["paper_starting_capital"] if mode == "paper"
        else settings["real_starting_capital"]
    )

    open_trades = database.get_open_trades(config.DB_PATH, mode)
    recent_trades = database.get_recent_trades(config.DB_PATH, limit=20, mode=mode)
    snapshots = database.get_portfolio_snapshots(config.DB_PATH, limit=200, mode=mode)
    performance = database.get_performance_by_category(config.DB_PATH, mode)
    daily = database.get_daily_stats(config.DB_PATH, mode)
    skipped = database.get_skipped_markets(config.DB_PATH, limit=20, mode=mode)
    total_pnl = database.get_total_pnl(config.DB_PATH, mode)
    deployed = database.get_deployed_capital(config.DB_PATH, mode)

    total_value = snapshots[-1]["total_value"] if snapshots else starting_capital
    cash = total_value - deployed
    daily_pnl = daily.get("daily_pnl") or 0
    daily_trades = daily.get("daily_trades") or 0
    pnl_pct = (total_pnl / starting_capital * 100) if starting_capital else 0
    daily_pct = (daily_pnl / starting_capital * 100) if starting_capital else 0

    def stat_card(label, value, sub=None, value_class=None):
        return html.Div(className="stat-card", children=[
            html.Div(label, className="stat-label"),
            html.Div(value, className=f"stat-value {value_class or ''}"),
            html.Div(sub, className="stat-sub") if sub else None,
        ])

    stats = html.Div([
        stat_card("TOTAL VALUE", fmt_currency(total_value), "starting capital"),
        stat_card("CASH BALANCE", fmt_currency(cash), f"Deployed: {fmt_currency(deployed)}"),
        stat_card("DAILY P&L", fmt_currency(daily_pnl), fmt_pct(daily_pct), "stat-positive" if daily_pnl >= 0 else "stat-negative"),
        stat_card("TOTAL P&L", fmt_currency(total_pnl), fmt_pct(pnl_pct), "stat-positive" if total_pnl >= 0 else "stat-negative"),
        stat_card("OPEN POSITIONS", str(len(open_trades)), "active markets"),
        stat_card("DAILY TRADES", str(daily_trades), "today"),
    ], style={"display": "flex", "gap": "12px"})

    status = trader.get_status()
    status_class = "status-running" if status != "STOPPED" else "status-stopped"

    return (
        stats,
        render_open_positions(open_trades), f"{len(open_trades)} positions",
        render_recent_trades(recent_trades), f"{len(recent_trades)} trades",
        render_portfolio_chart(snapshots, starting_capital),
        render_perf_by_category(performance),
        render_gemini_reasoning(recent_trades),
        render_passed_on(skipped), f"{len(skipped)} markets",
        f"● {status}", status_class,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "● Next refresh in 5s",
    )


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8050)
