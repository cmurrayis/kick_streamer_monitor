#!/usr/bin/env python3
"""
Add missing streamer columns to existing database.

This migration adds columns that will be useful for future features:
- profile_picture_url: URL to streamer's profile picture
- bio: Streamer's bio/description
- follower_count: Number of followers
- is_live: Current live status (redundant with status but useful for quick queries)
- is_verified: Whether the streamer is verified on Kick
"""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️  dotenv not available - using environment variables directly")

from services.database import DatabaseService, DatabaseConfig

def get_database_config():
    """Get database configuration from environment or defaults."""
    return DatabaseConfig(
        host=os.getenv('DATABASE_HOST', 'localhost'),
        port=int(os.getenv('DATABASE_PORT', '5432')),
        database=os.getenv('DATABASE_NAME', 'kick_monitor'),
        username=os.getenv('DATABASE_USER', 'kick_monitor'),
        password=os.getenv('DATABASE_PASSWORD', ''),
        min_connections=1,
        max_connections=5
    )

async def add_streamer_columns():
    """Add missing columns to streamer table."""
    try:
        config = get_database_config()
        db_service = DatabaseService(config)
        await db_service.connect()
        
        print("🔧 Adding missing columns to streamer table...")
        
        # Add columns with proper defaults
        migrations = [
            "ALTER TABLE streamer ADD COLUMN IF NOT EXISTS profile_picture_url TEXT",
            "ALTER TABLE streamer ADD COLUMN IF NOT EXISTS bio TEXT",
            "ALTER TABLE streamer ADD COLUMN IF NOT EXISTS follower_count INTEGER DEFAULT 0",
            "ALTER TABLE streamer ADD COLUMN IF NOT EXISTS is_live BOOLEAN DEFAULT FALSE", 
            "ALTER TABLE streamer ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE"
        ]
        
        for migration in migrations:
            await db_service.pool.execute(migration)
            print(f"✅ Executed: {migration}")
        
        # Create index for performance on commonly queried columns
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_streamer_is_live ON streamer(is_live)",
            "CREATE INDEX IF NOT EXISTS idx_streamer_is_verified ON streamer(is_verified)",
            "CREATE INDEX IF NOT EXISTS idx_streamer_follower_count ON streamer(follower_count)"
        ]
        
        print("🔧 Creating performance indexes...")
        for index in indexes:
            await db_service.pool.execute(index)
            print(f"✅ Created: {index}")
        
        # Update schema version
        await db_service.pool.execute("""
            INSERT INTO configuration (key, value, description, category, updated_by) VALUES
                ('SCHEMA_VERSION', '1.1.0', 'Database schema version with extended streamer columns', 'system', 'migration_script')
            ON CONFLICT (key) DO UPDATE SET 
                value = EXCLUDED.value,
                updated_at = CURRENT_TIMESTAMP
        """)
        
        print("✅ Schema migration completed successfully!")
        print("📝 Added columns:")
        print("   - profile_picture_url (TEXT) - URL to streamer's profile picture")
        print("   - bio (TEXT) - Streamer's bio/description")
        print("   - follower_count (INTEGER) - Number of followers")
        print("   - is_live (BOOLEAN) - Current live status")
        print("   - is_verified (BOOLEAN) - Verification status")
        print("📈 Added performance indexes for new columns")
        
        await db_service.disconnect()
        return True
        
    except Exception as e:
        print(f"❌ Failed to add streamer columns: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(add_streamer_columns())