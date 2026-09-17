CREATE TABLE project_memories (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    content text NOT NULL CHECK (length(content) BETWEEN 1 AND 1000),
    version integer NOT NULL DEFAULT 1,
    source_message_id uuid REFERENCES messages(id),
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id,owner_id) REFERENCES projects(id,owner_id)
);
CREATE TABLE project_revisions (
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    revision integer NOT NULL,
    state jsonb NOT NULL,
    memories jsonb NOT NULL,
    change jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(project_id,revision),
    FOREIGN KEY (project_id,owner_id) REFERENCES projects(id,owner_id)
);
ALTER TABLE runs ADD COLUMN project_snapshot jsonb;
CREATE INDEX active_project_memories ON project_memories(project_id,created_at) WHERE revoked_at IS NULL;
