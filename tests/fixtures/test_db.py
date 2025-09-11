"""
Test database setup scripts and utilities.

Provides database fixtures, test data creation, schema management, and cleanup
utilities for integration and end-to-end testing with PostgreSQL.
"""

import asyncio
import logging
import os
import random
import string
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, AsyncGenerator, Tuple
import pytest
import asyncpg
from asyncpg import Connection, Pool
import psycopg

from src.services.database import DatabaseService, DatabaseConfig
from src.models.streamer import Streamer, StreamerStatus, StreamerCreate
from src.models.status_event import StatusEvent, StatusEventCreate, EventType
from src.models.configuration import Configuration, ConfigurationCreate, ConfigCategory, ConfigurationType

logger = logging.getLogger(__name__)


class DatabaseTestConfig:
    """Configuration for test database connections."""
    
    def __init__(self, 
                 host: str = None,
                 port: int = None,
                 database: str = None,
                 username: str = None,
                 password: str = None,
                 use_existing: bool = False):
        
        # Use environment variables or defaults
        self.host = host or os.getenv('TEST_DATABASE_HOST', 'localhost')
        self.port = port or int(os.getenv('TEST_DATABASE_PORT', '5432'))
        self.database = database or os.getenv('TEST_DATABASE_NAME', 'kick_monitor_test')
        self.username = username or os.getenv('TEST_DATABASE_USER', 'kick_monitor_test')
        self.password = password or os.getenv('TEST_DATABASE_PASSWORD', 'test_password')
        self.use_existing = use_existing or os.getenv('TEST_USE_EXISTING_DB', 'false').lower() == 'true'
        
        # Generate unique database name for isolation if not using existing
        if not self.use_existing:
            unique_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
            self.database = f"{self.database}_{unique_suffix}"
    
    def get_connection_url(self, database: str = None) -> str:
        """Get database connection URL."""
        db_name = database or self.database
        return f"postgresql://{self.username}:{self.password}@{self.host}:{self.port}/{db_name}"
    
    def get_admin_connection_url(self) -> str:
        """Get connection URL for admin operations (using postgres database)."""
        return f"postgresql://{self.username}:{self.password}@{self.host}:{self.port}/postgres"
    
    def to_database_config(self) -> DatabaseConfig:
        """Convert to DatabaseConfig object."""
        return DatabaseConfig(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
            min_connections=1,
            max_connections=5,
            command_timeout=30.0
        )


