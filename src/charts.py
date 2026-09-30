"""Draw the README's charts from the warehouse: a 100x price, a vendor reporting the wrong instrument, and cost by weight."""

import sys
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from src import answers, config, db  # noqa: E402

OUT = config.ROOT / "docs" / "images"
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES_1, SERIES_2, CRITICAL = "#2a78d6", "#eb6834", "#c22d2d"
FONT = "Arial"


def axes(title: str, subtitle: str):
    plt.rcParams.update({"font.family": FONT, "font.size": 10})
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    fig.text(0.06, 0.95, title, fontsize=13, fontweight="bold", color=INK, va="top")
    fig.text(0.06, 0.885, subtitle, fontsize=9.5, color=MUTED, va="top")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.subplots_adjust(left=0.1, right=0.95, top=0.8, bottom=0.14)
    return fig, ax


def series(conn, source: str, security: str, start: str, end: str):
    rows = conn.execute(
        f"SELECT price_date, close_zar FROM ({answers.LATEST_PRICE}) WHERE security_id = ? AND price_date BETWEEN ? AND ? "
        "ORDER BY price_date",
        (source, security, start, end),
    ).fetchall()
    return [date.fromisoformat(d) for d, _ in rows], [c for _, c in rows]


def unit_error(conn) -> None:
    days, closes = series(conn, "yahoo", "SBK", "2025-04-07", "2025-05-09")
    bad = days.index(date(2025, 4, 25))
    fig, ax = axes("A Standard Bank price 100 times too small",
                   "Yahoo closing prices, Standard Bank (SBK), April to May 2025, rands")
    ax.plot(days, closes, color=SERIES_1, linewidth=2)
    ax.plot(days[bad], closes[bad], "o", markersize=8, color=CRITICAL, markeredgecolor=SURFACE, markeredgewidth=2)
    ax.annotate(f"25 April: R{closes[bad]:.2f} against R{closes[bad - 1]:.2f} the day before",
                (days[bad], closes[bad]), xytext=(12, 10), textcoords="offset points", color=INK, fontsize=9.5)
    ax.set_ylim(0, max(closes) * 1.12)
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.savefig(OUT / "unit_error_sbk.png", facecolor=SURFACE)
    plt.close(fig)


def wrong_instrument(conn) -> None:
    y_days, y_close = series(conn, "yahoo", "BLU", "2026-08-17", "2026-09-29")
    e_days, e_close = series(conn, "eodhd", "BLU", "2026-08-17", "2026-09-29")
    fig, ax = axes("From 1 September, one vendor reports a different instrument",
                   "Blu Label (BLU) closing prices, rands, log scale: Yahoo against EODHD")
    ax.plot(y_days, y_close, color=SERIES_1, linewidth=2, label="Yahoo")
    ax.plot(e_days, e_close, color=SERIES_2, linewidth=2, label="EODHD")
    ax.set_yscale("log")
    ax.set_yticks([0.01, 0.1, 1, 10])
    ax.set_yticklabels(["R0.01", "R0.10", "R1", "R10"])
    ax.set_ylim(0.02, 30)
    ax.annotate("Yahoo, about R8", (y_days[-1], y_close[-1]), xytext=(-110, 12), textcoords="offset points",
                color=INK, fontsize=9.5)
    ax.annotate("EODHD, 4 to 5 cents on unrelated volumes", (e_days[-1], e_close[-1]), xytext=(-230, 12),
                textcoords="offset points", color=INK, fontsize=9.5)
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK)
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.savefig(OUT / "wrong_instrument_blu.png", facecolor=SURFACE)
    plt.close(fig)


def cost_by_weight(conn) -> None:
    tolerance = answers.MATERIALITY_BP / 10000
    fig, ax = axes("Any holding above 0.51% of a fund breaches the limit on its own",
                   "Cost to a fund of one price 100 times too small, against the holding's weight")
    weights = [w / 1000 for w in range(0, 101)]
    ax.plot([w * 100 for w in weights], [0.99 * w * 100 for w in weights], color=SERIES_1, linewidth=2)
    ax.axhline(tolerance * 100, color=MUTED, linewidth=1.2, linestyle=(0, (4, 3)))
    ax.text(10, tolerance * 100 + 0.2, "0.5% ASISA tolerance", color=MUTED, fontsize=9, ha="right")
    held = answers.weights_on("2025-04-25")[1]
    for code, label, dx in (("SBK", "Standard Bank, 25 Apr 2025", -150), ("SLM", "Sanlam, 10 Jan 2025", 10),
                            ("VOD", "Vodacom, 10 Jan 2025", 10)):
        w = held[code] * 100
        ax.plot(w, 0.99 * w, "o", markersize=8, color=CRITICAL, markeredgecolor=SURFACE, markeredgewidth=2)
        ax.annotate(label, (w, 0.99 * w), xytext=(dx, -4), textcoords="offset points", color=INK, fontsize=9)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.set_xlabel("Holding's weight in the fund (%)", color=MUTED, fontsize=9)
    ax.set_ylabel("Fund misstated by (%)", color=MUTED, fontsize=9)
    fig.savefig(OUT / "cost_by_weight.png", facecolor=SURFACE)
    plt.close(fig)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    unit_error(conn)
    wrong_instrument(conn)
    cost_by_weight(conn)
    print(f"Wrote three charts to {OUT.relative_to(config.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
