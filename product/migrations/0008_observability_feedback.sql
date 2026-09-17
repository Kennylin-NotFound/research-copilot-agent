CREATE TABLE feedback (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    project_id uuid NOT NULL,
    message_id uuid NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    artifact_version_id uuid REFERENCES file_versions(id),
    rating smallint NOT NULL CHECK (rating IN (-1, 1)),
    note text CHECK (char_length(note) <= 2000),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id, owner_id) REFERENCES projects(id, owner_id),
    UNIQUE NULLS NOT DISTINCT (owner_id, message_id, artifact_version_id)
);

CREATE INDEX feedback_run ON feedback(run_id, created_at);