class DatabaseSchemaManager:
    """Manages database schema creation and cleanup for tests."""
    
    def __init__(self, config: DatabaseTestConfig):
        self.config = config
        self.schema_sql_path = Path(__file__).parent.parent.parent / "src" / "models" / "schema.sql"
    
    async def create_database(self) -> None:
        """Create test database."""
        if self.config.use_existing:
            logger.info(f"Using existing database: {self.config.database}")
            return
        
        # Connect to admin database to create test database
        conn = await asyncpg.connect(self.config.get_admin_connection_url())
        try:
            # Check if database exists
            result = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1",
                self.config.database
            )
            
            if not result:
                await conn.execute(f'CREATE DATABASE "{self.config.database}"')
                logger.info(f"Created test database: {self.config.database}")
            else:
                logger.info(f"Test database already exists: {self.config.database}")
                
        except Exception as e:
            logger.error(f"Failed to create database {self.config.database}: {e}")
            raise
        finally:
            await conn.close()
    
    async def drop_database(self) -> None:
        """Drop test database."""
        if self.config.use_existing:
            logger.info(f"Skipping database drop (using existing): {self.config.database}")
            return
        
        # Connect to admin database to drop test database
        conn = await asyncpg.connect(self.config.get_admin_connection_url())
        try:
            # Terminate active connections to the test database
            await conn.execute("""
                SELECT pg_terminate_backend(pg_stat_activity.pid)
                FROM pg_stat_activity
                WHERE pg_stat_activity.datname = $1
                AND pid <> pg_backend_pid()
            """, self.config.database)
            
            # Drop database
            await conn.execute(f'DROP DATABASE IF EXISTS "{self.config.database}"')
            logger.info(f"Dropped test database: {self.config.database}")
            
        except Exception as e:
            logger.error(f"Failed to drop database {self.config.database}: {e}")
            # Don't raise exception for cleanup operations
        finally:
            await conn.close()
    
    async def create_schema(self) -> None:
        """Create database schema tables."""
        if self.schema_sql_path.exists():
            schema_sql = self.schema_sql_path.read_text()
            await self._execute_schema_sql(schema_sql)
        else:
            # Fallback to embedded schema if file doesn't exist
            await self._create_schema_tables()
        
        logger.info("Database schema created successfully")
    
    async def _execute_schema_sql(self, sql: str) -> None:
        """Execute schema SQL file."""
        conn = await asyncpg.connect(self.config.get_connection_url())
        try:
            await conn.execute(sql)
        finally:
            await conn.close()
    
    async def _create_schema_tables(self) -> None:
        """Create schema tables using embedded SQL."""
        schema_sql = """
        -- Streamers table
        CREATE TABLE IF NOT EXISTS streamers (
            id SERIAL PRIMARY KEY,
            kick_user_id VARCHAR(255) NOT NULL UNIQUE,
            username VARCHAR(255) NOT NULL UNIQUE,
            display_name VARCHAR(255),
            status VARCHAR(50) NOT NULL DEFAULT 'unknown',
            last_seen_online TIMESTAMP WITH TIME ZONE,
            last_status_update TIMESTAMP WITH TIME ZONE,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        
        -- Status events table
        CREATE TABLE IF NOT EXISTS status_events (
            id SERIAL PRIMARY KEY,
            streamer_id INTEGER NOT NULL REFERENCES streamers(id) ON DELETE CASCADE,
            event_type VARCHAR(100) NOT NULL,
            timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            details JSONB,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        
        -- Configuration table
        CREATE TABLE IF NOT EXISTS configurations (
            id SERIAL PRIMARY KEY,
            key VARCHAR(255) NOT NULL UNIQUE,
            value TEXT NOT NULL,
            category VARCHAR(100) NOT NULL,
            type VARCHAR(50) NOT NULL,
            description TEXT,
            is_encrypted BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        
        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_streamers_username ON streamers(username);
        CREATE INDEX IF NOT EXISTS idx_streamers_kick_user_id ON streamers(kick_user_id);
        CREATE INDEX IF NOT EXISTS idx_streamers_status ON streamers(status);
        CREATE INDEX IF NOT EXISTS idx_streamers_is_active ON streamers(is_active);
        CREATE INDEX IF NOT EXISTS idx_streamers_last_status_update ON streamers(last_status_update);
        
        CREATE INDEX IF NOT EXISTS idx_status_events_streamer_id ON status_events(streamer_id);
        CREATE INDEX IF NOT EXISTS idx_status_events_timestamp ON status_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_status_events_event_type ON status_events(event_type);
        
        CREATE INDEX IF NOT EXISTS idx_configurations_key ON configurations(key);
        CREATE INDEX IF NOT EXISTS idx_configurations_category ON configurations(category);
        
        -- Triggers for updated_at timestamps
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
        
        DROP TRIGGER IF EXISTS update_streamers_updated_at ON streamers;
        CREATE TRIGGER update_streamers_updated_at BEFORE UPDATE ON streamers
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        
        DROP TRIGGER IF EXISTS update_configurations_updated_at ON configurations;
        CREATE TRIGGER update_configurations_updated_at BEFORE UPDATE ON configurations
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        """
        
        await self._execute_schema_sql(schema_sql)
    
    async def clear_data(self) -> None:
        """Clear all data from test database tables."""
        conn = await asyncpg.connect(self.config.get_connection_url())
        try:
            # Clear in dependency order
            await conn.execute("DELETE FROM status_events")
            await conn.execute("DELETE FROM streamers")
            await conn.execute("DELETE FROM configurations")
            
            # Reset sequences
            await conn.execute("SELECT setval('streamers_id_seq', 1, false)")
            await conn.execute("SELECT setval('status_events_id_seq', 1, false)")
            await conn.execute("SELECT setval('configurations_id_seq', 1, false)")
            
            logger.info("Test database data cleared")
        finally:
            await conn.close()


