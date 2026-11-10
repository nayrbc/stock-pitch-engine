"""CLI entry point: `pitch TICKER --peers A,B,C`."""

from __future__ import annotations

import typer

from pitch.data import edgar, market

app = typer.Typer(add_completion=False, help="Stock Pitch Engine")


@app.command()
def main(
    ticker: str = typer.Argument(..., help="Target ticker, e.g. NVDA"),
    peers: str = typer.Option("", "--peers", help="Comma-separated peer tickers"),
) -> None:
    """Build valuation model, pitch pack, and memo draft for TICKER."""
    ticker = ticker.upper()
    peer_list = [p.strip().upper() for p in peers.split(",") if p.strip()]

    facts = edgar.get_company_facts(ticker)
    mkt = market.get_market_data(ticker)
    for p in peer_list:
        edgar.get_company_facts(p)
        market.get_market_data(p)

    typer.echo(f"{facts.get('entityName', ticker)}: price {mkt.get('price')}, peers {peer_list or 'none'}")
    # TODO Phase 1: normalize -> dcf -> comps -> pack
    # TODO Phase 2: excel + memo
    typer.echo("Data fetched. Model/pack/memo not implemented yet (Phase 1+).")


if __name__ == "__main__":
    app()
