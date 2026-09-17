ALTER TABLE file_versions DROP CONSTRAINT file_versions_status_check;
ALTER TABLE file_versions ADD CONSTRAINT file_versions_status_check CHECK (status IN ('uploaded','parsing','pending_index','ready','failed','revoked','published'));
ALTER TABLE file_versions ADD COLUMN provenance jsonb;
ALTER TABLE file_versions ADD COLUMN source_run_id uuid REFERENCES runs(id);
CREATE UNIQUE INDEX artifact_run_file ON file_versions(source_run_id,file_id) WHERE source_run_id IS NOT NULL;