class TestDataFactory:
    """Factory for creating test data objects."""
    
    @staticmethod
    def create_streamer(
        username: str = None,
        kick_user_id: str = None,
        status: StreamerStatus = StreamerStatus.UNKNOWN,
        display_name: str = None,
        is_active: bool = True
    ) -> StreamerCreate:
        """Create test streamer data."""
        if not username:
            username = f"testuser_{uuid.uuid4().hex[:8]}"
        
        if not kick_user_id:
            kick_user_id = str(random.randint(10000, 99999))
        
        if not display_name:
            display_name = f"Test User {username.split('_')[-1]}"
        
        return StreamerCreate(
            kick_user_id=kick_user_id,
            username=username,
            display_name=display_name,
            status=status,
            is_active=is_active
        )
    
    @staticmethod
    def create_status_event(
        streamer_id: int,
        event_type: EventType = EventType.STREAM_START,
        timestamp: datetime = None,
        details: Dict[str, Any] = None
    ) -> StatusEventCreate:
        """Create test status event data."""
        if not timestamp:
            timestamp = datetime.now(timezone.utc)
        
        if not details:
            details = {
                "test_event": True,
                "generated_at": timestamp.isoformat()
            }
        
        return StatusEventCreate(
            streamer_id=streamer_id,
            event_type=event_type,
            timestamp=timestamp,
            details=details
        )
    
    @staticmethod
    def create_configuration(
        key: str = None,
        value: str = "test_value",
        category: ConfigCategory = ConfigCategory.SYSTEM,
        config_type: ConfigurationType = ConfigurationType.STRING,
        description: str = "Test configuration"
    ) -> ConfigurationCreate:
        """Create test configuration data."""
        if not key:
            key = f"TEST_CONFIG_{uuid.uuid4().hex[:8].upper()}"
        
        return ConfigurationCreate(
            key=key,
            value=value,
            category=category,
            type=config_type,
            description=description
        )
    
    @staticmethod
    def create_multiple_streamers(count: int = 5) -> List[StreamerCreate]:
        """Create multiple test streamers."""
        streamers = []
        for i in range(count):
            streamer = TestDataFactory.create_streamer(
                username=f"teststreamer_{i+1}",
                kick_user_id=str(10000 + i),
                status=random.choice(list(StreamerStatus)),
                display_name=f"Test Streamer {i+1}"
            )
            streamers.append(streamer)
        return streamers
    
    @staticmethod
    def create_streamer_lifecycle_events(streamer_id: int, 
                                         session_count: int = 3) -> List[StatusEventCreate]:
        """Create a realistic sequence of streamer lifecycle events."""
        events = []
        base_time = datetime.now(timezone.utc) - timedelta(days=1)
        
        for session in range(session_count):
            # Stream start
            start_time = base_time + timedelta(hours=session * 8)
            events.append(StatusEventCreate(
                streamer_id=streamer_id,
                event_type=EventType.STREAM_START,
                timestamp=start_time,
                details={
                    "session_id": f"session_{session+1}",
                    "title": f"Test Stream Session {session+1}",
                    "category": "Gaming"
                }
            ))
            
            # Some viewer count updates during stream
            for update in range(3):
                update_time = start_time + timedelta(minutes=30 + update * 30)
                events.append(StatusEventCreate(
                    streamer_id=streamer_id,
                    event_type=EventType.VIEWER_COUNT_UPDATE,
                    timestamp=update_time,
                    details={
                        "session_id": f"session_{session+1}",
                        "viewer_count": random.randint(50, 500)
                    }
                ))
            
            # Stream end
            end_time = start_time + timedelta(hours=2, minutes=30)
            events.append(StatusEventCreate(
                streamer_id=streamer_id,
                event_type=EventType.STREAM_END,
                timestamp=end_time,
                details={
                    "session_id": f"session_{session+1}",
                    "duration_minutes": 150,
                    "final_viewer_count": random.randint(30, 200)
                }
            ))
        
        return events


