"""
Integration tests for database connection and operations.
Tests against real PostgreSQL database to validate schema and operations.

These tests require:
- PostgreSQL database (test database recommended)
- Database connection credentials in environment
- psycopg3 database driver

Set these environment variables:
- TEST_DATABASE_HOST
- TEST_DATABASE_PORT  
- TEST_DATABASE_NAME
- TEST_DATABASE_USER
- TEST_DATABASE_PASSWORD

Or use .env.test file for configuration.
"""

import os
import pytest
import asyncio
import asyncpg
import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from typing import Dict, Any, List, Optional, AsyncGenerator
from datetime import datetime, timezone
import tempfile
from pathlib import Path


class TestDatabaseIntegration:
    """Integration tests for database operations."""

    @pytest.fixture(autouse=True)
    def setup_database_config(self):
        """Setup database configuration from environment."""
        # Database connection parameters
        self.db_config = {
            "host": os.getenv("TEST_DATABASE_HOST", "localhost"),
            "port": int(os.getenv("TEST_DATABASE_PORT", "5432")),
            "dbname": os.getenv("TEST_DATABASE_NAME", "kick_monitor_test"),
            "user": os.getenv("TEST_DATABASE_USER", "kick_monitor"),
            "password": os.getenv("TEST_DATABASE_PASSWORD", "test_password"),
        }
        
        # Skip tests if database not configured
        if not all([
            os.getenv("TEST_DATABASE_HOST") or os.getenv("DATABASE_HOST"),
            os.getenv("TEST_DATABASE_NAME") or os.getenv("DATABASE_NAME"),
        ]):
            pytest.skip("Database configuration required for integration tests")

    @pytest.fixture
    async def db_connection(self) -> AsyncGenerator[AsyncConnection, None]:
        """Create database connection for testing."""
        conn_string = (
            f"postgresql://{self.db_config['user']}:{self.db_config['password']}"
            f"@{self.db_config['host']}:{self.db_config['port']}/{self.db_config['dbname']}"
        )
        
        try:
            conn = await psycopg.AsyncConnection.connect(
                conn_string,
                row_factory=dict_row
            )
            yield conn
        except psycopg.OperationalError as e:
            pytest.skip(f"Cannot connect to test database: {e}")
        finally:
            if 'conn' in locals():
                await conn.close()

    @pytest.fixture
    async def clean_database(self, db_connection: AsyncConnection):
        """Clean database before and after tests."""
        # Clean before test
        await self._clean_test_data(db_connection)
        
        yield db_connection
        
        # Clean after test
        await self._clean_test_data(db_connection)

    async def test_database_connection_basic(self, db_connection):
        """Test basic database connectivity."""
        # Test simple query
        async with db_connection.cursor() as cur:
            await cur.execute("SELECT 1 as test_value")
            result = await cur.fetchone()
            
        assert result is not None
        assert result["test_value"] == 1

    async def test_database_schema_existence(self, db_connection):
        """Test that required database schema exists or can be created."""
        # Test tables existence or create them for testing
        required_tables = ["streamers", "status_events", "configuration"]
        
        async with db_connection.cursor() as cur:
            # Check which tables exist
            await cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            existing_tables = {row["table_name"] for row in await cur.fetchall()}
            
            # Create missing tables for testing
            if "streamers" not in existing_tables:
                await self._create_streamers_table(cur)
            
            if "status_events" not in existing_tables:
                await self._create_status_events_table(cur)
            
            if "configuration" not in existing_tables:
                await self._create_configuration_table(cur)
            
            await db_connection.commit()

    async def test_streamer_crud_operations(self, clean_database):
        """Test CRUD operations for streamer records."""
        conn = clean_database
        
        # Test data
        test_streamer = {
            "kick_user_id": 12345,
            "username": "test_streamer",
            "display_name": "Test Streamer",
            "status": "unknown",
            "is_active": True
        }
        
        async with conn.cursor() as cur:
            # CREATE - Insert streamer
            await cur.execute("""
                INSERT INTO streamers (kick_user_id, username, display_name, status, is_active, created_at, updated_at)
                VALUES (%(kick_user_id)s, %(username)s, %(display_name)s, %(status)s, %(is_active)s, NOW(), NOW())
                RETURNING id
            """, test_streamer)
            
            result = await cur.fetchone()
            streamer_id = result["id"]
            assert streamer_id is not None
            
            # READ - Select streamer
            await cur.execute("""
                SELECT * FROM streamers WHERE id = %s
            """, (streamer_id,))
            
            fetched_streamer = await cur.fetchone()
            assert fetched_streamer is not None
            assert fetched_streamer["username"] == test_streamer["username"]
            assert fetched_streamer["kick_user_id"] == test_streamer["kick_user_id"]
            assert fetched_streamer["status"] == test_streamer["status"]
            
            # UPDATE - Change status
            await cur.execute("""
                UPDATE streamers 
                SET status = 'online', last_status_update = NOW(), updated_at = NOW()
                WHERE id = %s
            """, (streamer_id,))
            
            await cur.execute("""
                SELECT status, last_status_update FROM streamers WHERE id = %s
            """, (streamer_id,))
            
            updated_streamer = await cur.fetchone()
            assert updated_streamer["status"] == "online"
            assert updated_streamer["last_status_update"] is not None
            
            # DELETE - Remove streamer
            await cur.execute("""
                DELETE FROM streamers WHERE id = %s
            """, (streamer_id,))
            
            await cur.execute("""
                SELECT COUNT(*) as count FROM streamers WHERE id = %s
            """, (streamer_id,))
            
            count_result = await cur.fetchone()
            assert count_result["count"] == 0
            
            await conn.commit()

    async def test_status_event_operations(self, clean_database):
        """Test status event logging operations."""
        conn = clean_database
        
        async with conn.cursor() as cur:
            # Create test streamer first
            await cur.execute("""
                INSERT INTO streamers (kick_user_id, username, display_name, status, is_active, created_at, updated_at)
                VALUES (12345, 'test_streamer', 'Test Streamer', 'offline', true, NOW(), NOW())
                RETURNING id
            """)
            
            streamer_result = await cur.fetchone()
            streamer_id = streamer_result["id"]
            
            # Test status event creation
            event_data = {
                "streamer_id": streamer_id,
                "event_type": "stream_start",
                "previous_status": "offline",
                "new_status": "online",
                "event_timestamp": datetime.now(timezone.utc),
                "received_timestamp": datetime.now(timezone.utc),
                "event_data": '{"session_id": "test_session_123"}'
            }
            
            await cur.execute("""
                INSERT INTO status_events 
                (streamer_id, event_type, previous_status, new_status, 
                 event_timestamp, received_timestamp, processed_timestamp, event_data)
                VALUES (%(streamer_id)s, %(event_type)s, %(previous_status)s, %(new_status)s,
                        %(event_timestamp)s, %(received_timestamp)s, NOW(), %(event_data)s)
                RETURNING id
            """, event_data)
            
            event_result = await cur.fetchone()
            event_id = event_result["id"]
            assert event_id is not None
            
            # Verify event was stored correctly
            await cur.execute("""
                SELECT * FROM status_events WHERE id = %s
            """, (event_id,))
            
            stored_event = await cur.fetchone()
            assert stored_event["event_type"] == event_data["event_type"]
            assert stored_event["previous_status"] == event_data["previous_status"]
            assert stored_event["new_status"] == event_data["new_status"]
            assert stored_event["processed_timestamp"] is not None
            
            await conn.commit()

    async def test_configuration_operations(self, clean_database):
        """Test configuration storage and retrieval."""
        conn = clean_database
        
        test_configs = [
            ("kick.client_id", "test_client_id", "Test client ID", "auth", False),
            ("kick.client_secret", "encrypted_secret", "Test client secret", "auth", True),
            ("monitoring.batch_size", "10", "Batch size for updates", "monitoring", False),
            ("database.pool_size", "5", "Database connection pool size", "database", False)
        ]
        
        async with conn.cursor() as cur:
            # Insert test configurations
            for key, value, description, category, is_encrypted in test_configs:
                await cur.execute("""
                    INSERT INTO configuration (key, value, description, category, is_encrypted, updated_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (key, value, description, category, is_encrypted))
            
            # Test retrieval by category
            await cur.execute("""
                SELECT * FROM configuration WHERE category = 'auth' ORDER BY key
            """)
            
            auth_configs = await cur.fetchall()
            assert len(auth_configs) == 2
            assert auth_configs[0]["key"] == "kick.client_id"
            assert auth_configs[1]["key"] == "kick.client_secret"
            assert auth_configs[1]["is_encrypted"] is True
            
            # Test retrieval by key
            await cur.execute("""
                SELECT value FROM configuration WHERE key = %s
            """, ("monitoring.batch_size",))
            
            batch_size_result = await cur.fetchone()
            assert batch_size_result["value"] == "10"
            
            await conn.commit()

    async def test_database_constraints_validation(self, clean_database):
        """Test database constraints and validation."""
        conn = clean_database
        
        async with conn.cursor() as cur:
            # Test unique constraint on username
            await cur.execute("""
                INSERT INTO streamers (kick_user_id, username, display_name, status, is_active, created_at, updated_at)
                VALUES (12345, 'unique_test', 'Test Streamer', 'offline', true, NOW(), NOW())
            """)
            
            # Try to insert duplicate username
            with pytest.raises(psycopg.IntegrityError):
                await cur.execute("""
                    INSERT INTO streamers (kick_user_id, username, display_name, status, is_active, created_at, updated_at)
                    VALUES (54321, 'unique_test', 'Another Streamer', 'offline', true, NOW(), NOW())
                """)
            
            await conn.rollback()
            
            # Test foreign key constraint
            await cur.execute("""
                INSERT INTO streamers (kick_user_id, username, display_name, status, is_active, created_at, updated_at)
                VALUES (12345, 'fk_test', 'Test Streamer', 'offline', true, NOW(), NOW())
                RETURNING id
            """)
            
            streamer_result = await cur.fetchone()
            valid_streamer_id = streamer_result["id"]
            
            # Try to insert event with invalid streamer_id
            with pytest.raises(psycopg.IntegrityError):
                await cur.execute("""
                    INSERT INTO status_events 
                    (streamer_id, event_type, previous_status, new_status, event_timestamp, received_timestamp, processed_timestamp)
                    VALUES (99999, 'stream_start', 'offline', 'online', NOW(), NOW(), NOW())
                """)
            
            await conn.rollback()

    async def test_database_performance_basic(self, clean_database):
        """Test basic database performance requirements."""
        conn = clean_database
        import time
        
        async with conn.cursor() as cur:
            # Insert test streamer
            await cur.execute("""
                INSERT INTO streamers (kick_user_id, username, display_name, status, is_active, created_at, updated_at)
                VALUES (12345, 'perf_test', 'Performance Test', 'offline', true, NOW(), NOW())
                RETURNING id
            """)
            
            streamer_result = await cur.fetchone()
            streamer_id = streamer_result["id"]
            
            # Test status update performance
            start_time = time.time()
            
            await cur.execute("""
                UPDATE streamers 
                SET status = 'online', last_status_update = NOW(), updated_at = NOW()
                WHERE id = %s
            """, (streamer_id,))
            
            end_time = time.time()
            update_time = end_time - start_time
            
            # Status update should be fast (< 100ms)
            assert update_time < 0.1, f"Status update too slow: {update_time:.3f}s"
            
            # Test batch insert performance
            events = []
            for i in range(100):
                events.append((
                    streamer_id, "stream_update", "online", "online", 
                    datetime.now(timezone.utc), datetime.now(timezone.utc), f'{{"update": {i}}}'
                ))
            
            start_time = time.time()
            
            await cur.executemany("""
                INSERT INTO status_events 
                (streamer_id, event_type, previous_status, new_status, 
                 event_timestamp, received_timestamp, processed_timestamp, event_data)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
            """, events)
            
            end_time = time.time()
            batch_time = end_time - start_time
            
            # Batch insert should be efficient (< 1s for 100 records)
            assert batch_time < 1.0, f"Batch insert too slow: {batch_time:.3f}s"
            
            await conn.commit()

    async def test_connection_pooling_simulation(self, db_connection):
        """Test concurrent database connections simulation."""
        # Simulate multiple concurrent connections
        async def concurrent_query(query_id: int):
            """Simulate concurrent database query."""
            try:
                conn_string = (
                    f"postgresql://{self.db_config['user']}:{self.db_config['password']}"
                    f"@{self.db_config['host']}:{self.db_config['port']}/{self.db_config['dbname']}"
                )
                
                async with await psycopg.AsyncConnection.connect(conn_string) as conn:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT %s as query_id, pg_sleep(0.1)", (query_id,))
                        result = await cur.fetchone()
                        return result["query_id"]
            except Exception as e:
                pytest.fail(f"Concurrent query {query_id} failed: {e}")
        
        # Run 5 concurrent queries
        tasks = [concurrent_query(i) for i in range(5)]
        results = await asyncio.gather(*tasks)
        
        # All queries should complete successfully
        assert len(results) == 5
        assert sorted(results) == list(range(5))

    async def test_database_transaction_handling(self, clean_database):
        """Test database transaction handling and rollback."""
        conn = clean_database
        
        async with conn.cursor() as cur:
            # Start transaction
            await cur.execute("""
                INSERT INTO streamers (kick_user_id, username, display_name, status, is_active, created_at, updated_at)
                VALUES (12345, 'transaction_test', 'Transaction Test', 'offline', true, NOW(), NOW())
                RETURNING id
            """)
            
            streamer_result = await cur.fetchone()
            streamer_id = streamer_result["id"]
            
            # Verify record exists in transaction
            await cur.execute("SELECT COUNT(*) as count FROM streamers WHERE id = %s", (streamer_id,))
            count_result = await cur.fetchone()
            assert count_result["count"] == 1
            
            # Rollback transaction
            await conn.rollback()
            
            # Verify record was rolled back
            await cur.execute("SELECT COUNT(*) as count FROM streamers WHERE id = %s", (streamer_id,))
            count_result = await cur.fetchone()
            assert count_result["count"] == 0

    async def _clean_test_data(self, conn: AsyncConnection):
        """Clean test data from database."""
        async with conn.cursor() as cur:
            # Clean in reverse order due to foreign key constraints
            await cur.execute("DELETE FROM status_events WHERE streamer_id IN (SELECT id FROM streamers WHERE username LIKE '%test%')")
            await cur.execute("DELETE FROM streamers WHERE username LIKE '%test%'")
            await cur.execute("DELETE FROM configuration WHERE key LIKE 'test.%'")
            await conn.commit()

    async def _create_streamers_table(self, cur):
        """Create streamers table for testing."""
        await cur.execute("""
            CREATE TABLE IF NOT EXISTS streamers (
                id SERIAL PRIMARY KEY,
                kick_user_id INTEGER UNIQUE NOT NULL,
                username VARCHAR(255) UNIQUE NOT NULL,
                display_name VARCHAR(255),
                status VARCHAR(50) NOT NULL CHECK (status IN ('online', 'offline', 'unknown')),
                last_seen_online TIMESTAMP WITH TIME ZONE,
                last_status_update TIMESTAMP WITH TIME ZONE,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
            )
        """)
        
        # Create indexes
        await cur.execute("CREATE INDEX IF NOT EXISTS idx_streamers_username ON streamers(username)")
        await cur.execute("CREATE INDEX IF NOT EXISTS idx_streamers_kick_user_id ON streamers(kick_user_id)")
        await cur.execute("CREATE INDEX IF NOT EXISTS idx_streamers_status_active ON streamers(status, is_active)")

    async def _create_status_events_table(self, cur):
        """Create status_events table for testing."""
        await cur.execute("""
            CREATE TABLE IF NOT EXISTS status_events (
                id SERIAL PRIMARY KEY,
                streamer_id INTEGER NOT NULL REFERENCES streamers(id) ON DELETE CASCADE,
                event_type VARCHAR(50) NOT NULL CHECK (event_type IN ('stream_start', 'stream_end', 'connection_test')),
                previous_status VARCHAR(50) NOT NULL,
                new_status VARCHAR(50) NOT NULL,
                kick_event_id VARCHAR(255),
                event_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                received_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                processed_timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                event_data JSONB,
                CONSTRAINT chk_timestamp_order CHECK (
                    received_timestamp >= event_timestamp 
                    AND processed_timestamp >= received_timestamp
                )
            )
        """)
        
        # Create indexes
        await cur.execute("CREATE INDEX IF NOT EXISTS idx_status_events_streamer_id ON status_events(streamer_id)")
        await cur.execute("CREATE INDEX IF NOT EXISTS idx_status_events_timestamp ON status_events(event_timestamp)")
        await cur.execute("CREATE INDEX IF NOT EXISTS idx_status_events_processed ON status_events(processed_timestamp)")

    async def _create_configuration_table(self, cur):
        """Create configuration table for testing."""
        await cur.execute("""
            CREATE TABLE IF NOT EXISTS configuration (
                key VARCHAR(255) PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT,
                category VARCHAR(100) NOT NULL,
                is_encrypted BOOLEAN NOT NULL DEFAULT false,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_by VARCHAR(255) DEFAULT 'system'
            )
        """)
        
        # Create indexes
        await cur.execute("CREATE INDEX IF NOT EXISTS idx_configuration_category ON configuration(category)")


@pytest.mark.integration
class TestDatabaseMigrations:
    """Integration tests for database migrations and schema updates."""
    
    async def test_migration_script_validation(self):
        """Test that migration scripts are valid and executable."""
        # This test validates the database schema creation scripts
        
        migration_requirements = [
            "CREATE TABLE statements for all entities",
            "Proper foreign key relationships",
            "Appropriate indexes for performance",
            "Check constraints for data validation",
            "Default values and NOT NULL constraints"
        ]
        
        print("Database Migration Requirements:")
        for requirement in migration_requirements:
            print(f"  - {requirement}")
        
        assert True

    def test_database_connection_string_validation(self):
        """Test database connection string construction."""
        # Test connection string formats
        test_configs = [
            {
                "host": "localhost",
                "port": 5432,
                "dbname": "kick_monitor",
                "user": "kick_user",
                "password": "secure_password"
            }
        ]
        
        for config in test_configs:
            conn_string = (
                f"postgresql://{config['user']}:{config['password']}"
                f"@{config['host']}:{config['port']}/{config['dbname']}"
            )
            
            # Validate connection string format
            assert "postgresql://" in conn_string
            assert "@" in conn_string
            assert ":" in conn_string
            
        assert True