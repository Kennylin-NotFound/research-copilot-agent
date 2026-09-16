CREATE TABLE users (
    id uuid PRIMARY KEY,
    username text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sessions (
    token_hash text PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE login_attempts (
    username text PRIMARY KEY,
    failures integer NOT NULL DEFAULT 0,
    window_started timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE projects (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    title text NOT NULL,
    revision integer NOT NULL DEFAULT 0,
    state jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, owner_id)
);
CREATE TABLE conversations (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    title text NOT NULL,
    archived boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id, owner_id) REFERENCES projects(id, owner_id),
    UNIQUE (id, project_id, owner_id)
);
CREATE TABLE messages (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('user', 'assistant')),
    content text NOT NULL,
    client_message_id uuid,
    run_id uuid,
    mode text NOT NULL CHECK (mode IN ('live', 'mock')),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (conversation_id, project_id, owner_id) REFERENCES conversations(id, project_id, owner_id),
    UNIQUE (conversation_id, client_message_id)
);
CREATE TABLE runs (
    id uuid PRIMARY KEY,
    trace_id uuid NOT NULL UNIQUE,
    conversation_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    user_message_id uuid NOT NULL UNIQUE REFERENCES messages(id),
    final_message_id uuid REFERENCES messages(id),
    project_revision integer NOT NULL,
    status text NOT NULL CHECK (status IN ('queued','running','waiting_user','cancelling','cancelled','completed','failed')),
    mode text NOT NULL CHECK (mode IN ('live', 'mock')),
    model text NOT NULL,
    prompt_version text NOT NULL,
    skill_id text,
    error_code text,
    usage jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    FOREIGN KEY (conversation_id, project_id, owner_id) REFERENCES conversations(id, project_id, owner_id)
);
ALTER TABLE messages ADD CONSTRAINT message_run_fk FOREIGN KEY (run_id) REFERENCES runs(id);
CREATE UNIQUE INDEX one_active_run_per_project ON runs(project_id)
    WHERE status IN ('queued','running','waiting_user','cancelling');
CREATE TABLE jobs (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL UNIQUE REFERENCES runs(id),
    status text NOT NULL CHECK (status IN ('queued','running','completed','failed')),
    attempt integer NOT NULL DEFAULT 0,
    lease_token uuid,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE trace_spans (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs(id),
    parent_span_id uuid REFERENCES trace_spans(id),
    kind text NOT NULL,
    name text NOT NULL,
    status text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    started_at timestamptz NOT NULL DEFAULT now(),
    duration_ms integer,
    error_code text
);
CREATE INDEX conversation_messages ON messages(conversation_id, created_at, id);
CREATE INDEX owner_projects ON projects(owner_id, updated_at);
CREATE INDEX owner_conversations ON conversations(owner_id, project_id, updated_at);
CREATE INDEX run_spans ON trace_spans(run_id, started_at);
