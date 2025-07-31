from __future__ import annotations

"""Visualization utilities using matplotlib with optional Streamlit support."""

from typing import Optional

import pandas as pd


def _show(fig, use_streamlit: bool) -> None:
    """Display ``fig`` either via matplotlib or Streamlit.

    Parameters
    ----------
    fig:
        The matplotlib figure to display.
    use_streamlit:
        When ``True`` and Streamlit is installed the figure is rendered using
        :func:`streamlit.pyplot`.  Otherwise :func:`matplotlib.pyplot.show` is
        used.  This makes it easy to plug these plots into a future Streamlit
        dashboard while keeping dependencies optional.
    """

    if use_streamlit:
        try:  # pragma: no cover - only executed when streamlit is available
            import streamlit as st

            st.pyplot(fig)
            return
        except Exception:
            pass
    import matplotlib.pyplot as plt

    plt.show()


def plot_equity(trades: pd.DataFrame, *, price: Optional[pd.DataFrame] = None, use_streamlit: bool = False):
    """Plot equity curve, drawdown and optional trade markers on price.

    Parameters
    ----------
    trades:
        DataFrame containing at least ``entry_time``, ``exit_time``, ``entry``,
        ``exit``, ``pnl`` and ``direction`` columns.
    price:
        Optional price series with ``timestamp`` and ``close`` columns.  When
        provided the price curve is drawn and trade entry/exit markers are
        overlaid.  If omitted, only equity and drawdown are shown.
    use_streamlit:
        If ``True`` the figure will be rendered using Streamlit when available.
    """

    if trades.empty:
        raise ValueError("trades DataFrame must not be empty")

    equity = trades["pnl"].cumsum()
    drawdown = equity.cummax() - equity

    import matplotlib.pyplot as plt

    nrows = 3 if price is not None else 2
    fig, axes = plt.subplots(nrows, 1, figsize=(10, 6), sharex=True)

    ax_idx = 0
    if price is not None:
        df = price.copy()
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")
        axes[0].plot(df.index, df["close"], label="Price", color="black")
        for _, trade in trades.iterrows():
            color = "green" if trade.get("direction", "long") == "long" else "red"
            axes[0].scatter(trade["entry_time"], trade["entry"], marker="^", color=color)
            axes[0].scatter(trade["exit_time"], trade["exit"], marker="v", color=color)
        axes[0].set_ylabel("Price")
        axes[0].legend()
        ax_idx = 1

    axes[ax_idx].plot(trades["exit_time"], equity, label="Equity")
    axes[ax_idx].set_ylabel("Equity")
    axes[ax_idx].legend()

    axes[ax_idx + 1].plot(trades["exit_time"], drawdown, label="Drawdown", color="red")
    axes[ax_idx + 1].set_ylabel("Drawdown")
    axes[ax_idx + 1].legend()
    axes[ax_idx + 1].set_xlabel("Time")

    fig.tight_layout()
    _show(fig, use_streamlit)
    return fig


def plot_clusters(price: pd.DataFrame, clusters: pd.DataFrame, *, use_streamlit: bool = False):
    """Highlight range ``clusters`` on a price chart.

    Parameters
    ----------
    price:
        DataFrame with ``timestamp`` and ``close`` columns or a ``DatetimeIndex``.
    clusters:
        Output of :func:`utils.range_clusters.find_tight_range_clusters`.
    use_streamlit:
        If ``True`` the figure will be rendered using Streamlit when available.
    """

    df = price.copy()
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")

    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(df.index, df["close"], label="Price")
    for _, row in clusters.iterrows():
        ax.axvspan(row["start"], row["end"], color="orange", alpha=0.3)
    ax.set_title("Tight Range Clusters")
    ax.set_ylabel("Price")
    ax.legend()
    fig.tight_layout()
    _show(fig, use_streamlit)
    return fig


__all__ = ["plot_equity", "plot_clusters"]
