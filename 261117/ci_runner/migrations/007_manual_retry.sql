-- Existing attempts keep the original budget starting at attempt 1.
ALTER TABLE attempts ADD COLUMN retry_start_no integer NOT NULL DEFAULT 1
    CHECK (retry_start_no BETWEEN 1 AND attempt_no);

CREATE TABLE manual_retries (
    run_id uuid NOT NULL,
    previous_attempt_id uuid NOT NULL,
    requested_at timestamptz NOT NULL,
    PRIMARY KEY (run_id, previous_attempt_id),
    FOREIGN KEY (run_id, previous_attempt_id) REFERENCES attempts (run_id, id)
);
