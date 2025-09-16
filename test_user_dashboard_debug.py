#!/usr/bin/env python3

"""
Test script to debug user dashboard streamer loading issue.
Run this on your server to help identify where the streamer list becomes empty.
"""

import asyncio
import sys
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from services.database import DatabaseService
from services.snags_database import SnagsDatabaseService
from models.streamer import Streamer
from models.config import DatabaseConfig
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def test_user_dashboard_logic():
    """Test the user dashboard logic step by step."""

    # Initialize database config
    db_config = DatabaseConfig(
        host=os.getenv('DATABASE_HOST', 'localhost'),
        port=int(os.getenv('DATABASE_PORT', 5432)),
        user=os.getenv('DATABASE_USER'),
        password=os.getenv('DATABASE_PASSWORD'),
        database=os.getenv('DATABASE_NAME', 'kick_monitor')
    )

    # Initialize services
    db_service = DatabaseService(db_config)
    await db_service.connect()

    # Test with user cameron (ID should be 1 or 2 based on previous conversation)
    test_user_id = 1  # Adjust this if needed
    test_username = "cameron"

    print(f"\n=== Testing User Dashboard Logic for {test_username} (ID: {test_user_id}) ===")

    try:
        # Step 1: Get assignments
        print("\nStep 1: Getting user assignments...")
        assignments = await db_service.get_user_streamer_assignments(test_user_id)
        print(f"Found {len(assignments)} assignments")
        for a in assignments:
            print(f"  - Assignment: streamer_id={a.streamer_id}, assigned_at={getattr(a, 'assigned_at', 'N/A')}")

        if not assignments:
            print("❌ No assignments found - this explains the empty dashboard!")
            return

        # Step 2: Get streamer IDs
        streamer_ids = [a.streamer_id for a in assignments]
        print(f"\nStep 2: Streamer IDs to fetch: {streamer_ids}")

        # Step 3: Fetch streamers by ID
        print("\nStep 3: Fetching streamers by ID...")
        all_streamers_basic = []
        for streamer_id in streamer_ids:
            streamer = await db_service.get_streamer_by_id(streamer_id)
            if streamer:
                print(f"  ✓ Found streamer: {streamer.username} (ID: {streamer.id}, Status: {streamer.status})")
                all_streamers_basic.append(streamer)
            else:
                print(f"  ❌ Streamer with ID {streamer_id} not found!")

        print(f"Fetched {len(all_streamers_basic)} basic streamers")

        if not all_streamers_basic:
            print("❌ No streamers found by ID - assignments reference non-existent streamers!")
            return

        # Step 4: Get worker data
        print("\nStep 4: Getting worker data...")
        usernames = [s.username for s in all_streamers_basic]
        print(f"Usernames for worker data: {usernames}")

        worker_data_map = await db_service.snags_service.get_worker_data_for_multiple_streamers(usernames)
        print(f"Worker data map: {worker_data_map}")

        # Step 5: Build enhanced streamer data
        print("\nStep 5: Building enhanced streamer data...")
        all_streamers = []
        for streamer in all_streamers_basic:
            worker_data = worker_data_map.get(streamer.username, {'running': 0, 'assigned': 0})
            current_viewers = getattr(streamer, 'current_viewers', None)

            # Handle offline streamers
            if streamer.status.value == 'offline':
                current_viewers = 0
            else:
                current_viewers = current_viewers or 0

            running_workers = worker_data['running']
            assigned_capacity = worker_data['assigned']
            humans = max(0, current_viewers - running_workers)

            streamer_dict = streamer.dict()
            streamer_dict.update({
                'current_viewers': current_viewers,
                'running_workers': running_workers,
                'assigned_capacity': assigned_capacity,
                'humans': humans
            })
            all_streamers.append(streamer_dict)
            print(f"  ✓ Enhanced streamer: {streamer.username}")

        print(f"Built {len(all_streamers)} enhanced streamer records")

        # Step 6: Convert back to Streamer objects (this is where it might fail)
        print("\nStep 6: Converting back to Streamer objects...")
        streamers = []

        for streamer_data in all_streamers:
            try:
                print(f"  Processing: {streamer_data['username']}")

                # Filter fields to only those that exist in the Streamer model
                streamer_fields = {}
                for field_name in Streamer.__fields__:
                    if field_name in streamer_data:
                        value = streamer_data[field_name]
                        # Handle datetime fields
                        if field_name in ['created_at', 'updated_at', 'last_seen_online', 'last_status_update']:
                            if isinstance(value, str):
                                from datetime import datetime
                                try:
                                    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
                                except:
                                    pass
                        streamer_fields[field_name] = value

                print(f"    Streamer fields: {list(streamer_fields.keys())}")
                streamer = Streamer(**streamer_fields)

                # Add worker data as attributes
                streamer.running_workers = streamer_data.get('running_workers', 0)
                streamer.assigned_capacity = streamer_data.get('assigned_capacity', 0)
                streamer.humans = streamer_data.get('humans', 0)
                streamers.append(streamer)
                print(f"    ✓ Successfully created Streamer object for {streamer.username}")

            except Exception as e:
                print(f"    ❌ Error creating Streamer object for {streamer_data.get('username', 'unknown')}: {e}")
                print(f"    Streamer data keys: {list(streamer_data.keys())}")
                import traceback
                print(f"    Full error: {traceback.format_exc()}")

        print(f"\nFinal result: {len(streamers)} Streamer objects created")

        # Step 7: Calculate stats
        online_count = sum(1 for s in streamers if s.status.value == 'online')
        offline_count = len(streamers) - online_count
        total_viewers = sum(s.current_viewers for s in streamers if s.status.value == 'online' and hasattr(s, 'current_viewers') and s.current_viewers)

        print(f"\nStats: Assigned={len(streamers)}, Online={online_count}, Offline={offline_count}, Total Viewers={total_viewers}")

        if len(streamers) == 0:
            print("\n❌ ISSUE FOUND: Streamers list became empty during Streamer object creation!")
        else:
            print(f"\n✓ SUCCESS: {len(streamers)} streamers ready for dashboard")

    except Exception as e:
        print(f"\n❌ CRITICAL ERROR: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")

    finally:
        await db_service.disconnect()

if __name__ == "__main__":
    asyncio.run(test_user_dashboard_logic())