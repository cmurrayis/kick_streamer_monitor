#!/usr/bin/env python3
"""
Database migration script to add analytics tables for 1-minute interval data collection.

Adds tables for analytics data storage:
- streamer_analytics: 1-minute interval snapshots
- stream_sessions: Complete streaming session tracking

This migration is safe to run multiple times (uses IF NOT EXISTS).
"""

import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

# Add src to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

load_dotenv()

from services.database import DatabaseService, DatabaseConfig

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_migration():
    """Apply database schema changes for analytics tables."""

    # Initialize database service
    db_config = DatabaseConfig(
        host=os.getenv('DATABASE_HOST', 'localhost'),
        port=int(os.getenv('DATABASE_PORT', 5432)),
        database=os.getenv('DATABASE_NAME', 'kick_mon'),
        username=os.getenv('DATABASE_USER', 'postgres'),
        password=os.getenv('DATABASE_PASSWORD', '')
    )

    db_service = DatabaseService(db_config)

    try:
        await db_service.connect()
        logger.info("Connected to database")

        async with db_service.transaction() as conn:
            # Create streamer_analytics table
            logger.info("Creating streamer_analytics table...")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS streamer_analytics (
                    -- Primary key and relationships
                    id                   SERIAL PRIMARY KEY,
                    streamer_id          INTEGER NOT NULL REFERENCES streamer(id) ON DELETE CASCADE,
                    recorded_at          TIMESTAMP WITH TIME ZONE NOT NULL,

                    -- Core metrics (1-minute snapshot)
                    viewers              INTEGER NOT NULL CHECK (viewers >= 0),
                    running              BOOLEAN NOT NULL DEFAULT FALSE,
                    assigned             INTEGER NOT NULL DEFAULT 0 CHECK (assigned >= 0),

                    -- Status snapshot
                    status               VARCHAR(20) NOT NULL CHECK (status IN ('online', 'offline')),

                    -- Metadata
                    created_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

                    -- Prevent duplicate entries for same minute
                    UNIQUE(streamer_id, recorded_at)
                )
            """)

            # Create stream_sessions table
            logger.info("Creating stream_sessions table...")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS stream_sessions (
                    -- Primary key and relationships
                    id                   SERIAL PRIMARY KEY,
                    streamer_id          INTEGER NOT NULL REFERENCES streamer(id) ON DELETE CASCADE,

                    -- Session tracking
                    session_start        TIMESTAMP WITH TIME ZONE NOT NULL,
                    session_end          TIMESTAMP WITH TIME ZONE,

                    -- Analytics during session
                    peak_viewers         INTEGER CHECK (peak_viewers >= 0),
                    avg_viewers          INTEGER CHECK (avg_viewers >= 0),
                    total_minutes        INTEGER CHECK (total_minutes >= 0),

                    -- Session metadata
                    kick_livestream_id   INTEGER,
                    created_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create analytics indexes for performance
            logger.info("Creating analytics indexes...")

            # Streamer analytics indexes
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_streamer_analytics_streamer_recorded
                ON streamer_analytics(streamer_id, recorded_at DESC)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_streamer_analytics_recorded_at
                ON streamer_analytics(recorded_at DESC)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_streamer_analytics_status_viewers
                ON streamer_analytics(status, viewers DESC) WHERE status = 'online'
            """)

            # Stream sessions indexes
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_stream_sessions_streamer_start
                ON stream_sessions(streamer_id, session_start DESC)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_stream_sessions_active
                ON stream_sessions(streamer_id, session_start) WHERE session_end IS NULL
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_stream_sessions_livestream_id
                ON stream_sessions(kick_livestream_id) WHERE kick_livestream_id IS NOT NULL
            """)

            # Add trigger for automatic timestamp updates (skip if exists)
            logger.info("Creating triggers...")
            await conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_trigger
                        WHERE tgname = 'update_stream_sessions_updated_at'
                    ) THEN
                        CREATE TRIGGER update_stream_sessions_updated_at
                            BEFORE UPDATE ON stream_sessions
                            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
                    END IF;
                END
                $$
            """)

            # Update schema version
            logger.info("Updating schema version...")
            await conn.execute("""
                INSERT INTO configuration (key, value, description, category, updated_by)
                VALUES ('SCHEMA_VERSION', '1.1.0', 'Analytics tables added', 'system', 'migration')
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = CURRENT_TIMESTAMP
            """)

            logger.info("Analytics tables migration completed successfully!")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise

    finally:
        await db_service.disconnect()
        logger.info("Database connection closed")

async def verify_migration():
    """Verify that the analytics tables were created successfully."""

    db_config = DatabaseConfig(
        host=os.getenv('DATABASE_HOST', 'localhost'),
        port=int(os.getenv('DATABASE_PORT', 5432)),
        database=os.getenv('DATABASE_NAME', 'kick_mon'),
        username=os.getenv('DATABASE_USER', 'postgres'),
        password=os.getenv('DATABASE_PASSWORD', '')
    )

    db_service = DatabaseService(db_config)

    try:
        await db_service.connect()

        async with db_service.transaction() as conn:
            # Check if tables exist
            result = await conn.fetch("""
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename IN ('streamer_analytics', 'stream_sessions')
                ORDER BY tablename
            """)

            tables = [row['tablename'] for row in result]

            if 'streamer_analytics' in tables and 'stream_sessions' in tables:
                logger.info("Analytics tables verified successfully")

                # Check schema version
                version_result = await conn.fetchrow("""
                    SELECT value FROM configuration WHERE key = 'SCHEMA_VERSION'
                """)

                if version_result:
                    logger.info(f"Schema version: {version_result['value']}")

                return True
            else:
                logger.error(f"Missing tables. Found: {tables}")
                return False

    except Exception as e:
        logger.error(f"Verification failed: {e}")
        return False
    finally:
        await db_service.disconnect()

async def main():
    """Main migration function."""
    print("Kick Streamer Monitor - Analytics Tables Migration")
    print("=" * 55)
    print()

    try:
        await run_migration()
        print()

        # Verify the migration
        success = await verify_migration()

        if success:
            print("Migration completed successfully!")
            print()
            print("New analytics features enabled:")
            print("- 1-minute interval data collection in streamer_analytics")
            print("- Complete stream session tracking in stream_sessions")
            print("- Performance indexes for analytics queries")
            print("- Automatic timestamp triggers")
            print()
            print("Tables added:")
            print("- streamer_analytics: stores viewer counts, running status every minute")
            print("- stream_sessions: tracks complete streaming sessions with peak/avg viewers")
        else:
            print("Migration verification failed!")
            sys.exit(1)

    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())