class DatabaseTestHelper:
    """Helper class for database testing operations."""
    
    def __init__(self, config: DatabaseTestConfig):
        self.config = config
        self.schema_manager = DatabaseSchemaManager(config)
        self.data_factory = TestDataFactory()
    
    async def setup_database(self) -> None:
        """Complete database setup for testing."""
        await self.schema_manager.create_database()
        await self.schema_manager.create_schema()
    
    async def cleanup_database(self) -> None:
        """Complete database cleanup after testing."""
        await self.schema_manager.drop_database()
    
    async def reset_data(self) -> None:
        """Reset test data (clear all tables)."""
        await self.schema_manager.clear_data()
    
    @asynccontextmanager
    async def database_connection(self) -> AsyncGenerator[Connection, None]:
        """Context manager for database connections."""
        conn = await asyncpg.connect(self.config.get_connection_url())
        try:
            yield conn
        finally:
            await conn.close()
    
    @asynccontextmanager
    async def database_service(self) -> AsyncGenerator[DatabaseService, None]:
        """Context manager for DatabaseService instances."""
        service = DatabaseService(self.config.to_database_config())
        try:
            await service.connect()
            yield service
        finally:
            await service.disconnect()
    
    async def create_test_streamers(self, count: int = 5) -> List[int]:
        """Create test streamers and return their IDs."""
        streamers = self.data_factory.create_multiple_streamers(count)
        streamer_ids = []
        
        async with self.database_service() as db:
            for streamer in streamers:
                saved_streamer = await db.create_streamer(streamer)
                streamer_ids.append(saved_streamer.id)
        
        return streamer_ids
    
    async def create_test_events(self, streamer_id: int, count: int = 10) -> List[int]:
        """Create test events for a streamer and return their IDs."""
        events = self.data_factory.create_streamer_lifecycle_events(streamer_id, count // 4)
        event_ids = []
        
        async with self.database_service() as db:
            for event in events:
                saved_event = await db.create_status_event(event)
                event_ids.append(saved_event.id)
        
        return event_ids
    
    async def verify_database_state(self) -> Dict[str, int]:
        """Verify database state and return counts."""
        async with self.database_connection() as conn:
            streamer_count = await conn.fetchval("SELECT COUNT(*) FROM streamers")
            event_count = await conn.fetchval("SELECT COUNT(*) FROM status_events")
            config_count = await conn.fetchval("SELECT COUNT(*) FROM configurations")
            
            return {
                "streamers": streamer_count,
                "events": event_count,
                "configurations": config_count
            }
    
    async def get_test_connection_info(self) -> Dict[str, Any]:
        """Get connection information for tests."""
        async with self.database_connection() as conn:
            version = await conn.fetchval("SELECT version()")
            db_size = await conn.fetchval(
                "SELECT pg_size_pretty(pg_database_size($1))",
                self.config.database
            )
            
            return {
                "database": self.config.database,
                "version": version,
                "size": db_size,
                "connection_url": self.config.get_connection_url()
            }


# Pytest fixtures
@pytest.fixture(scope="session")
def database_test_config():
    """Session-scoped database test configuration."""
    return DatabaseTestConfig()


@pytest.fixture(scope="session")
async def database_test_helper(database_test_config):
    """Session-scoped database test helper."""
    helper = DatabaseTestHelper(database_test_config)
    
    # Setup
    await helper.setup_database()
    
    try:
        yield helper
    finally:
        # Cleanup
        await helper.cleanup_database()


@pytest.fixture
async def clean_database(database_test_helper):
    """Function-scoped clean database state."""
    await database_test_helper.reset_data()
    yield database_test_helper
    # Optional: cleanup after test if needed


@pytest.fixture
def test_data_factory():
    """Test data factory fixture."""
    return TestDataFactory()


@pytest.fixture
async def database_connection(database_test_helper):
    """Database connection fixture."""
    async with database_test_helper.database_connection() as conn:
        yield conn


@pytest.fixture
async def database_service(database_test_helper):
    """Database service fixture."""
    async with database_test_helper.database_service() as service:
        yield service


@pytest.fixture
async def test_streamers(clean_database):
    """Fixture that creates test streamers."""
    streamer_ids = await clean_database.create_test_streamers(3)
    return streamer_ids


@pytest.fixture
async def test_streamer_with_events(clean_database):
    """Fixture that creates a test streamer with events."""
    streamer_ids = await clean_database.create_test_streamers(1)
    streamer_id = streamer_ids[0]
    event_ids = await clean_database.create_test_events(streamer_id, 8)
    
    return {
        "streamer_id": streamer_id,
        "event_ids": event_ids
    }


# Environment validation
def validate_test_database_environment() -> bool:
    """Validate that test database environment is properly configured."""
    required_vars = ['TEST_DATABASE_HOST', 'TEST_DATABASE_USER', 'TEST_DATABASE_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.warning(f"Missing test database environment variables: {missing_vars}")
        return False
    
    return True


def skip_if_no_database():
    """Pytest marker to skip tests if database is not available."""
    return pytest.mark.skipif(
        not validate_test_database_environment(),
        reason="Test database environment not configured"
    )


# Export commonly used utilities
__all__ = [
    'DatabaseTestConfig',
    'DatabaseSchemaManager', 
    'TestDataFactory',
    'DatabaseTestHelper',
    'validate_test_database_environment',
    'skip_if_no_database'
]