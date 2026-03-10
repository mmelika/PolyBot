import dash
from dash import dcc, html, Input, Output
import plotly.graph_objs as go
from datetime import datetime
import os

import config
import database
import trader

os.makedirs("data", exist_ok=True)
database.init_db(config.DB_PATH)

if not database.get_portfolio_snapshots(config.DB_PATH, limit=1):
    database.snapshot_portfolio(config.DB_PATH, config.STARTING_CAPITAL, config.STARTING_CAPITAL, "paper")

if not database.get_app_state(config.DB_PATH, "trading_mode"):
    database.set_app_state(config.DB_PATH, "trading_mode", config.TRADING_MODE)

trader.start(config.DB_PATH)

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "Polymarket AI Agent"

app.layout = html.Div([
    dcc.Interval(id="interval", interval=5000, n_intervals=0),
    html.Div([
        html.Div([
            html.Span("Polymarket AI Agent", className="topbar-title"),
            html.Span(id="status-pill", children="● RUNNING", className="status-running"),
        ], style={"display": "flex", "alignItems": "center"}),
        html.Div([
            html.Span(id="mode-btn", n_clicks=0, className="btn-paper",
                      children="PAPER MODE", style={"cursor": "pointer", "marginRight": "16px"}),
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
            ], style={"flex": "1"}),
        ], style={"display": "flex", "gap": "16px"}),
    ], style={"padding": "16px"}),
])


