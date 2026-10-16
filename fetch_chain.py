"""Step 1-2: pull the SPXW end-of-day chain, save raw Parquet, load into DuckDB.

Run:  .venv\\Scripts\\python.exe fetch_chain.py
"""
import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import config
import theta
from analyze_event import SanityCheckFailed, fail, snapshot_time


def check_expiration_listed(client):
    exps = theta.call(client.option_list_expirations, symbol=config.OPTION_SYMBOL)
    listed = set(pd.to_datetime(exps["expiration"]).dt.date)
    if config.EXPIRATION not in listed:
        fail(f"{config.OPTION_SYMBOL} has no expiration on {config.EXPIRATION}")


def fetch_snapshot_chain(client, trade_date=config.TRADE_DATE, expiration=config.EXPIRATION):
    """All strikes, calls and puts, for one expiration: last NBBO quote at the options close
    (16:15 ET, 13:15 on a half day) on the trade date."""
    chain = theta.call(
        client.option_at_time_quote,
        start_date=trade_date,
        end_date=trade_date,
        time_of_day=snapshot_time(trade_date).strftime("%H:%M:%S.000"),
        symbol=config.OPTION_SYMBOL,
        expiration=expiration,      # one expiry: max_dte is ignored by this endpoint, so never use "*" here
        strike="*",
        right="both",
    )
    if chain is None or len(chain) == 0:
        fail("empty option chain")
    if set(pd.to_datetime(chain["expiration"]).dt.date) != {expiration}:
        fail("chain contains rows for an unexpected expiration")
    if set(chain["right"].str.upper()) != {"CALL", "PUT"}:
        fail(f"chain does not have both calls and puts: {sorted(chain['right'].unique())}")
    # Add the trade date so we can partition on it.
    chain["trade_date"] = trade_date.isoformat()
    return chain


def save_parquet(chain):
    """Writes RAW_DIR/trade_date=YYYY-MM-DD/. Re-runs replace that partition."""
    pq.write_to_dataset(
        pa.Table.from_pandas(chain, preserve_index=False),
        root_path=config.RAW_DIR,
        partition_cols=["trade_date"],
        existing_data_behavior="delete_matching",
    )


def load_duckdb():
    con = duckdb.connect(str(config.DUCKDB_PATH))
    con.execute(f"""
        CREATE OR REPLACE TABLE {config.DUCKDB_TABLE} AS
        SELECT * FROM read_parquet('{config.RAW_DIR.as_posix()}/*/*.parquet', hive_partitioning = true)
    """)
    return con


def example_query(con):
    print(f"\nExample DuckDB query ({config.TRADE_DATE}): quotes per side, plus the 5 strikes nearest the money")
    print(con.execute(f"""
        SELECT trade_date, expiration, "right", count(*) AS contracts, sum(bid > 0) AS with_bid
        FROM {config.DUCKDB_TABLE} WHERE CAST(trade_date AS DATE) = ?
        GROUP BY ALL ORDER BY "right"
    """, [config.TRADE_DATE]).df().to_string(index=False))
    print(con.execute(f"""
        SELECT c.strike, c.bid AS call_bid, c.ask AS call_ask, p.bid AS put_bid, p.ask AS put_ask
        FROM {config.DUCKDB_TABLE} c JOIN {config.DUCKDB_TABLE} p USING (trade_date, expiration, strike)
        WHERE CAST(c.trade_date AS DATE) = ? AND upper(c."right") = 'CALL' AND upper(p."right") = 'PUT'
        ORDER BY abs((c.bid + c.ask) - (p.bid + p.ask)) LIMIT 5
    """, [config.TRADE_DATE]).df().to_string(index=False))


def main():
    client = theta.make_client()
    check_expiration_listed(client)
    chain = fetch_snapshot_chain(client)
    print(f"Fetched {len(chain)} rows: {config.OPTION_SYMBOL} exp {config.EXPIRATION} "
          f"on trade date {config.TRADE_DATE}")
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    save_parquet(chain)
    print(f"Saved Parquet under {config.RAW_DIR}")
    con = load_duckdb()
    print(f"Loaded table '{config.DUCKDB_TABLE}' into {config.DUCKDB_PATH}")
    example_query(con)
    con.close()


if __name__ == "__main__":
    try:
        main()
    except SanityCheckFailed as e:
        raise SystemExit(f"SANITY CHECK FAILED: {e}")
