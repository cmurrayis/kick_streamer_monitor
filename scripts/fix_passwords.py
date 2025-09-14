#!/usr/bin/env python3
"""
Fix password hashes in existing database.
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

async def fix_passwords():
    """Update password hashes in database."""
    try:
        config = get_database_config()
        db_service = DatabaseService(config)
        await db_service.connect()
        
        print("🔐 Updating password hashes...")
        
        # Update admin password hash
        admin_hash = 'e37274ea6cd884f0d96960f501ecfb1ef329e054b03a9883e133e9681f55eb64'
        await db_service.pool.execute("""
            UPDATE users 
            SET password_hash = $1 
            WHERE username = 'admin'
        """, admin_hash)
        
        # Update test user password hash
        user_hash = '618aa2e6d55ce49e3a97b98648d3f706f35a65b81d918057fdbda87197ce81db'
        await db_service.pool.execute("""
            UPDATE users 
            SET password_hash = $1 
            WHERE username = 'testuser'
        """, user_hash)
        
        print("✅ Password hashes updated successfully!")
        print("📝 Login credentials:")
        print("   Admin: username=admin, password=admin123")
        print("   Test User: username=testuser, password=user123")
        
        await db_service.disconnect()
        return True
        
    except Exception as e:
        print(f"❌ Failed to update passwords: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(fix_passwords())