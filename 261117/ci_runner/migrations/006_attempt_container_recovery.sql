ALTER TABLE attempts
    ADD COLUMN container_recovered_at timestamptz,
    ADD CONSTRAINT attempts_container_recovery_identity CHECK (
        container_recovered_at IS NULL
        OR (created_container_id IS NOT NULL AND finished_at IS NOT NULL
            AND container_recovered_at >= finished_at)
    );
