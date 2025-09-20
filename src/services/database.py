"""
Database service with connection pooling and async operations.

Provides high-level database operations for the Kick streamer monitoring application
with connection pooling, transaction management, and performance optimization.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Union, AsyncGenerator
from urllib.parse import urlparse

import asyncpg
from asyncpg import Pool, Connection, Record
from asyncpg.pool import PoolConnectionProxy

from models import (
    Streamer, StreamerCreate, StreamerUpdate, StreamerStatusUpdate, StreamerStatus,
    StatusEvent, StatusEventCreate, StatusEventUpdate, StatusEventQuery, EventType,
    Configuration, ConfigurationCreate, ConfigurationUpdate, ConfigCategory
)
from models.user import (
    User, UserCreate, UserUpdate, UserRole, UserStatus,
    UserStreamerAssignment, UserStreamerAssignmentCreate
)
from .snags_database import SnagsDatabaseService

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

        # Initialize snags database service for worker assignments
        self.snags_service = SnagsDatabaseService()
        
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

            # Connect to snags database for worker assignments
            try:
                await self.snags_service.connect()
                logger.info("Successfully connected to snags database")
            except Exception as e:
                logger.warning(f"Failed to connect to snags database (worker assignments will be unavailable): {e}")
                # Don't fail the main connection if snags is unavailable

        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise ConnectionError(f"Database connection failed: {e}") from e
    
    async def disconnect(self) -> None:
        """Close database connection pool."""
        if not self._is_connected or not self.pool:
            return
        
        try:
            # Close snags database connection first
            try:
                await self.snags_service.disconnect()
                logger.info("Snags database connection closed")
            except Exception as e:
                logger.warning(f"Error closing snags database connection: {e}")

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
                         last_seen_online, last_status_update, created_at, updated_at, is_active,
                         current_viewers, peak_viewers, avg_viewers, livestream_id, channel_id,
                         profile_picture_url, bio, follower_count, is_live, is_verified
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
    
    async def get_streamer_by_id(self, streamer_id: int) -> Optional[Streamer]:
        """Get streamer by ID."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, kick_user_id, username, display_name, status,
                   last_seen_online, last_status_update, created_at, updated_at, is_active,
                   current_viewers, peak_viewers, avg_viewers, livestream_id, channel_id,
                   profile_picture_url, bio, follower_count, is_live, is_verified
            FROM streamer WHERE id = $1
            """

            record = await conn.fetchrow(query, streamer_id)
            return Streamer(**dict(record)) if record else None
    
    async def get_streamer_by_username(self, username: str) -> Optional[Streamer]:
        """Get streamer by username."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, kick_user_id, username, display_name, status,
                   last_seen_online, last_status_update, created_at, updated_at, is_active,
                   current_viewers, peak_viewers, avg_viewers, livestream_id, channel_id,
                   profile_picture_url, bio, follower_count, is_live, is_verified
            FROM streamer WHERE username = $1
            """

            record = await conn.fetchrow(query, username)
            return Streamer(**dict(record)) if record else None
    
    async def get_streamer_by_kick_user_id(self, kick_user_id: str) -> Optional[Streamer]:
        """Get streamer by Kick user ID."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, kick_user_id, username, display_name, status,
                   last_seen_online, last_status_update, created_at, updated_at, is_active,
                   current_viewers, peak_viewers, avg_viewers, livestream_id, channel_id,
                   profile_picture_url, bio, follower_count, is_live, is_verified
            FROM streamer WHERE kick_user_id = $1
            """

            record = await conn.fetchrow(query, kick_user_id)
            return Streamer(**dict(record)) if record else None
    
    async def get_active_streamers(self) -> List[Streamer]:
        """Get all active streamers."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, kick_user_id, username, display_name, status,
                   last_seen_online, last_status_update, created_at, updated_at, is_active,
                   current_viewers, peak_viewers, avg_viewers, livestream_id, channel_id,
                   profile_picture_url, bio, follower_count, is_live, is_verified
            FROM streamer WHERE is_active = true
            ORDER BY username
            """
            
            records = await conn.fetch(query)
            return [Streamer(**dict(record)) for record in records]

    async def get_active_streamers_with_assigned_viewers(self) -> List[Dict[str, Any]]:
        """
        Get all active streamers with their assigned viewer counts from snags database.

        Returns:
            List of dictionaries containing streamer data plus assigned_viewers and humans counts
        """
        # Get basic streamer data
        streamers = await self.get_active_streamers()

        if not streamers:
            return []

        # Get worker data from snags database (running and assigned)
        usernames = [s.username for s in streamers]
        worker_data_map = await self.snags_service.get_worker_data_for_multiple_streamers(usernames)

        # Combine data
        result = []
        for streamer in streamers:
            worker_data = worker_data_map.get(streamer.username, {'running': 0, 'assigned': 0})
            current_viewers = getattr(streamer, 'current_viewers', None)

            # Handle offline streamers - force viewer counts to 0 if offline
            if streamer.status.value == 'offline':
                current_viewers = 0
            else:
                current_viewers = current_viewers or 0

            running_workers = worker_data['running']
            assigned_capacity = worker_data['assigned']
            humans = max(0, current_viewers - running_workers)  # Can't be negative

            streamer_data = streamer.dict()
            streamer_data.update({
                'current_viewers': current_viewers,  # Ensure offline = 0
                'running_workers': running_workers,  # This will be "Running" column
                'assigned_capacity': assigned_capacity,  # This will be "Assigned" column
                'humans': humans  # Viewers - Running Workers
            })

            result.append(streamer_data)

        return result

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
                     last_seen_online, last_status_update, created_at, updated_at, is_active,
                     current_viewers, peak_viewers, avg_viewers, livestream_id, channel_id,
                     profile_picture_url, bio, follower_count, is_live, is_verified
            """
            
            record = await conn.fetchrow(query, *values)
            return Streamer(**dict(record)) if record else None
    
    async def update_streamer_status(self, streamer_id: int, status_update: StreamerStatusUpdate) -> Optional[Streamer]:
        """Update streamer status with timestamp."""
        async with self.transaction() as conn:
            query = """
            UPDATE streamer
            SET status = $2::varchar,
                last_status_update = $3,
                last_seen_online = CASE
                    WHEN $2::varchar = 'online' THEN $3
                    ELSE last_seen_online
                END,
                current_viewers = CASE
                    WHEN $2::varchar = 'offline' THEN 0
                    ELSE current_viewers
                END,
                is_live = CASE
                    WHEN $2::varchar = 'offline' THEN false
                    ELSE is_live
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $1
            RETURNING id, kick_user_id, username, display_name, status,
                     last_seen_online, last_status_update, created_at, updated_at, is_active,
                     current_viewers, peak_viewers, avg_viewers, livestream_id, channel_id,
                     profile_picture_url, bio, follower_count, is_live, is_verified
            """
            
            record = await conn.fetchrow(
                query,
                streamer_id,
                status_update.new_status.value,
                status_update.timestamp
            )
            
            return Streamer(**dict(record)) if record else None
    
    async def delete_streamer(self, streamer_id: int) -> bool:
        """Delete streamer record (and cascade assignments)."""
        async with self.transaction() as conn:
            # Log assignment count before deletion for audit trail
            assignment_count_query = "SELECT COUNT(*) FROM user_streamer_assignments WHERE streamer_id = $1"
            assignment_count = await conn.fetchval(assignment_count_query, streamer_id)

            if assignment_count > 0:
                logger.info(f"Deleting streamer {streamer_id} will cascade delete {assignment_count} user assignments")

            query = "DELETE FROM streamer WHERE id = $1"
            result = await conn.execute(query, streamer_id)

            if result == "DELETE 1" and assignment_count > 0:
                logger.info(f"Successfully deleted streamer {streamer_id} and cascaded {assignment_count} assignments")

            return result == "DELETE 1"
    
    async def get_streamer_count(self) -> int:
        """Get total count of streamers."""
        try:
            query = "SELECT COUNT(*) FROM streamer"
            result = await self.pool.fetchval(query)
            return result or 0
        except Exception as e:
            logger.error(f"Error getting streamer count: {e}")
            return 0
    
    async def add_streamer(self, username: str) -> bool:
        """Add a new streamer by username."""
        try:
            # Check if streamer already exists
            existing = await self.get_streamer_by_username(username)
            if existing:
                logger.warning(f"Streamer {username} already exists")
                return False

            # Fetch streamer data from Kick API
            kick_data = await self._fetch_kick_channel_data(username)
            if not kick_data:
                logger.error(f"Failed to fetch channel data for {username} from Kick API")
                return False

            # Create new streamer record with real data from Kick API
            streamer_create = StreamerCreate(
                kick_user_id=str(kick_data.get('id')),
                username=kick_data.get('slug', username),
                display_name=kick_data.get('user', {}).get('username', username),
                status=StreamerStatus.UNKNOWN
            )

            result = await self.create_streamer(streamer_create)
            return result is not None
        except Exception as e:
            logger.error(f"Error adding streamer {username}: {e}")
            return False

    async def _fetch_kick_channel_data(self, username: str) -> dict:
        """Fetch channel data from Kick API using both direct and browser fallback methods."""
        import aiohttp

        # First try direct HTTP request
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }

            async with aiohttp.ClientSession(headers=headers) as session:
                url = f"https://kick.com/api/v1/channels/{username}"
                logger.info(f"Trying direct request to: {url}")

                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Direct request successful for {username}: ID={data.get('id')}")
                        return data
                    elif response.status == 404:
                        logger.error(f"Channel {username} not found on Kick.com")
                        return None
                    elif response.status == 403:
                        logger.warning(f"Direct request blocked (403), trying browser fallback for {username}")
                    else:
                        logger.warning(f"Direct request failed for {username}: HTTP {response.status}, trying browser fallback")

        except Exception as e:
            logger.warning(f"Direct request error for {username}: {e}, trying browser fallback")

        # If direct request fails, try browser fallback
        try:
            from .browser_client import BrowserAPIClient
            logger.info(f"Using browser fallback for {username}")

            async with BrowserAPIClient(headless=True) as browser_client:
                data = await browser_client.fetch_channel_data(username)
                if data:
                    logger.info(f"Browser fallback successful for {username}: ID={data.get('id')}")
                    return data
                else:
                    logger.error(f"Browser fallback failed for {username}")
                    return None

        except ImportError:
            logger.error(f"Browser client not available for {username}")
            return None
        except Exception as e:
            logger.error(f"Browser fallback error for {username}: {e}")
            return None
    
    async def update_streamer_profile_data(self, streamer_id: int, profile_data: dict) -> Optional[Streamer]:
        """Update streamer profile data (bio, profile picture, etc.)."""
        try:
            update_fields = []
            values = []
            param_count = 1
            
            # Build dynamic update query for profile fields
            profile_fields = {
                'profile_picture_url': profile_data.get('profile_picture_url'),
                'bio': profile_data.get('bio'), 
                'follower_count': profile_data.get('follower_count'),
                'is_verified': profile_data.get('is_verified'),
                'display_name': profile_data.get('display_name')
            }
            
            for field, value in profile_fields.items():
                if value is not None:
                    update_fields.append(f"{field} = ${param_count}")
                    values.append(value)
                    param_count += 1
            
            if not update_fields:
                # No fields to update
                return await self.get_streamer_by_id(streamer_id)
            
            # Add updated_at timestamp
            update_fields.append(f"updated_at = ${param_count}")
            values.append(datetime.now(timezone.utc))
            param_count += 1
            
            # Add streamer_id for WHERE clause
            values.append(streamer_id)
            
            query = f"""
            UPDATE streamer 
            SET {', '.join(update_fields)}
            WHERE id = ${param_count}
            RETURNING id, kick_user_id, username, display_name, status, profile_picture_url,
                     bio, follower_count, is_live, is_verified, last_seen_online,
                     last_status_update, created_at, updated_at, is_active,
                     current_viewers, peak_viewers, avg_viewers, livestream_id, channel_id
            """
            
            record = await self.pool.fetchrow(query, *values)
            return Streamer(**dict(record)) if record else None
        except Exception as e:
            logger.error(f"Error updating streamer profile data: {e}")
            return None
    
    async def get_all_streamers(self) -> List[Streamer]:
        """Get all streamers."""
        try:
            # Try new schema first, fall back to old schema if columns don't exist
            try:
                query = """
                    SELECT id, username, kick_user_id, status, display_name, profile_picture_url,
                           bio, follower_count, is_live, is_verified, last_seen_online, 
                           last_status_update, created_at, updated_at, is_active
                    FROM streamer
                    ORDER BY username
                    """
                records = await self.pool.fetch(query)
                return [Streamer(**dict(record)) for record in records]
            except Exception as new_schema_error:
                logger.warning(f"New schema failed, trying fallback: {new_schema_error}")
                # Fallback to old schema without new columns
                query = """
                    SELECT id, username, kick_user_id, status, display_name, 
                           last_seen_online, last_status_update, created_at, updated_at, is_active,
                           NULL as profile_picture_url, NULL as bio, 0 as follower_count,
                           FALSE as is_live, FALSE as is_verified
                    FROM streamer
                    ORDER BY username
                    """
                records = await self.pool.fetch(query)
                return [Streamer(**dict(record)) for record in records]
        except Exception as e:
            logger.error(f"Error getting all streamers: {e}")
            return []
    
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
                    json.dumps(event.event_data) if event.event_data is not None else None
                )
                
                if not record:
                    raise DatabaseError("Failed to create status event record")

                return self._convert_status_event_record(record)
                
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
            if not record:
                return None

            return self._convert_status_event_record(record)
    
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
            return [self._convert_status_event_record(record) for record in records]
    
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
            if not record:
                return None

            return self._convert_status_event_record(record)
    
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
            return [self._convert_status_event_record(record) for record in records]
    
    def _convert_status_event_record(self, record) -> StatusEvent:
        """Convert database record to StatusEvent object with proper event_data parsing."""
        record_dict = dict(record)
        if record_dict.get('event_data'):
            try:
                record_dict['event_data'] = json.loads(record_dict['event_data'])
            except (json.JSONDecodeError, TypeError):
                record_dict['event_data'] = None
        return StatusEvent(**record_dict)

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
    
    # =========================================================================
    # USER OPERATIONS  
    # =========================================================================
    
    async def create_user(self, user_create: UserCreate) -> Optional[User]:
        """Create a new user account."""
        async with self.transaction() as conn:
            try:
                # Hash password
                import hashlib
                password_hash = hashlib.sha256((user_create.password + "kick_monitor_salt").encode()).hexdigest()
                
                query = """
                INSERT INTO users (username, email, display_name, password_hash, role, status)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, username, email, display_name, role, status, 
                         created_at, updated_at, last_login
                """
                
                record = await conn.fetchrow(
                    query,
                    user_create.username,
                    user_create.email,
                    user_create.display_name,
                    password_hash,
                    user_create.role.value,
                    UserStatus.ACTIVE.value
                )
                
                if not record:
                    raise DatabaseError("Failed to create user record")
                
                user_dict = dict(record)
                user_dict['password_hash'] = password_hash
                return User(**user_dict)
                
            except asyncpg.UniqueViolationError as e:
                if "username" in str(e):
                    raise DatabaseError(f"Username '{user_create.username}' already exists")
                elif "email" in str(e):
                    raise DatabaseError(f"Email '{user_create.email}' already registered")
                else:
                    raise DatabaseError(f"Unique constraint violation: {e}") from e
            except Exception as e:
                logger.error(f"Error creating user: {e}")
                raise DatabaseError(f"Failed to create user: {e}") from e
    
    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, username, email, display_name, password_hash, role, status,
                   created_at, updated_at, last_login
            FROM users WHERE id = $1
            """
            
            record = await conn.fetchrow(query, user_id)
            return User(**dict(record)) if record else None
    
    async def get_user_by_username(self, username: str) -> Optional[User]:
        """Get user by username."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, username, email, display_name, password_hash, role, status,
                   created_at, updated_at, last_login
            FROM users WHERE username = $1
            """
            
            record = await conn.fetchrow(query, username)
            return User(**dict(record)) if record else None
    
    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, username, email, display_name, password_hash, role, status,
                   created_at, updated_at, last_login
            FROM users WHERE email = $1
            """
            
            record = await conn.fetchrow(query, email)
            return User(**dict(record)) if record else None
    
    async def get_all_users(self) -> List[User]:
        """Get all users."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, username, email, display_name, password_hash, role, status,
                   created_at, updated_at, last_login
            FROM users ORDER BY username
            """
            
            records = await conn.fetch(query)
            return [User(**dict(record)) for record in records]
    
    async def update_user(self, user_id: int, update: UserUpdate) -> Optional[User]:
        """Update user account."""
        update_fields = []
        values = []
        param_count = 1
        
        # Build dynamic update query
        for field, value in update.dict(exclude_unset=True).items():
            if field == 'role' and isinstance(value, UserRole):
                update_fields.append(f"{field} = ${param_count}")
                values.append(value.value)
            elif field == 'status' and isinstance(value, UserStatus):
                update_fields.append(f"{field} = ${param_count}")
                values.append(value.value)
            else:
                update_fields.append(f"{field} = ${param_count}")
                values.append(value)
            param_count += 1
        
        if not update_fields:
            # No fields to update
            return await self.get_user_by_id(user_id)
        
        # Add updated_at
        update_fields.append(f"updated_at = ${param_count}")
        values.append(datetime.now(timezone.utc))
        param_count += 1
        
        # Add user_id for WHERE clause
        values.append(user_id)
        
        async with self.transaction() as conn:
            query = f"""
            UPDATE users 
            SET {', '.join(update_fields)}
            WHERE id = ${param_count}
            RETURNING id, username, email, display_name, password_hash, role, status,
                     created_at, updated_at, last_login
            """
            
            record = await conn.fetchrow(query, *values)
            return User(**dict(record)) if record else None
    
    async def update_user_last_login(self, user_id: int) -> None:
        """Update user's last login timestamp."""
        async with self.transaction() as conn:
            query = """
            UPDATE users 
            SET last_login = $1, updated_at = $1
            WHERE id = $2
            """
            await conn.execute(query, datetime.now(timezone.utc), user_id)
    
    async def delete_user(self, user_id: int) -> bool:
        """Delete user account (and cascade assignments)."""
        async with self.transaction() as conn:
            # Log assignment count before deletion for audit trail
            assignment_count_query = "SELECT COUNT(*) FROM user_streamer_assignments WHERE user_id = $1"
            assignment_count = await conn.fetchval(assignment_count_query, user_id)

            if assignment_count > 0:
                logger.info(f"Deleting user {user_id} will cascade delete {assignment_count} streamer assignments")

            query = "DELETE FROM users WHERE id = $1"
            result = await conn.execute(query, user_id)

            if result == "DELETE 1" and assignment_count > 0:
                logger.info(f"Successfully deleted user {user_id} and cascaded {assignment_count} assignments")

            return result == "DELETE 1"
    
    # =========================================================================
    # USER-STREAMER ASSIGNMENT OPERATIONS  
    # =========================================================================
    
    async def create_user_streamer_assignment(self, assignment: UserStreamerAssignmentCreate, 
                                            assigned_by: Optional[int] = None) -> Optional[UserStreamerAssignment]:
        """Create user-streamer assignment."""
        async with self.transaction() as conn:
            try:
                query = """
                INSERT INTO user_streamer_assignments (user_id, streamer_id, assigned_by)
                VALUES ($1, $2, $3)
                RETURNING id, user_id, streamer_id, assigned_at, assigned_by
                """
                
                record = await conn.fetchrow(
                    query,
                    assignment.user_id,
                    assignment.streamer_id,
                    assigned_by
                )
                
                if not record:
                    raise DatabaseError("Failed to create assignment record")
                
                return UserStreamerAssignment(**dict(record))
                
            except asyncpg.UniqueViolationError:
                raise DatabaseError("User already assigned to this streamer")
            except asyncpg.ForeignKeyViolationError as e:
                if "user_id" in str(e):
                    raise DatabaseError("User not found")
                elif "streamer_id" in str(e):
                    raise DatabaseError("Streamer not found")
                else:
                    raise DatabaseError(f"Foreign key violation: {e}") from e
            except Exception as e:
                logger.error(f"Error creating assignment: {e}")
                raise DatabaseError(f"Failed to create assignment: {e}") from e
    
    async def get_user_streamer_assignments(self, user_id: int) -> List[UserStreamerAssignment]:
        """Get all streamer assignments for a user."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, user_id, streamer_id, assigned_at, assigned_by
            FROM user_streamer_assignments 
            WHERE user_id = $1
            ORDER BY assigned_at DESC
            """
            
            records = await conn.fetch(query, user_id)
            return [UserStreamerAssignment(**dict(record)) for record in records]
    
    async def get_streamer_user_assignments(self, streamer_id: int) -> List[UserStreamerAssignment]:
        """Get all user assignments for a streamer."""
        async with self.get_connection() as conn:
            query = """
            SELECT id, user_id, streamer_id, assigned_at, assigned_by
            FROM user_streamer_assignments 
            WHERE streamer_id = $1
            ORDER BY assigned_at DESC
            """
            
            records = await conn.fetch(query, streamer_id)
            return [UserStreamerAssignment(**dict(record)) for record in records]
    
    async def delete_user_streamer_assignment(self, user_id: int, streamer_id: int) -> bool:
        """Remove user-streamer assignment."""
        async with self.transaction() as conn:
            query = """
            DELETE FROM user_streamer_assignments 
            WHERE user_id = $1 AND streamer_id = $2
            """
            result = await conn.execute(query, user_id, streamer_id)
            return result == "DELETE 1"
    
    async def get_all_user_streamer_assignments(self) -> List[UserStreamerAssignment]:
        """Get all user-streamer assignments."""
        try:
            async with self.get_connection() as conn:
                query = """
                SELECT id, user_id, streamer_id, assigned_at, assigned_by
                FROM user_streamer_assignments
                ORDER BY assigned_at DESC
                """
                records = await conn.fetch(query)
                return [UserStreamerAssignment(**dict(record)) for record in records]
        except Exception as e:
            logger.error(f"Error getting all user streamer assignments: {e}")
            return []
    
    async def get_users_with_streamer_assignments(self) -> List[Dict[str, Any]]:
        """Get all users with their assigned streamer counts."""
        async with self.get_connection() as conn:
            query = """
            SELECT u.id, u.username, u.email, u.display_name, u.role, u.status,
                   u.created_at, u.last_login,
                   COUNT(usa.streamer_id) as assigned_streamers_count
            FROM users u
            LEFT JOIN user_streamer_assignments usa ON u.id = usa.user_id
            GROUP BY u.id, u.username, u.email, u.display_name, u.role, u.status,
                     u.created_at, u.last_login
            ORDER BY u.username
            """
            
            records = await conn.fetch(query)
            return [dict(record) for record in records]

    # =========================================================================
    # DASHBOARD AND ANALYTICS OPERATIONS
    # =========================================================================

    async def get_dashboard_summary(self) -> Dict[str, Any]:
        """Get dashboard summary statistics."""
        async with self.get_connection() as conn:
            # Get basic counts
            stats_query = """
            SELECT
                (SELECT COUNT(*) FROM streamer) as total_streamers,
                (SELECT COUNT(*) FROM streamer WHERE status = 'online') as online_streamers,
                (SELECT COUNT(*) FROM streamer WHERE status = 'offline') as offline_streamers,
                (SELECT COUNT(*) FROM streamer WHERE status = 'unknown') as unknown_streamers,
                (SELECT COUNT(*) FROM users WHERE status = 'active') as active_users,
                (SELECT COUNT(*) FROM user_streamer_assignments) as total_assignments
            """

            stats = await conn.fetchrow(stats_query)

            # Get recent activity count (last 24 hours)
            recent_activity_query = """
            SELECT COUNT(*) as recent_changes
            FROM status_event
            WHERE event_timestamp >= NOW() - INTERVAL '24 hours'
            """

            recent_activity = await conn.fetchval(recent_activity_query)

            # Get total current viewers across all live streams
            total_viewers_query = """
            SELECT COALESCE(SUM(current_viewers), 0) as total_viewers
            FROM streamer
            WHERE status = 'online' AND current_viewers IS NOT NULL
            """

            total_viewers = await conn.fetchval(total_viewers_query)

            return {
                "total_streamers": stats["total_streamers"],
                "online_streamers": stats["online_streamers"],
                "offline_streamers": stats["offline_streamers"],
                "unknown_streamers": stats["unknown_streamers"],
                "active_users": stats["active_users"],
                "total_assignments": stats["total_assignments"],
                "recent_changes_24h": recent_activity or 0,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }

    async def get_recent_status_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent status change events with streamer details."""
        async with self.get_connection() as conn:
            query = """
            SELECT se.id, se.event_type, se.event_timestamp, se.event_data,
                   se.viewer_count, se.previous_status, se.new_status,
                   s.username as streamer_username, s.kick_user_id, s.status as current_status
            FROM status_event se
            JOIN streamer s ON se.streamer_id = s.id
            ORDER BY se.event_timestamp DESC
            LIMIT $1
            """

            records = await conn.fetch(query, limit)
            return [dict(record) for record in records]

    async def get_streamer_status_grid(self) -> List[Dict[str, Any]]:
        """Get streamers with their current status for dashboard grid."""
        async with self.get_connection() as conn:
            query = """
            SELECT s.id, s.username, s.kick_user_id, s.status, s.last_seen_online,
                   s.profile_picture_url, s.follower_count, s.is_verified,
                   COUNT(usa.user_id) as assigned_users_count
            FROM streamer s
            LEFT JOIN user_streamer_assignments usa ON s.id = usa.streamer_id
            GROUP BY s.id, s.username, s.kick_user_id, s.status, s.last_seen_online,
                     s.profile_picture_url, s.follower_count, s.is_verified
            ORDER BY s.status DESC, s.username ASC
            """

            records = await conn.fetch(query)
            return [dict(record) for record in records]

    async def get_system_health_metrics(self) -> Dict[str, Any]:
        """Get system health and performance metrics."""
        async with self.get_connection() as conn:
            # Get database connection info
            db_query = "SELECT COUNT(*) as active_connections FROM pg_stat_activity WHERE state = 'active'"
            active_connections = await conn.fetchval(db_query)

            # Get latest successful status checks
            latest_check_query = """
            SELECT MAX(event_timestamp) as last_status_change
            FROM status_event
            WHERE event_timestamp >= NOW() - INTERVAL '1 hour'
            """

            last_change = await conn.fetchval(latest_check_query)

            # Check for recent errors (placeholder - would integrate with actual error tracking)
            error_count_query = """
            SELECT COUNT(*) as error_count
            FROM status_event
            WHERE event_timestamp >= NOW() - INTERVAL '1 hour'
            AND event_data::text LIKE '%error%'
            """

            error_count = await conn.fetchval(error_count_query)

            return {
                "database_status": "connected",
                "active_db_connections": active_connections,
                "last_status_change": last_change.isoformat() if last_change else None,
                "recent_errors": error_count or 0,
                "system_uptime": "operational",  # Would calculate actual uptime
                "last_health_check": datetime.now(timezone.utc).isoformat(),
                "connections": {
                    "database_connected": True
                }
            }

    async def get_viewer_analytics_summary(self) -> Dict[str, Any]:
        """Get viewer count analytics summary."""
        async with self.get_connection() as conn:
            # Get current total viewers across all live streams
            current_viewers_query = """
            SELECT COALESCE(SUM(current_viewers), 0) as total_current_viewers,
                   COUNT(CASE WHEN current_viewers > 0 THEN 1 END) as streams_with_viewers
            FROM streamer
            WHERE status::text = 'online' AND current_viewers IS NOT NULL
            """

            current_stats = await conn.fetchrow(current_viewers_query)

            # Get peak viewer statistics
            peak_viewers_query = """
            SELECT MAX(viewer_count) as peak_viewers_today,
                   AVG(viewer_count) as avg_viewers_today,
                   COUNT(*) as viewer_events_today
            FROM status_event
            WHERE event_timestamp >= CURRENT_DATE
            AND viewer_count IS NOT NULL
            """

            peak_stats = await conn.fetchrow(peak_viewers_query)

            # Get top streamers by current viewers
            top_streamers_query = """
            SELECT username, current_viewers, peak_viewers
            FROM streamer
            WHERE current_viewers > 0
            ORDER BY current_viewers DESC
            LIMIT 5
            """

            top_streamers = await conn.fetch(top_streamers_query)

            return {
                "summary": {
                    "total_current_viewers": current_stats["total_current_viewers"],
                    "live_streams_with_viewers": current_stats["streams_with_viewers"],
                    "peak_viewers_today": peak_stats["peak_viewers_today"] or 0,
                    "avg_viewers_24h": float(peak_stats["avg_viewers_today"]) if peak_stats["avg_viewers_today"] else 0,
                    "viewer_events_today": peak_stats["viewer_events_today"]
                },
                "top_streamers": [dict(streamer) for streamer in top_streamers],
                "last_updated": datetime.now(timezone.utc).isoformat()
            }

    async def get_streamer_viewer_history(self, streamer_id: int, hours: int = 24) -> List[Dict[str, Any]]:
        """Get viewer count history for a specific streamer."""
        async with self.get_connection() as conn:
            query = """
            SELECT event_timestamp, viewer_count, event_type
            FROM status_event
            WHERE streamer_id = $1
            AND event_timestamp >= NOW() - INTERVAL '%s hours'
            AND viewer_count IS NOT NULL
            ORDER BY event_timestamp ASC
            """ % hours

            records = await conn.fetch(query, streamer_id)
            return [dict(record) for record in records]

    async def update_streamer_viewer_stats(self, streamer_id: int, current_viewers: int,
                                         livestream_id: Optional[int] = None,
                                         channel_id: Optional[int] = None) -> bool:
        """Update streamer's current viewer count and related stats."""
        async with self.transaction() as conn:
            # Get current peak for this stream session
            peak_query = "SELECT peak_viewers FROM streamer WHERE id = $1"
            current_peak = await conn.fetchval(peak_query, streamer_id)

            # Update current viewers and peak if necessary
            update_query = """
            UPDATE streamer
            SET current_viewers = $1,
                peak_viewers = GREATEST(COALESCE(peak_viewers, 0), $1),
                livestream_id = $2,
                channel_id = $3,
                last_status_update = NOW()
            WHERE id = $4
            """

            result = await conn.execute(update_query, current_viewers, livestream_id, channel_id, streamer_id)
            return result == "UPDATE 1"

    async def get_viewer_trends(self, days: int = 7) -> List[Dict[str, Any]]:
        """Get viewer count trends over time."""
        async with self.get_connection() as conn:
            query = """
            SELECT DATE(event_timestamp) as date,
                   MAX(viewer_count) as peak_viewers,
                   AVG(viewer_count) as avg_viewers,
                   COUNT(DISTINCT streamer_id) as active_streamers
            FROM status_event
            WHERE event_timestamp >= CURRENT_DATE - INTERVAL '%s days'
            AND viewer_count IS NOT NULL
            GROUP BY DATE(event_timestamp)
            ORDER BY date ASC
            """ % days

            records = await conn.fetch(query)
            return [dict(record) for record in records]

    async def get_worker_analytics_summary(self, hours: int = 24) -> Dict[str, Any]:
        """
        Get worker analytics summary for the last N hours.

        Args:
            hours: Number of hours to look back

        Returns:
            Summary statistics including totals, averages, and trends
        """
        try:
            async with self.get_connection() as conn:
                # Get summary statistics
                summary_query = """
                SELECT
                    COUNT(*) as total_records,
                    COUNT(DISTINCT streamer_id) as unique_streamers,
                    SUM(current_viewers) as total_current_viewers,
                    SUM(assigned_viewers) as total_assigned_viewers,
                    SUM(humans) as total_humans,
                    AVG(current_viewers) as avg_current_viewers,
                    AVG(assigned_viewers) as avg_assigned_viewers,
                    AVG(humans) as avg_humans,
                    MIN(timestamp) as earliest_record,
                    MAX(timestamp) as latest_record
                FROM worker_analytics
                WHERE timestamp >= NOW() - INTERVAL '%s hours'
                """ % hours

                summary = await conn.fetchrow(summary_query)

                # Get top streamers by assigned viewers
                top_assigned_query = """
                SELECT
                    s.username,
                    s.display_name,
                    AVG(wa.current_viewers) as avg_viewers,
                    AVG(wa.assigned_viewers) as avg_assigned,
                    AVG(wa.humans) as avg_humans,
                    COUNT(*) as data_points
                FROM worker_analytics wa
                JOIN streamer s ON wa.streamer_id = s.id
                WHERE wa.timestamp >= NOW() - INTERVAL '%s hours'
                GROUP BY s.id, s.username, s.display_name
                HAVING AVG(wa.assigned_viewers) > 0
                ORDER BY AVG(wa.assigned_viewers) DESC
                LIMIT 10
                """ % hours

                top_assigned = await conn.fetch(top_assigned_query)

                # Get hourly trends
                trends_query = """
                SELECT
                    DATE_TRUNC('hour', timestamp) as hour,
                    SUM(current_viewers) as total_viewers,
                    SUM(assigned_viewers) as total_assigned,
                    SUM(humans) as total_humans,
                    COUNT(DISTINCT streamer_id) as active_streamers
                FROM worker_analytics
                WHERE timestamp >= NOW() - INTERVAL '%s hours'
                GROUP BY DATE_TRUNC('hour', timestamp)
                ORDER BY hour DESC
                """ % hours

                trends = await conn.fetch(trends_query)

                return {
                    'summary': dict(summary) if summary else {},
                    'top_assigned_streamers': [dict(row) for row in top_assigned],
                    'hourly_trends': [dict(row) for row in trends],
                    'period_hours': hours,
                    'generated_at': datetime.now(timezone.utc)
                }

        except Exception as e:
            logger.error(f"Error getting worker analytics summary: {e}")
            return {
                'summary': {},
                'top_assigned_streamers': [],
                'hourly_trends': [],
                'period_hours': hours,
                'generated_at': datetime.now(timezone.utc),
                'error': str(e)
            }

    async def get_streamer_worker_analytics(self, streamer_id: int, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Get worker analytics data for a specific streamer.

        Args:
            streamer_id: Streamer ID
            hours: Number of hours to look back

        Returns:
            List of analytics records with timestamps
        """
        try:
            async with self.get_connection() as conn:
                query = """
                SELECT
                    timestamp,
                    current_viewers,
                    assigned_viewers,
                    humans,
                    logged_at
                FROM worker_analytics
                WHERE streamer_id = $1
                AND timestamp >= NOW() - INTERVAL '%s hours'
                ORDER BY timestamp DESC
                """ % hours

                records = await conn.fetch(query, streamer_id)
                return [dict(record) for record in records]

        except Exception as e:
            logger.error(f"Error getting streamer worker analytics for {streamer_id}: {e}")
            return []