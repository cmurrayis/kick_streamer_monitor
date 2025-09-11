-- Kick Streamer Status Monitor Database Schema
-- PostgreSQL 12+ compatible schema with proper indexes and constraints
-- 
-- This script creates the complete database schema for the Kick streamer
-- monitoring application including all tables, indexes, constraints, and
-- performance optimizations.

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Drop existing tables if recreating (development only)
-- Uncomment the following lines for development resets
-- DROP TABLE IF EXISTS status_event CASCADE;
-- DROP TABLE IF EXISTS configuration CASCADE;
-- DROP TABLE IF EXISTS streamer CASCADE;

-- =============================================================================
-- STREAMER TABLE
-- =============================================================================
-- Represents Kick.com content creators being monitored

CREATE TABLE IF NOT EXISTS streamer (
    -- Primary key and identification
    id                  SERIAL PRIMARY KEY,
    kick_user_id        VARCHAR(50) NOT NULL UNIQUE,
    username            VARCHAR(100) NOT NULL UNIQUE,
    
    -- Display and status information
    display_name        VARCHAR(200),
    status              VARCHAR(20) NOT NULL DEFAULT 'unknown',
    
    -- Timestamps
    last_seen_online    TIMESTAMP WITH TIME ZONE,
    last_status_update  TIMESTAMP WITH TIME ZONE,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Configuration
    is_active           BOOLEAN NOT NULL DEFAULT TRUE
);

-- Status validation constraint
ALTER TABLE streamer ADD CONSTRAINT chk_streamer_status 
    CHECK (status IN ('online', 'offline', 'unknown'));

-- Timestamp consistency constraints
ALTER TABLE streamer ADD CONSTRAINT chk_streamer_last_seen_not_future
    CHECK (last_seen_online IS NULL OR last_seen_online <= CURRENT_TIMESTAMP);

ALTER TABLE streamer ADD CONSTRAINT chk_streamer_last_update_not_future
    CHECK (last_status_update IS NULL OR last_status_update <= CURRENT_TIMESTAMP);

-- Username validation
ALTER TABLE streamer ADD CONSTRAINT chk_streamer_username_not_empty
    CHECK (LENGTH(TRIM(username)) > 0);

ALTER TABLE streamer ADD CONSTRAINT chk_streamer_kick_user_id_not_empty
    CHECK (LENGTH(TRIM(kick_user_id)) > 0);

-- =============================================================================
-- STATUS_EVENT TABLE  
-- =============================================================================
-- Represents real-time status change notifications from Kick.com

