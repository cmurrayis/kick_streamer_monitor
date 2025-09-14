#!/usr/bin/env python3
"""
Database migration script for Kick Streamer Monitor.

Applies user management schema to existing database.
"""

import asyncio
import logging
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

logger = logging.getLogger(__name__)

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

async def run_migration():
    """Run database migration to add user management tables."""
    try:
        # Initialize database service with config
        config = get_database_config()
        db_service = DatabaseService(config)
        await db_service.connect()
        
        print("🚀 Starting database migration...")
        
        # Read the user schema SQL
        schema_path = Path(__file__).parent.parent / "src" / "models" / "user_schema.sql"
        
        if not schema_path.exists():
            print(f"❌ Schema file not found: {schema_path}")
            return False
        
        with open(schema_path, 'r') as f:
            schema_sql = f.read()
        
        print("📋 Applying user management schema...")
        
        # Execute the schema
        await db_service.pool.execute(schema_sql)
        
        print("✅ Migration completed successfully!")
        print("👤 User management tables created:")
        print("   - users")
        print("   - user_streamer_assignments")
        print("   - Default admin user created (username: admin, password: admin123)")
        print("   - Test user created (username: testuser, password: user123)")
        
        return True
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        logger.error(f"Migration error: {e}")
        return False
    
    finally:
        if 'db_service' in locals():
            await db_service.disconnect()

async def check_migration_status():
    """Check if migration has already been applied."""
    try:
        config = get_database_config()
        db_service = DatabaseService(config)
        await db_service.connect()
        
        # Check if users table exists
        result = await db_service.pool.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'users'
            );
        """)
        
        await db_service.disconnect()
        return result
        
    except Exception as e:
        logger.error(f"Error checking migration status: {e}")
        return False

async def main():
    """Main migration function."""
    logging.basicConfig(level=logging.INFO)
    
    # Display database configuration
    config = get_database_config()
    print(f"🔍 Using database configuration:")
    print(f"   Host: {config.host}:{config.port}")
    print(f"   Database: {config.database}")
    print(f"   User: {config.username}")
    print()
    
    print("🔍 Checking migration status...")
    
    # Check if migration already applied
    already_migrated = await check_migration_status()
    
    if already_migrated:
        print("ℹ️  Migration already applied - users table exists")
        print("   Use --force flag to re-run migration")
        
        if "--force" not in sys.argv:
            return
        else:
            print("⚠️  Force flag detected - re-running migration...")
    
    # Run the migration
    success = await run_migration()
    
    if success:
        print("\n🎉 Database migration completed successfully!")
        print("🔐 You can now log in to the admin panel at: http://localhost:8080/login")
        print("   Username: admin")
        print("   Password: admin123")
        print("\n⚠️  Remember to change the default admin password!")
    else:
        print("\n💥 Migration failed - check logs for details")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())