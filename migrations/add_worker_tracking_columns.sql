-- Migration to optimize the worker table in snags database for C2 API
-- The worker table already has these columns:
-- id, str_status, target, count, hostname, wrk_status, wrk_count, updated_at, max_threads, service

-- Add indexes for better performance
CREATE INDEX IF NOT EXISTS idx_worker_hostname
ON worker(hostname);

CREATE INDEX IF NOT EXISTS idx_worker_target
ON worker(target);

CREATE INDEX IF NOT EXISTS idx_worker_updated_at
ON worker(updated_at DESC);

-- Add comments to document column usage
COMMENT ON COLUMN worker.str_status IS 'Manual override for streamer status (online/offline/null)';
COMMENT ON COLUMN worker.target IS 'Streamer username to monitor (matches streamer.username)';
COMMENT ON COLUMN worker.count IS 'Number of workers this node should spawn';
COMMENT ON COLUMN worker.hostname IS 'Unique identifier for worker node';
COMMENT ON COLUMN worker.wrk_status IS 'Current worker node status (online/offline)';
COMMENT ON COLUMN worker.wrk_count IS 'Current number of active workers spawned';
COMMENT ON COLUMN worker.updated_at IS 'Last update timestamp from worker check-in';
COMMENT ON COLUMN worker.service IS 'Service identifier for worker grouping';