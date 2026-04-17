CREATE TABLE IF NOT EXISTS options_rankings (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    rank            INT NOT NULL,
    ticker          VARCHAR(10) NOT NULL,
    market_cap      BIGINT,
    total_vol       BIGINT,
    call_vol        BIGINT,
    put_vol         BIGINT,
    opt_oi          BIGINT,
    iv              NUMERIC(8,4),
    iv_change       NUMERIC(8,4),
    hv              NUMERIC(8,4),
    iv_hv_ratio     NUMERIC(8,4),
    iv_pct_52w      NUMERIC(6,2),
    close_price     NUMERIC(10,2),
    price_change    NUMERIC(8,4),
    volume          BIGINT,
    ytd_change      NUMERIC(8,4),
    next_earnings   DATE,
    days_to_earnings INT,
    UNIQUE (date, rank)
);

CREATE TABLE IF NOT EXISTS iv_history (
    id          SERIAL PRIMARY KEY,
    date        DATE NOT NULL,
    ticker      VARCHAR(10) NOT NULL,
    iv          NUMERIC(8,4),
    is_proxy    BOOLEAN DEFAULT FALSE,
    UNIQUE (date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_rankings_date   ON options_rankings(date DESC);
CREATE INDEX IF NOT EXISTS idx_rankings_ticker ON options_rankings(ticker);
CREATE INDEX IF NOT EXISTS idx_iv_ticker_date  ON iv_history(ticker, date DESC);
