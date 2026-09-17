CREATE TABLE embedding_cache (
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    text_sha256 text NOT NULL,
    model text NOT NULL,
    dimension integer NOT NULL CHECK (dimension=2048),
    embedding vector(2048) NOT NULL,
    PRIMARY KEY(project_id,owner_id,text_sha256,model),
    FOREIGN KEY(project_id,owner_id) REFERENCES projects(id,owner_id)
);
CREATE TABLE chunk_embeddings (
    chunk_id uuid PRIMARY KEY REFERENCES document_chunks(id),
    model text NOT NULL,
    dimension integer NOT NULL CHECK (dimension=2048),
    embedding vector(2048) NOT NULL
);
ALTER TABLE file_jobs ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}';
ALTER TABLE runs ADD COLUMN request_options jsonb NOT NULL DEFAULT '{}';
ALTER TABLE runs ADD COLUMN skill_snapshot jsonb;
ALTER TABLE runs ADD COLUMN result_json jsonb;
CREATE TABLE context_snapshots (
    run_id uuid PRIMARY KEY REFERENCES runs(id),
    context jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE run_citations (
    run_id uuid NOT NULL REFERENCES runs(id),
    ordinal integer NOT NULL,
    chunk_id uuid NOT NULL REFERENCES document_chunks(id),
    quote text NOT NULL,
    PRIMARY KEY(run_id,ordinal)
);
