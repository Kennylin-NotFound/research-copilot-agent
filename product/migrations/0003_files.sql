CREATE TABLE folders (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    parent_id uuid,
    name text NOT NULL,
    system_kind text CHECK (system_kind IN ('original','user_note','generated')),
    FOREIGN KEY (project_id,owner_id) REFERENCES projects(id,owner_id),
    UNIQUE (id,project_id,owner_id),
    FOREIGN KEY (parent_id,project_id,owner_id) REFERENCES folders(id,project_id,owner_id)
);
CREATE UNIQUE INDEX folder_names ON folders(project_id,COALESCE(parent_id,'00000000-0000-0000-0000-000000000000'::uuid),lower(name));
CREATE UNIQUE INDEX system_folders ON folders(project_id,system_kind) WHERE system_kind IS NOT NULL;
CREATE TABLE upload_attempts (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    storage_key text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('uploading','blob_written','attached','failed')),
    size_bytes bigint NOT NULL DEFAULT 0,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id,owner_id) REFERENCES projects(id,owner_id)
);
CREATE TABLE files (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    folder_id uuid NOT NULL,
    display_name text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('original','user_note','generated')),
    current_version_id uuid,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id,owner_id) REFERENCES projects(id,owner_id),
    FOREIGN KEY (folder_id,project_id,owner_id) REFERENCES folders(id,project_id,owner_id),
    UNIQUE (id,project_id,owner_id)
);
CREATE TABLE file_versions (
    id uuid PRIMARY KEY,
    file_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    version integer NOT NULL CHECK (version>0),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    storage_key text NOT NULL UNIQUE,
    size_bytes bigint NOT NULL CHECK (size_bytes>0 AND size_bytes<=20971520),
    media_type text NOT NULL,
    status text NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded','parsing','pending_index','ready','failed','revoked')),
    parser_version text,
    embedding_model text,
    embedding_dimension integer,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (file_id,version),
    UNIQUE (id,file_id),
    UNIQUE (id,project_id,owner_id),
    FOREIGN KEY (file_id,project_id,owner_id) REFERENCES files(id,project_id,owner_id),
    CHECK (status<>'ready' OR (parser_version IS NOT NULL AND embedding_model IS NOT NULL AND embedding_dimension>0))
);
ALTER TABLE files ADD FOREIGN KEY (current_version_id,id) REFERENCES file_versions(id,file_id);
CREATE TABLE document_pages (
    version_id uuid NOT NULL REFERENCES file_versions(id),
    page integer NOT NULL CHECK (page>0),
    text text NOT NULL,
    PRIMARY KEY(version_id,page)
);
CREATE TABLE document_chunks (
    id uuid PRIMARY KEY,
    version_id uuid NOT NULL,
    page integer NOT NULL,
    paragraph integer NOT NULL CHECK (paragraph>0),
    char_start integer NOT NULL,
    char_end integer NOT NULL,
    text text NOT NULL,
    content_sha256 text NOT NULL,
    FOREIGN KEY (version_id,page) REFERENCES document_pages(version_id,page),
    UNIQUE (version_id,page,char_start)
);
CREATE TABLE file_jobs (
    id uuid PRIMARY KEY,
    version_id uuid NOT NULL REFERENCES file_versions(id),
    kind text NOT NULL CHECK (kind IN ('parse','index')),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed')),
    attempt integer NOT NULL DEFAULT 0,
    lease_token uuid,
    lease_until timestamptz,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(version_id,kind)
);
CREATE INDEX file_jobs_queue ON file_jobs(status,created_at);
