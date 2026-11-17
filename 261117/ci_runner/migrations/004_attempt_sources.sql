-- Provenance for official results from the approved commit-source Worker.
-- Local fixture results and pre-existing attempts have no row in this table.
CREATE TABLE attempt_sources (
    attempt_id uuid PRIMARY KEY,
    run_id uuid NOT NULL,
    commit_sha text NOT NULL CHECK (commit_sha ~ '^[0-9a-f]{40}$'),
    main_sha text NOT NULL CHECK (main_sha ~ '^[0-9a-f]{40}$'),
    blob_sha text NOT NULL CHECK (blob_sha ~ '^[0-9a-f]{40}$'),
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    image_id text NOT NULL CHECK (image_id ~ '^sha256:[0-9a-f]{64}$'),
    FOREIGN KEY (run_id, attempt_id) REFERENCES attempts (run_id, id)
);
