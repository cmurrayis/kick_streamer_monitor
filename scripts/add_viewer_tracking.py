#!/usr/bin/env python3
"""
Database migration script to add viewer count tracking fields.

Adds columns to support viewer count analytics:
- status_event.viewer_count
- streamer.current_viewers
- streamer.peak_viewers
- streamer.avg_viewers
- streamer.livestream_id
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
    """Apply database schema changes for viewer tracking."""

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
            # Add viewer_count column to status_event table
            logger.info("Adding viewer_count column to status_event table...")
            await conn.execute("""
                ALTER TABLE status_event
                ADD COLUMN IF NOT EXISTS viewer_count INTEGER CHECK (viewer_count >= 0)
            """)

            # Add viewer tracking columns to streamer table
            logger.info("Adding viewer tracking columns to streamer table...")
            await conn.execute("""
                ALTER TABLE streamer
                ADD COLUMN IF NOT EXISTS current_viewers INTEGER CHECK (current_viewers >= 0),
                ADD COLUMN IF NOT EXISTS peak_viewers INTEGER CHECK (peak_viewers >= 0),
                ADD COLUMN IF NOT EXISTS avg_viewers INTEGER CHECK (avg_viewers >= 0),
                ADD COLUMN IF NOT EXISTS livestream_id INTEGER CHECK (livestream_id > 0)
            """)

            # Create index on viewer_count for analytics queries
            logger.info("Creating indexes for viewer count analytics...")
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status_event_viewer_count
                ON status_event(viewer_count)
                WHERE viewer_count IS NOT NULL
            """)

            # Create index on current_viewers for dashboard queries
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_streamer_current_viewers
                ON streamer(current_viewers)
                WHERE current_viewers IS NOT NULL
            """)

            # Create index on livestream_id for API lookups
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_streamer_livestream_id
                ON streamer(livestream_id)
                WHERE livestream_id IS NOT NULL
            """)

            logger.info("Viewer tracking migration completed successfully!")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise

    finally:
        await db_service.disconnect()
        logger.info("Database connection closed")

async def main():
    """Main migration function."""
    print("Kick Streamer Monitor - Viewer Tracking Migration")
    print("=" * 55)

    try:
        await run_migration()
        print("\\n✓ Migration completed successfully!")
        print("\\nNew features enabled:")
        print("- Viewer count tracking in status events")
        print("- Current viewer display for live streams")
        print("- Peak and average viewer analytics")
        print("- Livestream ID tracking for API calls")

    except Exception as e:
        print(f"\\n✗ Migration failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())