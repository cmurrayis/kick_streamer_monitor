# Data Model: Kick Streamer Status Monitor

## Core Entities

### Streamer
**Purpose**: Represents a Kick.com content creator being monitored

**Fields**:
- `id` (Primary Key): Unique identifier for the streamer record
- `kick_user_id`: Kick.com's internal user ID for the streamer  
- `username`: Kick.com username (e.g., "gaming_streamer_123")
- `display_name`: Human-readable display name
- `status`: Current online/offline status
- `last_seen_online`: Timestamp of last online detection
- `last_status_update`: Timestamp of most recent status change
- `created_at`: Record creation timestamp
- `updated_at`: Record modification timestamp
- `is_active`: Whether monitoring is enabled for this streamer

**Validation Rules**:
- `username` must be unique and non-empty
- `kick_user_id` must be unique and non-empty  
- `status` must be one of: "online", "offline", "unknown"
- `last_seen_online` cannot be in the future
- `is_active` defaults to true for new records

**State Transitions**:
```
unknown → online (initial detection)
unknown → offline (initial detection)  
online → offline (stream ends)
offline → online (stream starts)
```

### Status Event
**Purpose**: Represents a real-time status change notification from Kick.com

**Fields**:
- `id` (Primary Key): Unique event identifier
- `streamer_id` (Foreign Key): Reference to Streamer entity
- `event_type`: Type of status change ("stream_start", "stream_end")
- `previous_status`: Status before the change
- `new_status`: Status after the change  
- `kick_event_id`: Kick.com's internal event identifier (if available)
- `event_timestamp`: When the event occurred on Kick.com
- `received_timestamp`: When our service received the event
- `processed_timestamp`: When the database was updated
- `event_data`: JSON blob containing raw event data from Kick.com

**Validation Rules**:
- `event_type` must be one of: "stream_start", "stream_end", "connection_test"
- `previous_status` and `new_status` must be valid status values
- `event_timestamp` cannot be in the future
- `received_timestamp` >= `event_timestamp`
- `processed_timestamp` >= `received_timestamp`

### Configuration
**Purpose**: Application configuration and runtime settings

**Fields**:
- `key`: Configuration parameter name
- `value`: Configuration parameter value  
- `description`: Human-readable description of the parameter
- `category`: Configuration category ("auth", "database", "monitoring", "system")
- `is_encrypted`: Whether the value is encrypted at rest
- `updated_at`: When the configuration was last modified
- `updated_by`: System or user that made the change

**Validation Rules**:
- `key` must be unique and follow naming convention
- `category` must be one of predefined categories
- Encrypted values must be properly secured
- Sensitive keys (containing "secret", "password", "token") must have `is_encrypted` = true

## Entity Relationships

### Streamer ↔ Status Event
- **Type**: One-to-Many
- **Relationship**: One Streamer can have many Status Events
- **Foreign Key**: `status_event.streamer_id` → `streamer.id`
- **Cascade**: Delete streamer → cascade delete all related events

## Database Schema Considerations

### Indexes
```sql
-- Primary performance indexes
CREATE INDEX idx_streamer_username ON streamer(username);
CREATE INDEX idx_streamer_kick_user_id ON streamer(kick_user_id);
CREATE INDEX idx_streamer_status_active ON streamer(status, is_active);

-- Event lookup indexes  
CREATE INDEX idx_status_event_streamer_id ON status_event(streamer_id);
CREATE INDEX idx_status_event_timestamp ON status_event(event_timestamp);
CREATE INDEX idx_status_event_processed ON status_event(processed_timestamp);

-- Configuration lookup
CREATE INDEX idx_configuration_category ON configuration(category);
```

### Constraints
```sql
-- Status validation
ALTER TABLE streamer ADD CONSTRAINT chk_status 
  CHECK (status IN ('online', 'offline', 'unknown'));

-- Event type validation  
ALTER TABLE status_event ADD CONSTRAINT chk_event_type
  CHECK (event_type IN ('stream_start', 'stream_end', 'connection_test'));

-- Timestamp ordering
ALTER TABLE status_event ADD CONSTRAINT chk_timestamp_order
  CHECK (received_timestamp >= event_timestamp 
         AND processed_timestamp >= received_timestamp);
```

### Performance Optimizations
- **Partitioning**: Consider partitioning `status_event` table by date for high-volume scenarios
- **Retention**: Implement automatic cleanup of old events (e.g., keep 90 days)
- **Connection Pooling**: Use prepared statements for frequent status updates
- **Batch Updates**: Group multiple status changes into single transaction when possible

## Data Access Patterns

### High-Frequency Operations
1. **Update Streamer Status**: Single record update by `kick_user_id`
2. **Insert Status Event**: Single record insert with streamer reference
3. **Get Active Streamers**: Query all streamers where `is_active = true`

### Medium-Frequency Operations  
1. **Get Streamer by Username**: Lookup by username for CLI/API operations
2. **Get Recent Events**: Query events by time range for monitoring
3. **Configuration Lookup**: Get configuration values by key or category

### Low-Frequency Operations
1. **Add New Streamer**: Insert new streamer record
2. **Deactivate Streamer**: Update `is_active` flag
3. **Event History**: Query historical events for reporting

## Data Consistency Requirements

### ACID Properties
- **Atomicity**: Status updates and event logging must be atomic
- **Consistency**: Foreign key relationships must be maintained
- **Isolation**: Concurrent status updates must not conflict
- **Durability**: Status changes must persist through system failures

### Eventual Consistency Considerations
- **WebSocket Events**: May arrive out of order, use timestamps for ordering
- **Duplicate Events**: Use `kick_event_id` or timestamp deduplication
- **Network Partitions**: Service must handle temporary database unavailability