"""CLI entry point: `pitch TICKER --peers A,B,C` -> outputs/<TICKER>/{pitch_pack.json, model.xlsx, memo.md}."""

from __future__ import annotations

import json

import typer

from pitch import pack as pack_mod
from pitch.data import edgar
from pitch.dcf import DCFError
from pitch.excel import write_model
from pitch.memo import scenarios, write_memo
from pitch.normalize import NormalizeError

app = typer.Typer(add_completion=False, help="Stock Pitch Engine")


@app.command()
def main(
    ticker: str = typer.Argument(..., help="Target ticker, e.g. NVDA"),
    peers: str = typer.Option("", "--peers", help="Comma-separated peer tickers"),
) -> None:
    """Build pitch_pack.json, model.xlsx and memo.md for TICKER."""
    ticker = ticker.upper()
    peer_list = [p.strip().upper() for p in peers.split(",") if p.strip()]
    try:
        pack_path = pack_mod.run(ticker, peer_list)
    except (pack_mod.UnsupportedCompany, edgar.EdgarError, NormalizeError, DCFError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    pack = json.loads(pack_path.read_text())
    out_dir = pack_path.parent
    model_path = write_model(pack, out_dir / "model.xlsx")
    memo_path = write_memo(pack, out_dir / "memo.md")

    s = scenarios(pack)
    fmt = lambda x: "n/a" if x is None else f"${x:,.2f}"
    typer.echo(f"{pack['meta']['name']} ({ticker}): {s['rating']}, PT {fmt(s['price_target'])} "
               f"vs {fmt(s['current_price'])} | bear {fmt(s['bear'])} / bull {fmt(s['bull'])} | "
               f"WACC {pack['wacc']['wacc']:.2%} | {len(pack['data_quality'])} data-quality flags")
    for p in (pack_path, model_path, memo_path):
        typer.echo(f"  wrote {p}")


if __name__ == "__main__":
    app()