def fmt_currency(v): return f"${v:,.2f}"
def fmt_pct(v): return f"{v:+.2f}%"
def fmt_price(v): return f"{v:.4f}"
def pnl_class(v): return "pnl-positive" if v >= 0 else "pnl-negative"
def pnl_sign(v): return f"+${v:.2f}" if v >= 0 else f"-${abs(v):.2f}"


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
            html.Td(t["question"][:42], className="cell-primary", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#fff", "fontSize": "12px", "maxWidth": "260px", "overflow": "hidden", "textOverflow": "ellipsis"}),
            html.Td(html.Span(t["outcome"], className=outcome_cls), style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(prob_str, className="prob-value", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
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
            html.Td(t["question"][:32], style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#ffffff", "fontSize": "12px", "maxWidth": "200px", "overflow": "hidden", "textOverflow": "ellipsis"}),
            html.Td(html.Span(t["side"], className="pill-buy"), style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(html.Span(t["outcome"], className=outcome_cls), style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(prob_str, className="prob-value", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(fmt_currency(t["size_usd"]), className="mono", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#a1a1aa"}),
            html.Td(fmt_price(t["entry_price"]), className="mono", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#a1a1aa"}),
            html.Td(edge_pct, className="mono", style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)", "color": "#a78bfa"}),
            html.Td(html.Span(t["status"], className=status_cls), style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
        ]))
    return _table(["TIME", "MARKET", "SIDE", "OUTCOME", "PROB", "SIZE", "PRICE", "EDGE", "STATUS"], rows)


def render_portfolio_chart(snapshots):
    if not snapshots:
        snapshots = [{"timestamp": datetime.now().isoformat(), "total_value": config.STARTING_CAPITAL}]

    baseline = config.STARTING_CAPITAL
    xs = [s["timestamp"] for s in snapshots]
    ys = [s["total_value"] for s in snapshots]

    # Build green (above) and red (below) segments by interpolating crossings
    green_x, green_y, red_x, red_y = [], [], [], []

    for i in range(len(xs)):
        above = ys[i] >= baseline
        if i > 0:
            prev_above = ys[i - 1] >= baseline
            if above != prev_above:
                # Approximate crossing at previous timestamp
                cross_x = xs[i - 1]
                green_x.append(cross_x)
                green_y.append(baseline)
                red_x.append(cross_x)
                red_y.append(baseline)
        if above:
            green_x.append(xs[i])
            green_y.append(ys[i])
            red_x.append(xs[i])
            red_y.append(baseline)
        else:
            red_x.append(xs[i])
            red_y.append(ys[i])
            green_x.append(xs[i])
            green_y.append(baseline)

    fig = go.Figure()

    if green_x:
        fig.add_trace(go.Scatter(
            x=green_x, y=green_y,
            mode="lines",
            line=dict(color="#22c55e", width=2),
            fill="tozeroy",
            fillcolor="rgba(34,197,94,0.12)",
            hovertemplate="%{x}<br>$%{y:,.2f}<extra></extra>",
            showlegend=False,
        ))

    if red_x:
        fig.add_trace(go.Scatter(
            x=red_x, y=red_y,
            mode="lines",
            line=dict(color="#ef4444", width=2),
            fill="tozeroy",
            fillcolor="rgba(239,68,68,0.10)",
            hovertemplate="%{x}<br>$%{y:,.2f}<extra></extra>",
            showlegend=False,
        ))

    # Baseline reference line
    fig.add_hline(
        y=baseline,
        line=dict(color="rgba(255,255,255,0.10)", width=1, dash="dot"),
    )

    fig.update_layout(
        paper_bgcolor="#111114",
        plot_bgcolor="#111114",
        font=dict(color="#52525b", size=10, family="Inter, sans-serif"),
        margin=dict(l=48, r=12, t=8, b=32),
        height=220,
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#18181b", bordercolor="#27272a", font=dict(color="#fff", size=11)),
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
        ),
    )
    return fig


def render_perf_by_category(perf):
    if not perf:
        return html.Div("No closed trades yet", style={"color": "#6b7280", "fontSize": "13px"})
    rows = []
    for cat, stats in perf.items():
        pnl = stats["total_pnl"]
        rows.append(html.Div([
            html.Span(cat.capitalize(), style={"flex": "1", "color": "#e2e8f0", "fontSize": "13px"}),
            html.Span(f"{stats['total']} trades", style={"color": "#6b7280", "fontSize": "12px", "marginRight": "16px"}),
            html.Span(f"{stats['win_rate']*100:.0f}% WR", style={"color": "#60a5fa", "fontSize": "12px", "marginRight": "16px"}),
            html.Span(pnl_sign(pnl), className=pnl_class(pnl), style={"fontSize": "12px"}),
        ], style={"display": "flex", "alignItems": "center", "padding": "8px 0", "borderBottom": "1px solid #1f2937"}))
    return html.Div(rows)


def render_gemini_reasoning(trades):
    for t in trades:
        if t.get("gemini_reasoning"):
            return html.Div([
                html.Div(t["question"][:60], style={"color": "#f1f5f9", "fontWeight": "600", "fontSize": "13px", "marginBottom": "8px"}),
                html.Div(t["gemini_reasoning"], style={"color": "#9ca3af", "fontSize": "12px", "lineHeight": "1.6"}),
            ])
    return html.Div("No analysis yet", style={"color": "#6b7280", "fontSize": "13px"})


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
    Output("stats-row", "children"),
    Output("open-positions-table", "children"),
    Output("open-positions-badge", "children"),
    Output("recent-trades-table", "children"),
    Output("recent-trades-badge", "children"),
    Output("portfolio-chart", "figure"),
    Output("perf-by-category", "children"),
    Output("gemini-reasoning", "children"),
    Output("status-pill", "children"),
    Output("status-pill", "className"),
    Output("clock", "children"),
    Output("refresh-text", "children"),
    Input("interval", "n_intervals"),
)
def refresh(_n):
    open_trades = database.get_open_trades(config.DB_PATH)
    recent_trades = database.get_recent_trades(config.DB_PATH, limit=20)
    snapshots = database.get_portfolio_snapshots(config.DB_PATH, limit=200)
    performance = database.get_performance_by_category(config.DB_PATH)
    daily = database.get_daily_stats(config.DB_PATH)
    total_pnl = database.get_total_pnl(config.DB_PATH)
    deployed = database.get_deployed_capital(config.DB_PATH)

    total_value = snapshots[-1]["total_value"] if snapshots else config.STARTING_CAPITAL
    cash = total_value - deployed
    daily_pnl = daily.get("daily_pnl") or 0
    daily_trades = daily.get("daily_trades") or 0
    pnl_pct = (total_pnl / config.STARTING_CAPITAL * 100) if config.STARTING_CAPITAL else 0
    daily_pct = (daily_pnl / config.STARTING_CAPITAL * 100) if config.STARTING_CAPITAL else 0

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
        render_portfolio_chart(snapshots),
        render_perf_by_category(performance),
        render_gemini_reasoning(recent_trades),
        f"● {status}", status_class,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "● Next refresh in 5s",
    )


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8050)
