-- Relay event store schema. Applied idempotently at startup.

-- The ledger. Append-only; nothing ever UPDATEs or DELETEs rows here.
CREATE TABLE IF NOT EXISTS events (
    run_id      TEXT        NOT NULL,
    seq         INTEGER     NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    event       JSONB       NOT NULL,
    PRIMARY KEY (run_id, seq)          -- physical guarantee: no duplicate seq
);

-- Tiny projection so recovery / listing never has to replay every log.
-- Updated in the SAME transaction as the append, so it never lags.
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT        PRIMARY KEY,
    status     TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status);
CREATE INDEX IF NOT EXISTS idx_events_type ON events ((event->>'type'));