CREATE TABLE IF NOT EXISTS status_event (
    -- Primary key and relationships
    id                   SERIAL PRIMARY KEY,
    streamer_id          INTEGER NOT NULL REFERENCES streamer(id) ON DELETE CASCADE,
    
    -- Event classification
    event_type           VARCHAR(50) NOT NULL,
    previous_status      VARCHAR(20) NOT NULL,
    new_status           VARCHAR(20) NOT NULL,
    
    -- External identifiers
    kick_event_id        VARCHAR(100),
    
    -- Timestamps for latency tracking
    event_timestamp      TIMESTAMP WITH TIME ZONE NOT NULL,
    received_timestamp   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_timestamp  TIMESTAMP WITH TIME ZONE,
    
    -- Raw event data
    event_data           JSONB,
    
    -- Record metadata
    created_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Event type validation constraint
ALTER TABLE status_event ADD CONSTRAINT chk_status_event_type
    CHECK (event_type IN ('stream_start', 'stream_end', 'connection_test'));

-- Status validation constraints
ALTER TABLE status_event ADD CONSTRAINT chk_status_event_previous_status
    CHECK (previous_status IN ('online', 'offline', 'unknown'));

ALTER TABLE status_event ADD CONSTRAINT chk_status_event_new_status  
    CHECK (new_status IN ('online', 'offline', 'unknown'));

-- Timestamp ordering constraints
ALTER TABLE status_event ADD CONSTRAINT chk_status_event_timestamp_order
    CHECK (received_timestamp >= event_timestamp 
           AND (processed_timestamp IS NULL OR processed_timestamp >= received_timestamp)
           AND (processed_timestamp IS NULL OR processed_timestamp >= event_timestamp));

-- Future timestamp validation
ALTER TABLE status_event ADD CONSTRAINT chk_status_event_not_future
    CHECK (event_timestamp <= CURRENT_TIMESTAMP);

-- Event consistency validation
ALTER TABLE status_event ADD CONSTRAINT chk_stream_start_consistency
    CHECK (event_type != 'stream_start' OR (new_status = 'online' AND previous_status != 'online'));

ALTER TABLE status_event ADD CONSTRAINT chk_stream_end_consistency
    CHECK (event_type != 'stream_end' OR (new_status = 'offline' AND previous_status != 'offline'));

-- =============================================================================
-- CONFIGURATION TABLE
-- =============================================================================  
-- Application configuration and runtime settings

CREATE TABLE IF NOT EXISTS configuration (
    -- Primary key and identification
    id                   SERIAL PRIMARY KEY,
    key                  VARCHAR(200) NOT NULL UNIQUE,
    
    -- Configuration data
    value                TEXT NOT NULL,
    description          TEXT,
    category             VARCHAR(50) NOT NULL DEFAULT 'system',
    value_type          VARCHAR(20) NOT NULL DEFAULT 'string',
    
    -- Security and metadata
    is_encrypted         BOOLEAN NOT NULL DEFAULT FALSE,
    is_sensitive         BOOLEAN NOT NULL DEFAULT FALSE,
    updated_by           VARCHAR(100) NOT NULL DEFAULT 'system',
    
    -- Timestamps
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Category validation constraint
ALTER TABLE configuration ADD CONSTRAINT chk_configuration_category
    CHECK (category IN ('auth', 'database', 'monitoring', 'system'));

-- Value type validation constraint  
ALTER TABLE configuration ADD CONSTRAINT chk_configuration_value_type
    CHECK (value_type IN ('string', 'integer', 'float', 'boolean', 'json'));

-- Key format validation (uppercase, letters, numbers, underscores)
ALTER TABLE configuration ADD CONSTRAINT chk_configuration_key_format
    CHECK (key ~ '^[A-Z][A-Z0-9_]*[A-Z0-9]$');

-- Non-empty key constraint
ALTER TABLE configuration ADD CONSTRAINT chk_configuration_key_not_empty
    CHECK (LENGTH(TRIM(key)) > 0);

-- =============================================================================
-- PERFORMANCE INDEXES
-- =============================================================================

-- Streamer table indexes
CREATE INDEX IF NOT EXISTS idx_streamer_username 
    ON streamer(username);

CREATE INDEX IF NOT EXISTS idx_streamer_kick_user_id 
    ON streamer(kick_user_id);

CREATE INDEX IF NOT EXISTS idx_streamer_status_active 
    ON streamer(status, is_active);

CREATE INDEX IF NOT EXISTS idx_streamer_last_seen_online 
    ON streamer(last_seen_online);

CREATE INDEX IF NOT EXISTS idx_streamer_last_status_update 
    ON streamer(last_status_update);

CREATE INDEX IF NOT EXISTS idx_streamer_updated_at 
    ON streamer(updated_at);

-- Status event table indexes
CREATE INDEX IF NOT EXISTS idx_status_event_streamer_id 
    ON status_event(streamer_id);

CREATE INDEX IF NOT EXISTS idx_status_event_timestamp 
    ON status_event(event_timestamp);

CREATE INDEX IF NOT EXISTS idx_status_event_received_timestamp 
    ON status_event(received_timestamp);

CREATE INDEX IF NOT EXISTS idx_status_event_processed_timestamp 
    ON status_event(processed_timestamp);

CREATE INDEX IF NOT EXISTS idx_status_event_type 
    ON status_event(event_type);

CREATE INDEX IF NOT EXISTS idx_status_event_kick_event_id 
    ON status_event(kick_event_id) WHERE kick_event_id IS NOT NULL;

-- Composite indexes for common queries
CREATE INDEX IF NOT EXISTS idx_status_event_streamer_timestamp 
    ON status_event(streamer_id, event_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_status_event_type_timestamp 
    ON status_event(event_type, event_timestamp DESC);

-- JSONB indexes for event data queries
CREATE INDEX IF NOT EXISTS idx_status_event_data 
    ON status_event USING GIN(event_data) WHERE event_data IS NOT NULL;

-- Configuration table indexes
CREATE INDEX IF NOT EXISTS idx_configuration_category 
    ON configuration(category);

CREATE INDEX IF NOT EXISTS idx_configuration_updated_at 
    ON configuration(updated_at);

CREATE INDEX IF NOT EXISTS idx_configuration_is_sensitive 
    ON configuration(is_sensitive, is_encrypted);

-- =============================================================================
-- TRIGGER FUNCTIONS FOR AUTOMATIC TIMESTAMPS
-- =============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply timestamp triggers
CREATE TRIGGER update_streamer_updated_at 
    BEFORE UPDATE ON streamer 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_configuration_updated_at 
    BEFORE UPDATE ON configuration 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- PARTITIONING SETUP (Optional - for high-volume deployments)
-- =============================================================================

-- Uncomment the following section for high-volume deployments
-- This will partition the status_event table by month for better performance

/*
-- Drop the existing table if implementing partitioning
DROP TABLE IF EXISTS status_event CASCADE;

-- Create partitioned table
CREATE TABLE status_event (
    id                   SERIAL,
    streamer_id          INTEGER NOT NULL,
    event_type           VARCHAR(50) NOT NULL,
    previous_status      VARCHAR(20) NOT NULL,
    new_status           VARCHAR(20) NOT NULL,
    kick_event_id        VARCHAR(100),
    event_timestamp      TIMESTAMP WITH TIME ZONE NOT NULL,
    received_timestamp   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_timestamp  TIMESTAMP WITH TIME ZONE,
    event_data           JSONB,
    created_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
) PARTITION BY RANGE (event_timestamp);

-- Create monthly partitions (example for 2025)
CREATE TABLE status_event_2025_01 PARTITION OF status_event 
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');

CREATE TABLE status_event_2025_02 PARTITION OF status_event 
    FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');

-- Add more partitions as needed...

-- Add constraints and indexes to partitioned table
ALTER TABLE status_event ADD CONSTRAINT chk_status_event_type
    CHECK (event_type IN ('stream_start', 'stream_end', 'connection_test'));
-- ... (add other constraints as above)
*/

-- =============================================================================
-- UTILITY VIEWS
-- =============================================================================

-- View for active streamers with latest status
CREATE OR REPLACE VIEW active_streamers AS
SELECT 
    s.id,
    s.kick_user_id,
    s.username,
    s.display_name,
    s.status,
    s.last_seen_online,
    s.last_status_update,
    s.updated_at,
    -- Calculate time since last update
    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - s.last_status_update)) / 60 AS minutes_since_last_update,
    -- Latest event information
    se.event_type AS latest_event_type,
    se.event_timestamp AS latest_event_timestamp
FROM streamer s
LEFT JOIN LATERAL (
    SELECT event_type, event_timestamp 
    FROM status_event 
    WHERE streamer_id = s.id 
    ORDER BY event_timestamp DESC 
    LIMIT 1
) se ON true
WHERE s.is_active = true
ORDER BY s.last_status_update DESC;

-- View for recent status events with streamer info
CREATE OR REPLACE VIEW recent_status_events AS
SELECT 
    se.id,
    se.event_type,
    se.previous_status,
    se.new_status,
    se.event_timestamp,
    se.received_timestamp,
    se.processed_timestamp,
    s.username,
    s.display_name,
    s.kick_user_id,
    -- Calculate processing latency
    EXTRACT(EPOCH FROM (se.processed_timestamp - se.received_timestamp)) AS processing_latency_seconds,
    -- Calculate delivery latency  
    EXTRACT(EPOCH FROM (se.received_timestamp - se.event_timestamp)) AS delivery_latency_seconds
FROM status_event se
JOIN streamer s ON se.streamer_id = s.id
ORDER BY se.event_timestamp DESC;

-- =============================================================================
-- SAMPLE DATA (Development/Testing)
-- =============================================================================

-- Insert sample configuration values
INSERT INTO configuration (key, value, description, category, value_type, updated_by) VALUES
    ('AUTH_TOKEN_REFRESH_MARGIN', '300', 'Seconds before token expiry to refresh', 'auth', 'integer', 'system_init'),
    ('DATABASE_POOL_SIZE', '10', 'Database connection pool size', 'database', 'integer', 'system_init'),
    ('MONITORING_STATUS_UPDATE_INTERVAL', '5', 'Status update interval in seconds', 'monitoring', 'integer', 'system_init'),
    ('SYSTEM_LOG_LEVEL', 'INFO', 'Application log level', 'system', 'string', 'system_init')
ON CONFLICT (key) DO NOTHING;

-- Insert sample streamer (for testing)
INSERT INTO streamer (kick_user_id, username, display_name, status) VALUES
    ('12345', 'test_streamer', 'Test Streamer', 'unknown')
ON CONFLICT (kick_user_id) DO NOTHING;

-- =============================================================================
-- SCHEMA VALIDATION QUERIES
-- =============================================================================

-- Verify table creation
SELECT 
    schemaname,
    tablename,
    tableowner
FROM pg_tables 
WHERE schemaname = 'public' 
  AND tablename IN ('streamer', 'status_event', 'configuration')
ORDER BY tablename;

-- Verify indexes
SELECT 
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes 
WHERE schemaname = 'public' 
  AND tablename IN ('streamer', 'status_event', 'configuration')
ORDER BY tablename, indexname;

-- Verify constraints
SELECT 
    tc.table_name,
    tc.constraint_name,
    tc.constraint_type,
    cc.check_clause
FROM information_schema.table_constraints tc
LEFT JOIN information_schema.check_constraints cc 
    ON tc.constraint_name = cc.constraint_name
WHERE tc.table_schema = 'public' 
  AND tc.table_name IN ('streamer', 'status_event', 'configuration')
ORDER BY tc.table_name, tc.constraint_type, tc.constraint_name;

-- =============================================================================
-- PERFORMANCE NOTES
-- =============================================================================

-- For high-volume deployments, consider:
-- 1. Partitioning status_event table by date/month
-- 2. Implementing automatic cleanup of old events
-- 3. Using connection pooling (handled in application)
-- 4. Regular VACUUM and ANALYZE operations
-- 5. Monitoring slow query log
-- 6. Consider read replicas for reporting queries

-- For maintenance:
-- VACUUM ANALYZE streamer;
-- VACUUM ANALYZE status_event;
-- VACUUM ANALYZE configuration;

-- Schema version tracking (for migrations)
INSERT INTO configuration (key, value, description, category, updated_by) VALUES
    ('SCHEMA_VERSION', '1.0.0', 'Current database schema version', 'system', 'schema_init')
ON CONFLICT (key) DO UPDATE SET 
    value = EXCLUDED.value,
    updated_at = CURRENT_TIMESTAMP;