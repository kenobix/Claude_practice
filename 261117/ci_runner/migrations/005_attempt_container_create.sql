-- Existing attempts retain unknown create history. Only new claims are tracked.
ALTER TABLE attempts
    ADD COLUMN create_tracked boolean NOT NULL DEFAULT false,
    ADD COLUMN create_session uuid UNIQUE,
    ADD COLUMN create_daemon_id uuid,
    ADD COLUMN created_container_id text UNIQUE CHECK (created_container_id ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT attempts_create_identity CHECK (
        (create_session IS NULL AND create_daemon_id IS NULL AND created_container_id IS NULL)
        OR (create_tracked AND create_session IS NOT NULL AND create_daemon_id IS NOT NULL)
    );
