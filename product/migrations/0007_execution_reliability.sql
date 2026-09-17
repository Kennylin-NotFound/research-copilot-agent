ALTER TABLE jobs ADD COLUMN worker_id text;
ALTER TABLE jobs ADD COLUMN heartbeat_at timestamptz;
ALTER TABLE jobs ADD COLUMN retry_after timestamptz NOT NULL DEFAULT now();
ALTER TABLE jobs ADD COLUMN max_attempts integer NOT NULL DEFAULT 2 CHECK (max_attempts BETWEEN 1 AND 3);
ALTER TABLE jobs ADD COLUMN last_error_code text;
ALTER TABLE jobs DROP CONSTRAINT jobs_status_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_status_check CHECK (status IN ('queued','running','completed','failed','cancelled'));

CREATE TABLE run_events (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq integer NOT NULL CHECK (seq > 0),
    event_type text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE action_records (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    action_id text NOT NULL,
    attempt integer NOT NULL CHECK (attempt > 0),
    action_type text NOT NULL,
    status text NOT NULL CHECK (status IN ('planned','running','ok','error','unknown')),
    request_fingerprint text NOT NULL,
    result_summary text,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, action_id, attempt)
);

CREATE INDEX run_events_created ON run_events(run_id, created_at);
CREATE INDEX jobs_retryable ON jobs(status, retry_after, created_at);
