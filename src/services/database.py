"""
Database service with connection pooling and async operations.

Provides high-level database operations for the Kick streamer monitoring application
with connection pooling, transaction management, and performance optimization.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Union, AsyncGenerator
from urllib.parse import urlparse

import asyncpg
from asyncpg import Pool, Connection, Record
from asyncpg.pool import PoolConnectionProxy

from ..models import (
    Streamer, StreamerCreate, StreamerUpdate, StreamerStatusUpdate, StreamerStatus,
    StatusEvent, StatusEventCreate, StatusEventUpdate, StatusEventQuery, EventType,
    Configuration, ConfigurationCreate, ConfigurationUpdate, ConfigCategory
)

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Base exception for database operations."""
    pass


class ConnectionError(DatabaseError):
    """Database connection related errors."""
    pass


class TransactionError(DatabaseError):
    """Database transaction related errors."""
    pass


class DatabaseConfig:
    """Database configuration settings."""
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "kick_monitor",
        username: str = "kick_monitor",
        password: str = "",
        min_connections: int = 5,
        max_connections: int = 20,
        command_timeout: float = 30.0,
        server_settings: Optional[Dict[str, str]] = None
    ):
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.command_timeout = command_timeout
        self.server_settings = server_settings or {}
    
    @classmethod
    def from_url(cls, database_url: str) -> 'DatabaseConfig':
        """Create config from database URL."""
        parsed = urlparse(database_url)
        
        return cls(
            host=parsed.hostname or "localhost",
            port=parsed.port or 5432,
            database=parsed.path.lstrip('/') if parsed.path else "kick_monitor",
            username=parsed.username or "kick_monitor",
            password=parsed.password or "",
        )
    
    def get_dsn(self) -> str:
        """Get database connection DSN."""
        return f"postgresql://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}"


