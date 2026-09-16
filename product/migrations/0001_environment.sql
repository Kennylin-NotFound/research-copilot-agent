CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE installation_probe (
    id uuid PRIMARY KEY,
    value text NOT NULL,
    sample vector(3) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
