-- P0 initial model. State transition rules require conditional application SQL in P1/P2.
CREATE TABLE runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id text NOT NULL,
    ref text NOT NULL,
    commit_sha text NOT NULL CHECK (commit_sha ~ '^[0-9a-f]{40}$'),
    workflow_version text NOT NULL,
    state text NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued','running','succeeded','failed','timed_out','canceled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    available_at timestamptz NOT NULL DEFAULT now(),
    cancel_requested_at timestamptz,
    completed_attempt_id uuid,
    UNIQUE (repository_id, ref, commit_sha, workflow_version)
);
CREATE INDEX runs_queue ON runs (available_at, created_at, id) WHERE state = 'queued';

CREATE TABLE attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES runs(id),
    attempt_no integer NOT NULL CHECK (attempt_no > 0),
    worker_id text NOT NULL,
    lease_token uuid NOT NULL UNIQUE,
    lease_expires_at timestamptz NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    state text NOT NULL DEFAULT 'running'
        CHECK (state IN ('running','succeeded','failed','timed_out','canceled')),
    exit_code integer,
    reason text,
    retry_kind text NOT NULL CHECK (retry_kind IN ('initial','infrastructure','manual')),
    CHECK (lease_expires_at > started_at),
    CHECK ((state = 'running' AND finished_at IS NULL) OR
           (state <> 'running' AND finished_at IS NOT NULL)),
    UNIQUE (run_id, attempt_no),
    UNIQUE (run_id, id)
);
CREATE UNIQUE INDEX attempts_one_running ON attempts (run_id) WHERE state = 'running';
CREATE INDEX attempts_expiry ON attempts (lease_expires_at) WHERE state = 'running';
ALTER TABLE runs ADD CONSTRAINT runs_completed_attempt_fk
    FOREIGN KEY (id, completed_attempt_id) REFERENCES attempts (run_id, id);

CREATE TABLE deliveries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    hook_id text NOT NULL,
    delivery_id text NOT NULL,
    body_sha256 text NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
    received_at timestamptz NOT NULL DEFAULT now(),
    result text NOT NULL CHECK (result IN ('accepted','ignored')),
    run_id uuid REFERENCES runs(id),
    CHECK ((result = 'accepted' AND run_id IS NOT NULL) OR result = 'ignored'),
    UNIQUE (hook_id, delivery_id)
);

CREATE TABLE artifacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id uuid NOT NULL REFERENCES attempts(id),
    relative_name text NOT NULL CHECK (relative_name ~ '^[a-zA-Z0-9_-]+[.][a-z0-9]+$'),
    size_bytes bigint NOT NULL CHECK (size_bytes BETWEEN 0 AND 10485760),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    storage_key text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (attempt_id, relative_name)
);