class DatabaseService:
    """
    Main database service with connection pooling and async operations.
    
    Provides high-level operations for streamers, status events, and configuration
    with proper connection management and error handling.
    """
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.pool: Optional[Pool] = None
        self._is_connected = False
        
    async def connect(self) -> None:
        """Initialize database connection pool."""
        if self._is_connected:
            logger.warning("Database service already connected")
            return
        
        try:
            logger.info(f"Connecting to database at {self.config.host}:{self.config.port}")
            
            self.pool = await asyncpg.create_pool(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.username,
                password=self.config.password,
                min_size=self.config.min_connections,
                max_size=self.config.max_connections,
                command_timeout=self.config.command_timeout,
                server_settings=self.config.server_settings,
                init=self._init_connection
            )
            
            self._is_connected = True
            logger.info(f"Database connection pool created ({self.config.min_connections}-{self.config.max_connections} connections)")
            
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise ConnectionError(f"Database connection failed: {e}") from e
    
    async def disconnect(self) -> None:
        """Close database connection pool."""
        if not self._is_connected or not self.pool:
            return
        
        try:
            logger.info("Closing database connection pool")
            await self.pool.close()
            self._is_connected = False
            self.pool = None
            logger.info("Database connection pool closed")
        except Exception as e:
            logger.error(f"Error closing database pool: {e}")
    
    async def _init_connection(self, conn: Connection) -> None:
        """Initialize connection settings."""
        # Set timezone to UTC
        await conn.execute("SET timezone = 'UTC'")
        
        # Set search path if needed
        # await conn.execute("SET search_path TO public")
    
    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[PoolConnectionProxy, None]:
        """Get database connection from pool."""
        if not self._is_connected or not self.pool:
            raise ConnectionError("Database service not connected")
        
        async with self.pool.acquire() as conn:
            yield conn
    
    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[PoolConnectionProxy, None]:
        """Get database connection with transaction management."""
        async with self.get_connection() as conn:
            async with conn.transaction():
                yield conn
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform database health check."""
        try:
            async with self.get_connection() as conn:
                # Test connection
                result = await conn.fetchval("SELECT 1")
                
                # Get pool stats
                pool_stats = {
                    "size": self.pool.get_size(),
                    "min_size": self.pool.get_min_size(), 
                    "max_size": self.pool.get_max_size(),
                    "idle_size": self.pool.get_idle_size(),
                }
                
                return {
                    "status": "healthy",
                    "connection_test": result == 1,
                    "pool_stats": pool_stats,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    # =========================================================================
    # STREAMER OPERATIONS
    # =========================================================================
    
    async def create_streamer(self, streamer: StreamerCreate) -> Streamer:
        """Create a new streamer record."""
        async with self.transaction() as conn:
            try:
                query = """
                INSERT INTO streamer (kick_user_id, username, display_name, status, is_active)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, kick_user_id, username, display_name, status, 
                         last_seen_online, last_status_update, created_at, updated_at, is_active
                """
                
                record = await conn.fetchrow(
                    query,
                    streamer.kick_user_id,
                    streamer.username,
                    streamer.display_name,
                    streamer.status.value,
                    streamer.is_active
                )
                
                if not record:
                    raise DatabaseError("Failed to create streamer record")
                
                return Streamer(**dict(record))
                
            except asyncpg.UniqueViolationError as e:
                if "kick_user_id" in str(e):
                    raise DatabaseError(f"Streamer with kick_user_id '{streamer.kick_user_id}' already exists")
                elif "username" in str(e):
                    raise DatabaseError(f"Streamer with username '{streamer.username}' already exists")
                else:
                    raise DatabaseError(f"Unique constraint violation: {e}") from e
            except Exception as e:
                logger.error(f"Error creating streamer: {e}")
                raise DatabaseError(f"Failed to create streamer: {e}") from e
    
    async def get_streamer(self, streamer_id: int) -> Optional[Streamer]:
        """Get streamer by ID."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, kick_user_id, username, display_name, status, 
                   last_seen_online, last_status_update, created_at, updated_at, is_active
            FROM streamer WHERE id = $1
            """
            
            record = await conn.fetchrow(query, streamer_id)
            return Streamer(**dict(record)) if record else None
    
    async def get_streamer_by_username(self, username: str) -> Optional[Streamer]:
        """Get streamer by username."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, kick_user_id, username, display_name, status, 
                   last_seen_online, last_status_update, created_at, updated_at, is_active
            FROM streamer WHERE username = $1
            """
            
            record = await conn.fetchrow(query, username)
            return Streamer(**dict(record)) if record else None
    
    async def get_streamer_by_kick_user_id(self, kick_user_id: str) -> Optional[Streamer]:
        """Get streamer by Kick user ID."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, kick_user_id, username, display_name, status, 
                   last_seen_online, last_status_update, created_at, updated_at, is_active
            FROM streamer WHERE kick_user_id = $1
            """
            
            record = await conn.fetchrow(query, kick_user_id)
            return Streamer(**dict(record)) if record else None
    
    async def get_active_streamers(self) -> List[Streamer]:
        """Get all active streamers."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, kick_user_id, username, display_name, status, 
                   last_seen_online, last_status_update, created_at, updated_at, is_active
            FROM streamer WHERE is_active = true
            ORDER BY username
            """
            
            records = await conn.fetch(query)
            return [Streamer(**dict(record)) for record in records]
    
    async def update_streamer(self, streamer_id: int, update: StreamerUpdate) -> Optional[Streamer]:
        """Update streamer record."""
        update_fields = []
        values = []
        param_count = 1
        
        # Build dynamic update query
        for field, value in update.dict(exclude_unset=True).items():
            if field == 'status' and isinstance(value, StreamerStatus):
                update_fields.append(f"{field} = ${param_count}")
                values.append(value.value)
            else:
                update_fields.append(f"{field} = ${param_count}")
                values.append(value)
            param_count += 1
        
        if not update_fields:
            # No fields to update
            return await self.get_streamer(streamer_id)
        
        values.append(streamer_id)  # Add ID for WHERE clause
        
        async with self.transaction() as conn:
            query = f"""
            UPDATE streamer 
            SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
            WHERE id = ${param_count}
            RETURNING id, kick_user_id, username, display_name, status, 
                     last_seen_online, last_status_update, created_at, updated_at, is_active
            """
            
            record = await conn.fetchrow(query, *values)
            return Streamer(**dict(record)) if record else None
    
    async def update_streamer_status(self, streamer_id: int, status_update: StreamerStatusUpdate) -> Optional[Streamer]:
        """Update streamer status with timestamp."""
        async with self.transaction() as conn:
            query = """
            UPDATE streamer 
            SET status = $2, 
                last_status_update = $3,
                last_seen_online = CASE 
                    WHEN $2 = 'online' THEN $3 
                    ELSE last_seen_online 
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $1
            RETURNING id, kick_user_id, username, display_name, status, 
                     last_seen_online, last_status_update, created_at, updated_at, is_active
            """
            
            record = await conn.fetchrow(
                query,
                streamer_id,
                status_update.new_status.value,
                status_update.timestamp
            )
            
            return Streamer(**dict(record)) if record else None
    
    async def delete_streamer(self, streamer_id: int) -> bool:
        """Delete streamer record."""
        async with self.transaction() as conn:
            query = "DELETE FROM streamer WHERE id = $1"
            result = await conn.execute(query, streamer_id)
            return result == "DELETE 1"
    
    # =========================================================================
    # STATUS EVENT OPERATIONS  
    # =========================================================================
    
    async def create_status_event(self, event: StatusEventCreate) -> StatusEvent:
        """Create a new status event record."""
        async with self.transaction() as conn:
            try:
                query = """
                INSERT INTO status_event (
                    streamer_id, event_type, previous_status, new_status,
                    kick_event_id, event_timestamp, received_timestamp, event_data
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, streamer_id, event_type, previous_status, new_status,
                         kick_event_id, event_timestamp, received_timestamp, 
                         processed_timestamp, event_data, created_at
                """
                
                record = await conn.fetchrow(
                    query,
                    event.streamer_id,
                    event.event_type.value,
                    event.previous_status.value,
                    event.new_status.value,
                    event.kick_event_id,
                    event.event_timestamp,
                    event.received_timestamp,
                    event.event_data
                )
                
                if not record:
                    raise DatabaseError("Failed to create status event record")
                
                return StatusEvent(**dict(record))
                
            except Exception as e:
                logger.error(f"Error creating status event: {e}")
                raise DatabaseError(f"Failed to create status event: {e}") from e
    
    async def get_status_event(self, event_id: int) -> Optional[StatusEvent]:
        """Get status event by ID."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, streamer_id, event_type, previous_status, new_status,
                   kick_event_id, event_timestamp, received_timestamp, 
                   processed_timestamp, event_data, created_at
            FROM status_event WHERE id = $1
            """
            
            record = await conn.fetchrow(query, event_id)
            return StatusEvent(**dict(record)) if record else None
    
    async def query_status_events(self, query: StatusEventQuery) -> List[StatusEvent]:
        """Query status events with filters."""
        conditions = []
        values = []
        param_count = 1
        
        # Build WHERE clause
        if query.streamer_id:
            conditions.append(f"streamer_id = ${param_count}")
            values.append(query.streamer_id)
            param_count += 1
        
        if query.event_type:
            conditions.append(f"event_type = ${param_count}")
            values.append(query.event_type.value)
            param_count += 1
        
        if query.status_from:
            conditions.append(f"previous_status = ${param_count}")
            values.append(query.status_from.value)
            param_count += 1
        
        if query.status_to:
            conditions.append(f"new_status = ${param_count}")
            values.append(query.status_to.value)
            param_count += 1
        
        if query.after_timestamp:
            conditions.append(f"event_timestamp >= ${param_count}")
            values.append(query.after_timestamp)
            param_count += 1
        
        if query.before_timestamp:
            conditions.append(f"event_timestamp <= ${param_count}")
            values.append(query.before_timestamp)
            param_count += 1
        
        if query.processed_only is not None:
            if query.processed_only:
                conditions.append("processed_timestamp IS NOT NULL")
            else:
                conditions.append("processed_timestamp IS NULL")
        
        # Build full query
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        
        sql_query = f"""
        SELECT id, streamer_id, event_type, previous_status, new_status,
               kick_event_id, event_timestamp, received_timestamp, 
               processed_timestamp, event_data, created_at
        FROM status_event 
        {where_clause}
        ORDER BY event_timestamp DESC
        LIMIT ${param_count} OFFSET ${param_count + 1}
        """
        
        values.extend([query.limit or 100, query.offset or 0])
        
        async with self.get_connection() as conn:
            records = await conn.fetch(sql_query, *values)
            return [StatusEvent(**dict(record)) for record in records]
    
    async def mark_event_processed(self, event_id: int, timestamp: Optional[datetime] = None) -> Optional[StatusEvent]:
        """Mark status event as processed."""
        process_time = timestamp or datetime.now(timezone.utc)
        
        async with self.transaction() as conn:
            query = """
            UPDATE status_event 
            SET processed_timestamp = $2
            WHERE id = $1
            RETURNING id, streamer_id, event_type, previous_status, new_status,
                     kick_event_id, event_timestamp, received_timestamp, 
                     processed_timestamp, event_data, created_at
            """
            
            record = await conn.fetchrow(query, event_id, process_time)
            return StatusEvent(**dict(record)) if record else None
    
    async def get_recent_events(self, limit: int = 100) -> List[StatusEvent]:
        """Get most recent status events."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, streamer_id, event_type, previous_status, new_status,
                   kick_event_id, event_timestamp, received_timestamp, 
                   processed_timestamp, event_data, created_at
            FROM status_event 
            ORDER BY event_timestamp DESC
            LIMIT $1
            """
            
            records = await conn.fetch(query, limit)
            return [StatusEvent(**dict(record)) for record in records]
    
    # =========================================================================
    # CONFIGURATION OPERATIONS
    # =========================================================================
    
    async def create_configuration(self, config: ConfigurationCreate) -> Configuration:
        """Create a new configuration record."""
        async with self.transaction() as conn:
            try:
                query = """
                INSERT INTO configuration (
                    key, value, description, category, value_type, 
                    is_encrypted, is_sensitive, updated_by
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, key, value, description, category, value_type,
                         is_encrypted, is_sensitive, updated_by, created_at, updated_at
                """
                
                record = await conn.fetchrow(
                    query,
                    config.key,
                    config.value,
                    config.description,
                    config.category.value,
                    config.value_type.value,
                    config.is_encrypted,
                    config.is_sensitive,
                    config.updated_by
                )
                
                if not record:
                    raise DatabaseError("Failed to create configuration record")
                
                return Configuration(**dict(record))
                
            except asyncpg.UniqueViolationError:
                raise DatabaseError(f"Configuration key '{config.key}' already exists")
            except Exception as e:
                logger.error(f"Error creating configuration: {e}")
                raise DatabaseError(f"Failed to create configuration: {e}") from e
    
    async def get_configuration(self, key: str) -> Optional[Configuration]:
        """Get configuration by key."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, key, value, description, category, value_type,
                   is_encrypted, is_sensitive, updated_by, created_at, updated_at
            FROM configuration WHERE key = $1
            """
            
            record = await conn.fetchrow(query, key)
            return Configuration(**dict(record)) if record else None
    
    async def get_configurations_by_category(self, category: ConfigCategory) -> List[Configuration]:
        """Get all configurations for a category."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, key, value, description, category, value_type,
                   is_encrypted, is_sensitive, updated_by, created_at, updated_at
            FROM configuration WHERE category = $1
            ORDER BY key
            """
            
            records = await conn.fetch(query, category.value)
            return [Configuration(**dict(record)) for record in records]
    
    async def get_all_configurations(self) -> List[Configuration]:
        """Get all configurations."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, key, value, description, category, value_type,
                   is_encrypted, is_sensitive, updated_by, created_at, updated_at
            FROM configuration 
            ORDER BY category, key
            """
            
            records = await conn.fetch(query)
            return [Configuration(**dict(record)) for record in records]
    
    async def update_configuration(self, key: str, update: ConfigurationUpdate) -> Optional[Configuration]:
        """Update configuration value."""
        update_fields = []
        values = []
        param_count = 1
        
        # Build dynamic update query
        for field, value in update.dict(exclude_unset=True).items():
            update_fields.append(f"{field} = ${param_count}")
            values.append(value)
            param_count += 1
        
        if not update_fields:
            return await self.get_configuration(key)
        
        values.append(key)  # Add key for WHERE clause
        
        async with self.transaction() as conn:
            query = f"""
            UPDATE configuration 
            SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
            WHERE key = ${param_count}
            RETURNING id, key, value, description, category, value_type,
                     is_encrypted, is_sensitive, updated_by, created_at, updated_at
            """
            
            record = await conn.fetchrow(query, *values)
            return Configuration(**dict(record)) if record else None
    
    async def delete_configuration(self, key: str) -> bool:
        """Delete configuration record."""
        async with self.transaction() as conn:
            query = "DELETE FROM configuration WHERE key = $1"
            result = await conn.execute(query, key)
            return result == "DELETE 1"
    
    # =========================================================================
    # UTILITY OPERATIONS
    # =========================================================================
    
    async def execute_schema_migration(self, schema_sql: str) -> None:
        """Execute schema migration SQL."""
        async with self.transaction() as conn:
            try:
                await conn.execute(schema_sql)
                logger.info("Schema migration completed successfully")
            except Exception as e:
                logger.error(f"Schema migration failed: {e}")
                raise DatabaseError(f"Schema migration failed: {e}") from e
    
    async def get_database_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        async with self.get_connection() as conn:
            stats = {}
            
            # Table row counts
            for table in ['streamer', 'status_event', 'configuration']:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                stats[f"{table}_count"] = count
            
            # Active streamers
            active_count = await conn.fetchval("SELECT COUNT(*) FROM streamer WHERE is_active = true")
            stats["active_streamers"] = active_count
            
            # Recent events (last hour)
            recent_events = await conn.fetchval("""
                SELECT COUNT(*) FROM status_event 
                WHERE event_timestamp >= NOW() - INTERVAL '1 hour'
            """)
            stats["recent_events_count"] = recent_events
            
            # Database size
            db_size = await conn.fetchval("""
                SELECT pg_size_pretty(pg_database_size(current_database()))
            """)
            stats["database_size"] = db_size
            
            return stats