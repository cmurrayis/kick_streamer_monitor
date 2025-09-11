"""
CLI streamer management commands for Kick Streamer Status Monitor.

Provides commands for listing streamers, testing streamer connections,
and managing the streamers being monitored.
"""

import argparse
import asyncio
import json
import csv
import io
import sys
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from ..models import Streamer, StreamerStatus, StreamerCreate
from ..services import (
    DatabaseService, DatabaseConfig,
    KickOAuthService, OAuthConfig,
    KickWebSocketService, PusherConfig
)
from ..lib.logging import setup_logging


class StreamerCommandError(Exception):
    """Streamer command specific errors."""
    pass


class StreamerCommands:
    """Streamer management CLI commands."""
    
    def __init__(self):
        self.logger = setup_logging(__name__)
    
    async def list_streamers(self, args: argparse.Namespace) -> int:
        """List streamers being monitored."""
        try:
            # Get arguments
            output_format = getattr(args, 'format', 'table')
            status_filter = getattr(args, 'status', None)
            active_only = getattr(args, 'active', False)
            config_file = getattr(args, 'config', None)
            
            # Load database configuration
            db_config = self._load_database_config(config_file)
            if not db_config:
                print("Error: Could not load database configuration")
                return 2
            
            # Connect to database
            db_service = DatabaseService(db_config)
            await db_service.connect()
            
            try:
                # Get streamers based on filters
                if active_only:
                    streamers = await db_service.get_active_streamers()
                else:
                    # For now, just get active streamers as we don't have get_all_streamers
                    all_streamers = await db_service.get_active_streamers()
                    streamers = all_streamers
                
                # Apply status filter
                if status_filter:
                    try:
                        filter_status = StreamerStatus(status_filter.lower())
                        streamers = [s for s in streamers if s.status == filter_status]
                    except ValueError:
                        print(f"Error: Invalid status '{status_filter}'. Valid statuses: online, offline, unknown")
                        return 6
                
                # Output results
                if output_format == 'json':
                    self._output_streamers_json(streamers)
                elif output_format == 'csv':
                    self._output_streamers_csv(streamers)
                else:  # table format
                    self._output_streamers_table(streamers, status_filter is not None)
                
                return 0
                
            finally:
                await db_service.disconnect()
        
        except Exception as e:
            self.logger.error(f"Failed to list streamers: {e}")
            print(f"Error: {e}")
            return 1
    
    async def test_streamer(self, args: argparse.Namespace) -> int:
        """Test connection to a specific streamer."""
        try:
            username = args.username
            config_file = getattr(args, 'config', None)
            verbose = getattr(args, 'verbose', False)
            
            print(f"Testing connection to streamer: {username}")
            print("-" * 50)
            
            # Load configurations
            db_config = self._load_database_config(config_file)
            oauth_config = self._load_oauth_config(config_file)
            
            test_results = {}
            overall_success = True
            
            # Test 1: Database lookup
            print("1. Database lookup...")
            if db_config:
                try:
                    db_service = DatabaseService(db_config)
                    await db_service.connect()
                    
                    try:
                        streamer = await db_service.get_streamer_by_username(username)
                        if streamer:
                            print(f"   ✓ Found in database (ID: {streamer.id}, Status: {streamer.status.value})")
                            test_results['database'] = {
                                'success': True,
                                'streamer_id': streamer.id,
                                'status': streamer.status.value,
                                'kick_user_id': streamer.kick_user_id
                            }
                        else:
                            print(f"   ⚠ Not found in database")
                            test_results['database'] = {'success': False, 'reason': 'not_found'}
                            overall_success = False
                    finally:
                        await db_service.disconnect()
                
                except Exception as e:
                    print(f"   ✗ Database error: {e}")
                    test_results['database'] = {'success': False, 'error': str(e)}
                    overall_success = False
            else:
                print("   ✗ No database configuration")
                test_results['database'] = {'success': False, 'error': 'no_config'}
                overall_success = False
            
            # Test 2: Kick.com API lookup
            print("2. Kick.com API lookup...")
            if oauth_config:
                try:
                    oauth_service = KickOAuthService(oauth_config)
                    await oauth_service.start()
                    
                    try:
                        channel_info = await oauth_service.get_channel_info(username)
                        
                        if channel_info:
                            print(f"   ✓ Found on Kick.com")
                            if verbose:
                                print(f"     - Channel ID: {channel_info.get('id')}")
                                print(f"     - Display Name: {channel_info.get('user', {}).get('username')}")
                                print(f"     - Live: {channel_info.get('livestream') is not None}")
                            
                            test_results['api'] = {
                                'success': True,
                                'channel_id': channel_info.get('id'),
                                'display_name': channel_info.get('user', {}).get('username'),
                                'is_live': channel_info.get('livestream') is not None
                            }
                        else:
                            print(f"   ✗ Not found on Kick.com")
                            test_results['api'] = {'success': False, 'reason': 'not_found'}
                            overall_success = False
                    
                    finally:
                        await oauth_service.close()
                
                except Exception as e:
                    print(f"   ✗ API error: {e}")
                    test_results['api'] = {'success': False, 'error': str(e)}
                    overall_success = False
            else:
                print("   ✗ No OAuth configuration")
                test_results['api'] = {'success': False, 'error': 'no_config'}
                overall_success = False
            
            # Test 3: WebSocket subscription (mock test)
            print("3. WebSocket subscription test...")
            if test_results.get('api', {}).get('success'):
                try:
                    # We can't easily test real WebSocket without the full service running
                    # So we'll do a basic validation test
                    channel_id = test_results['api'].get('channel_id')
                    if channel_id:
                        print(f"   ✓ Would subscribe to channel.{channel_id}")
                        test_results['websocket'] = {
                            'success': True,
                            'channel': f"channel.{channel_id}",
                            'note': 'Mock test - would subscribe in real monitoring'
                        }
                    else:
                        print(f"   ⚠ No channel ID available")
                        test_results['websocket'] = {'success': False, 'reason': 'no_channel_id'}
                
                except Exception as e:
                    print(f"   ✗ WebSocket test error: {e}")
                    test_results['websocket'] = {'success': False, 'error': str(e)}
            else:
                print("   ⚠ Skipped (API lookup failed)")
                test_results['websocket'] = {'success': False, 'reason': 'api_failed'}
            
            # Test summary
            print("\n" + "="*50)
            if overall_success:
                print(f"✓ Test completed successfully for: {username}")
            else:
                print(f"⚠ Test completed with issues for: {username}")
            
            # Detailed results if verbose
            if verbose:
                print("\nDetailed Results:")
                print(json.dumps(test_results, indent=2))
            
            return 0 if overall_success else 1
        
        except Exception as e:
            self.logger.error(f"Streamer test failed: {e}")
            print(f"Error: {e}")
            return 1
    
    async def add_streamer(self, args: argparse.Namespace) -> int:
        """Add a new streamer to monitoring."""
        try:
            username = args.username
            config_file = getattr(args, 'config', None)
            force = getattr(args, 'force', False)
            
            print(f"Adding streamer to monitoring: {username}")
            
            # Load configurations
            db_config = self._load_database_config(config_file)
            oauth_config = self._load_oauth_config(config_file)
            
            if not db_config:
                print("Error: Could not load database configuration")
                return 2
            
            if not oauth_config:
                print("Error: Could not load OAuth configuration")
                return 2
            
            # First, verify streamer exists on Kick.com
            print("Verifying streamer on Kick.com...")
            
            oauth_service = KickOAuthService(oauth_config)
            await oauth_service.start()
            
            try:
                channel_info = await oauth_service.get_channel_info(username)
                
                if not channel_info:
                    print(f"Error: Streamer '{username}' not found on Kick.com")
                    return 1
                
                kick_user_id = str(channel_info.get('id'))
                display_name = channel_info.get('user', {}).get('username')
                is_live = channel_info.get('livestream') is not None
                
                print(f"✓ Found streamer: {display_name} (ID: {kick_user_id})")
                if is_live:
                    print("✓ Currently live")
                else:
                    print("• Currently offline")
            
            finally:
                await oauth_service.close()
            
            # Add to database
            print("Adding to database...")
            
            db_service = DatabaseService(db_config)
            await db_service.connect()
            
            try:
                # Check if already exists
                existing = await db_service.get_streamer_by_username(username)
                if existing and not force:
                    print(f"Error: Streamer '{username}' already exists in database")
                    print("Use --force to update existing streamer")
                    return 1
                
                if existing and force:
                    print("Updating existing streamer...")
                    # Update existing streamer
                    from ..models import StreamerUpdate
                    update = StreamerUpdate(
                        display_name=display_name,
                        status=StreamerStatus.ONLINE if is_live else StreamerStatus.OFFLINE,
                        is_active=True
                    )
                    updated_streamer = await db_service.update_streamer(existing.id, update)
                    print(f"✓ Updated streamer: {updated_streamer.username}")
                else:
                    # Create new streamer
                    streamer_create = StreamerCreate(
                        kick_user_id=kick_user_id,
                        username=username,
                        display_name=display_name,
                        status=StreamerStatus.ONLINE if is_live else StreamerStatus.OFFLINE,
                        is_active=True
                    )
                    
                    new_streamer = await db_service.create_streamer(streamer_create)
                    print(f"✓ Added streamer: {new_streamer.username} (DB ID: {new_streamer.id})")
                
                print("Streamer successfully added to monitoring!")
                return 0
            
            finally:
                await db_service.disconnect()
        
        except Exception as e:
            self.logger.error(f"Failed to add streamer: {e}")
            print(f"Error: {e}")
            return 1
    
    def _load_database_config(self, config_file: Optional[str]) -> Optional[DatabaseConfig]:
        """Load database configuration."""
        try:
            import os
            
            if config_file and os.path.exists(config_file):
                # Load from file
                config_vars = {}
                with open(config_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            config_vars[key] = value.strip('"\'')
                
                return DatabaseConfig(
                    host=config_vars.get('DATABASE_HOST', 'localhost'),
                    port=int(config_vars.get('DATABASE_PORT', 5432)),
                    database=config_vars.get('DATABASE_NAME', 'kick_monitor'),
                    username=config_vars.get('DATABASE_USER', 'kick_monitor'),
                    password=config_vars.get('DATABASE_PASSWORD', ''),
                )
            else:
                # Load from environment
                return DatabaseConfig(
                    host=os.getenv('DATABASE_HOST', 'localhost'),
                    port=int(os.getenv('DATABASE_PORT', 5432)),
                    database=os.getenv('DATABASE_NAME', 'kick_monitor'),
                    username=os.getenv('DATABASE_USER', 'kick_monitor'),
                    password=os.getenv('DATABASE_PASSWORD', ''),
                )
        
        except Exception as e:
            self.logger.debug(f"Could not load database config: {e}")
            return None
    
    def _load_oauth_config(self, config_file: Optional[str]) -> Optional[OAuthConfig]:
        """Load OAuth configuration."""
        try:
            import os
            
            if config_file and os.path.exists(config_file):
                # Load from file
                config_vars = {}
                with open(config_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            config_vars[key] = value.strip('"\'')
                
                client_id = config_vars.get('KICK_CLIENT_ID')
                client_secret = config_vars.get('KICK_CLIENT_SECRET')
            else:
                # Load from environment
                client_id = os.getenv('KICK_CLIENT_ID')
                client_secret = os.getenv('KICK_CLIENT_SECRET')
            
            if not client_id or not client_secret:
                return None
            
            return OAuthConfig(
                client_id=client_id,
                client_secret=client_secret
            )
        
        except Exception as e:
            self.logger.debug(f"Could not load OAuth config: {e}")
            return None
    
    def _output_streamers_table(self, streamers: List[Streamer], show_status_detail: bool = False) -> None:
        """Output streamers as table."""
        if not streamers:
            print("No streamers found.")
            return
        
        # Calculate column widths
        max_username_width = max(len(s.username) for s in streamers)
        max_display_width = max(len(s.display_name or '') for s in streamers)
        max_id_width = max(len(str(s.kick_user_id)) for s in streamers)
        
        # Ensure minimum widths
        max_username_width = max(max_username_width, 8)
        max_display_width = max(max_display_width, 12)
        max_id_width = max(max_id_width, 10)
        
        # Print header
        if show_status_detail:
            header = f"{'Username':<{max_username_width}} | {'Display Name':<{max_display_width}} | {'Kick ID':<{max_id_width}} | {'Status':<7} | {'Last Seen':<19} | {'Active'}"
        else:
            header = f"{'Username':<{max_username_width}} | {'Display Name':<{max_display_width}} | {'Kick ID':<{max_id_width}} | {'Status':<7} | {'Active'}"
        
        print(header)
        print("-" * len(header))
        
        # Print streamers
        for streamer in sorted(streamers, key=lambda x: x.username):
            display_name = streamer.display_name or ''
            if len(display_name) > max_display_width:
                display_name = display_name[:max_display_width-3] + "..."
            
            status_color = self._get_status_color(streamer.status)
            status_display = f"{status_color}{streamer.status.value:<7}\033[0m"
            
            active_display = "Yes" if streamer.is_active else "No"
            
            if show_status_detail and streamer.last_seen_online:
                last_seen = streamer.last_seen_online.strftime("%Y-%m-%d %H:%M:%S")
                line = f"{streamer.username:<{max_username_width}} | {display_name:<{max_display_width}} | {streamer.kick_user_id:<{max_id_width}} | {status_display} | {last_seen:<19} | {active_display}"
            else:
                line = f"{streamer.username:<{max_username_width}} | {display_name:<{max_display_width}} | {streamer.kick_user_id:<{max_id_width}} | {status_display} | {active_display}"
            
            print(line)
        
        print(f"\nTotal streamers: {len(streamers)}")
        
        # Status summary
        online_count = sum(1 for s in streamers if s.status == StreamerStatus.ONLINE)
        offline_count = sum(1 for s in streamers if s.status == StreamerStatus.OFFLINE)
        unknown_count = sum(1 for s in streamers if s.status == StreamerStatus.UNKNOWN)
        
        print(f"Online: {online_count}, Offline: {offline_count}, Unknown: {unknown_count}")
    
    def _output_streamers_json(self, streamers: List[Streamer]) -> None:
        """Output streamers as JSON."""
        output_data = []
        
        for streamer in streamers:
            data = {
                'id': streamer.id,
                'username': streamer.username,
                'display_name': streamer.display_name,
                'kick_user_id': streamer.kick_user_id,
                'status': streamer.status.value,
                'last_seen_online': streamer.last_seen_online.isoformat() if streamer.last_seen_online else None,
                'last_status_update': streamer.last_status_update.isoformat() if streamer.last_status_update else None,
                'created_at': streamer.created_at.isoformat() if streamer.created_at else None,
                'updated_at': streamer.updated_at.isoformat() if streamer.updated_at else None,
                'is_active': streamer.is_active
            }
            output_data.append(data)
        
        print(json.dumps(output_data, indent=2))
    
    def _output_streamers_csv(self, streamers: List[Streamer]) -> None:
        """Output streamers as CSV."""
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            'id', 'username', 'display_name', 'kick_user_id', 'status',
            'last_seen_online', 'last_status_update', 'created_at', 'updated_at', 'is_active'
        ])
        
        # Write data
        for streamer in streamers:
            writer.writerow([
                streamer.id,
                streamer.username,
                streamer.display_name or '',
                streamer.kick_user_id,
                streamer.status.value,
                streamer.last_seen_online.isoformat() if streamer.last_seen_online else '',
                streamer.last_status_update.isoformat() if streamer.last_status_update else '',
                streamer.created_at.isoformat() if streamer.created_at else '',
                streamer.updated_at.isoformat() if streamer.updated_at else '',
                streamer.is_active
            ])
        
        print(output.getvalue().strip())
    
    def _get_status_color(self, status: StreamerStatus) -> str:
        """Get ANSI color code for status."""
        if status == StreamerStatus.ONLINE:
            return "\033[32m"  # Green
        elif status == StreamerStatus.OFFLINE:
            return "\033[31m"  # Red
        else:  # UNKNOWN
            return "\033[33m"  # Yellow