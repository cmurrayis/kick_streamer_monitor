"""
CLI service commands for Kick Streamer Status Monitor.

Provides commands for starting, stopping, and checking status of the
monitoring service in different modes (automatic, manual, dry-run).
"""

import argparse
import asyncio
import json
import os
import signal
import sys
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pathlib import Path

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

from models import ConfigurationDefaults, EnvironmentConfigLoader
from services import (
    DatabaseService, DatabaseConfig,
    KickOAuthService, OAuthConfig,
    KickWebSocketService, PusherConfig,
    KickMonitorService, MonitoringMode
)
from services.simple_monitor import SimpleMonitorService
from services.web_dashboard import WebDashboardService
from services.analytics import AnalyticsService
from lib.logging import get_logger
from .manual import run_manual_mode


class ServiceCommandError(Exception):
    """Service command specific errors.""" 
    pass


class ServiceCommands:
    """Service management CLI commands."""
    
    def __init__(self):
        self.logger = get_logger(__name__)
        self._monitor_service: Optional[KickMonitorService] = None
        self._web_dashboard: Optional[WebDashboardService] = None
        self._analytics_service: Optional[AnalyticsService] = None
        self._shutdown_requested = False
    
    async def start(self, args: argparse.Namespace) -> int:
        """Start the monitoring service."""
        try:
            # Parse arguments
            config_file = getattr(args, 'config', None)
            dry_run = getattr(args, 'dry_run', False)
            daemon = getattr(args, 'daemon', False)
            manual = getattr(args, 'manual', False)
            
            # Web dashboard options (for daemon mode)
            enable_web_dashboard = getattr(args, 'web_dashboard', True) and not getattr(args, 'no_web_dashboard', False)
            dashboard_host = getattr(args, 'dashboard_host', '0.0.0.0')
            dashboard_port = getattr(args, 'dashboard_port', 8080)
            admin_username = getattr(args, 'admin_username', 'admin')
            admin_password = getattr(args, 'admin_password', 'password')
            # Load .env file if available for service configuration
            if DOTENV_AVAILABLE:
                env_file = Path('.env')
                if not env_file.exists():
                    env_file = Path('../.env')
                if env_file.exists():
                    load_dotenv(env_file)

            # Determine log level - priority: CLI args > ENV var > default
            cli_log_level = getattr(args, 'log_level', None)
            if cli_log_level:
                log_level = cli_log_level
            else:
                # Check environment variable for LOG_LEVEL (try both SYSTEM_LOG_LEVEL and LOG_LEVEL)
                env_log_level = os.getenv('SYSTEM_LOG_LEVEL') or os.getenv('LOG_LEVEL', 'INFO')
                env_log_level = env_log_level.upper()
                log_level = env_log_level

            browser_fallback = getattr(args, 'browser_fallback', True)
            simple_mode = getattr(args, 'simple_mode', False)

            # Determine monitoring mode
            if dry_run:
                mode = MonitoringMode.DRY_RUN
            elif manual:
                mode = MonitoringMode.MANUAL
            else:
                mode = MonitoringMode.AUTOMATIC

            # Validate conflicting options
            if daemon and manual:
                print("Error: Cannot run in both daemon and manual mode")
                return 6

            monitor_type = "simple polling" if simple_mode else "full WebSocket"
            print(f"Starting Kick Streamer Monitor in {mode.value} mode ({monitor_type})...")
            print(f"Using log level: {log_level}")

            # Setup logging for the service
            from lib.logging import setup_logging
            setup_logging(level=log_level.upper())
            self.logger = get_logger(__name__)
            
            # Load configurations
            db_config = self._load_database_config(config_file)
            oauth_config = self._load_oauth_config(config_file)
            pusher_config = self._load_pusher_config(config_file)
            
            if not db_config:
                print("Error: Could not load database configuration")
                return 2
            
            if not oauth_config:
                print("Error: Could not load OAuth configuration")
                return 3
            
            if not pusher_config:
                print("Error: Could not load Pusher configuration")
                return 3
            
            # Initialize services
            print("Initializing services...")
            
            db_service = DatabaseService(db_config)
            oauth_service = KickOAuthService(oauth_config, enable_browser_fallback=browser_fallback)
            
            # Test connections before starting
            print("Testing connections...")
            
            # Test database
            await db_service.connect()
            db_health = await db_service.health_check()
            if db_health['status'] != 'healthy':
                print(f"Error: Database connection failed: {db_health.get('error')}")
                return 2
            print("Database connection OK")
            
            # Test OAuth
            await oauth_service.start()
            auth_test = await oauth_service.test_authentication()
            if auth_test['status'] not in ['success', 'token_obtained']:
                print(f"Error: OAuth authentication failed: {auth_test.get('error')}")
                return 3
            
            # Show auth test result
            if auth_test.get('browser_fallback_used'):
                print(" OAuth + Browser fallback ready")
            elif auth_test.get('browser_fallback_available'):
                print(" OAuth ready (browser fallback available)")
            else:
                print(" OAuth ready")
            
            # Initialize monitoring service based on mode
            if simple_mode:
                print("Using simple polling monitor (like JavaScript version)")
                self._monitor_service = SimpleMonitorService(
                    database_service=db_service,
                    oauth_service=oauth_service
                )
            else:
                print("Using full WebSocket monitor")
                websocket_service = KickWebSocketService(pusher_config, oauth_service)
                self._monitor_service = KickMonitorService(
                    database_service=db_service,
                    oauth_service=oauth_service,
                    websocket_service=websocket_service,
                    mode=mode
                )
            
            # Setup signal handlers for graceful shutdown
            if not daemon:
                self._setup_signal_handlers()
            
            # Start monitoring service
            print("Starting monitoring service...")
            await self._monitor_service.start()

            print(f" Monitoring service started successfully")

            # Start analytics service if not in manual mode
            if mode != MonitoringMode.MANUAL:
                print("Starting analytics collection service...")
                self._analytics_service = AnalyticsService(
                    database_service=db_service,
                    oauth_service=oauth_service,
                    collection_interval=60  # 1-minute intervals
                )
                await self._analytics_service.start()
                print(" Analytics service started (1-minute intervals)")
            
            # Start web dashboard for daemon mode
            if daemon and enable_web_dashboard:
                print(f"Starting web dashboard on {dashboard_host}:{dashboard_port}...")
                self._web_dashboard = WebDashboardService(
                    monitor_service=self._monitor_service,
                    host=dashboard_host,
                    port=dashboard_port,
                    admin_username=admin_username,
                    admin_password=admin_password
                )
                await self._web_dashboard.start()
                print(f" Web dashboard available at: {self._web_dashboard.url}")
            
            # Different behavior based on mode
            if manual:
                return await self._run_manual_mode()
            elif daemon:
                return await self._run_daemon_mode()
            else:
                return await self._run_interactive_mode()
        
        except KeyboardInterrupt:
            print("\nShutdown requested by user")
            return await self._shutdown_service()
        except Exception as e:
            self.logger.error(f"Service start failed: {e}")
            print(f"Error: {e}")
            return 1
    
    async def stop(self, args: argparse.Namespace) -> int:
        """Stop the monitoring service."""
        try:
            # For now, this is a simple implementation
            # In a full implementation, this would connect to a running daemon
            # and send a shutdown signal
            
            print("Stopping monitoring service...")
            
            # Check if we have a running service in this process
            if self._monitor_service and self._monitor_service.is_running:
                # Stop analytics service first
                if self._analytics_service:
                    await self._analytics_service.stop()
                    print(" Analytics service stopped")

                await self._monitor_service.stop()
                print(" Monitor service stopped")
                return 0
            else:
                print("No running service found in current process")
                
                # In production, this would check for running daemon process
                # and send appropriate signals
                print("Note: To stop a daemon service, use system service management")
                print("Example: sudo systemctl stop kick-monitor")
                return 7
        
        except Exception as e:
            self.logger.error(f"Service stop failed: {e}")
            print(f"Error: {e}")
            return 1
    
    async def status(self, args: argparse.Namespace) -> int:
        """Show monitoring service status."""
        try:
            output_format = getattr(args, 'format', 'table')
            config_file = getattr(args, 'config', None)
            
            # If we have a running service in this process, show its status
            if self._monitor_service and self._monitor_service.is_running:
                stats = self._monitor_service.get_monitoring_stats()
                streamers = self._monitor_service.get_streamer_details()
                
                if output_format == 'json':
                    output_data = {
                        'service': stats,
                        'streamers': streamers
                    }
                    print(json.dumps(output_data, indent=2))
                else:
                    self._output_status_table(stats, streamers)
                
                return 0
            else:
                # Try to get status from database if possible
                return await self._get_database_status(config_file, output_format)
        
        except Exception as e:
            self.logger.error(f"Status check failed: {e}")
            print(f"Error: {e}")
            return 1
    
    async def restart(self, args: argparse.Namespace) -> int:
        """Restart the monitoring service."""
        print("Restarting monitoring service...")
        
        # Stop current service
        stop_result = await self.stop(args)
        if stop_result == 0:
            print("Service stopped, starting again...")
            # Small delay
            await asyncio.sleep(1)
            # Start again
            return await self.start(args)
        else:
            print("Could not stop service, attempting fresh start...")
            return await self.start(args)
    
    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            print(f"\nReceived signal {signum}")
            self._shutdown_requested = True
        
        # Setup handlers for common signals
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, signal_handler)
        if hasattr(signal, 'SIGINT'):
            signal.signal(signal.SIGINT, signal_handler)
    
    async def _run_manual_mode(self) -> int:
        """Run in manual/interactive mode with UI."""
        try:
            # Use the dedicated manual mode UI
            return await run_manual_mode(self._monitor_service)
        
        except KeyboardInterrupt:
            return await self._shutdown_service()
        except Exception as e:
            self.logger.error(f"Manual mode error: {e}")
            print(f"Manual mode error: {e}")
            return 1
    
    async def _run_daemon_mode(self) -> int:
        """Run in daemon mode (background)."""
        print("Running in daemon mode...")
        print("Service will run in background until stopped")
        
        try:
            # In daemon mode, just keep the service running
            while not self._shutdown_requested and self._monitor_service.is_running:
                await asyncio.sleep(10)  # Check every 10 seconds
            
            return await self._shutdown_service()
        
        except Exception as e:
            self.logger.error(f"Daemon mode error: {e}")
            return 1
    
    async def _run_interactive_mode(self) -> int:
        """Run in interactive mode."""
        print("Service started in interactive mode")
        print("Press Ctrl+C to stop the service")
        
        try:
            # Show periodic status updates
            while not self._shutdown_requested and self._monitor_service.is_running:
                await asyncio.sleep(30)  # Status update every 30 seconds
                
                stats = self._monitor_service.get_monitoring_stats()
                streamers = stats['streamers']
                processing = stats['processing']
                
                print(f"\nStatus Update - {datetime.now().strftime('%H:%M:%S')}")
                print(f"Streamers: {streamers['total_monitored']} total, "
                      f"{streamers['online']} online, {streamers['offline']} offline")
                print(f"Events processed: {processing['total_events_processed']}, "
                      f"Queue size: {processing['queue_size']}")
            
            return await self._shutdown_service()
        
        except KeyboardInterrupt:
            return await self._shutdown_service()
    
    async def _shutdown_service(self) -> int:
        """Gracefully shutdown the service."""
        try:
            print("\nShutting down services...")
            
            # Stop web dashboard first
            if self._web_dashboard and self._web_dashboard.is_running:
                print("Stopping web dashboard...")
                await self._web_dashboard.stop()
                print(" Web dashboard stopped")

            # Stop analytics service
            if self._analytics_service:
                print("Stopping analytics service...")
                await self._analytics_service.stop()
                print(" Analytics service stopped")

            # Stop monitoring service
            if self._monitor_service:
                print("Stopping monitoring service...")
                await self._monitor_service.stop()
                print(" Monitoring service stopped")
            
            print(" Shutdown complete")
            return 0
        
        except Exception as e:
            self.logger.error(f"Shutdown error: {e}")
            return 1
    
    async def _get_database_status(self, config_file: Optional[str], output_format: str) -> int:
        """Get status from database when service is not running."""
        try:
            db_config = self._load_database_config(config_file)
            if not db_config:
                print("Error: Could not load database configuration")
                return 2
            
            db_service = DatabaseService(db_config)
            await db_service.connect()
            
            try:
                # Get database statistics
                db_stats = await db_service.get_database_stats()
                db_health = await db_service.health_check()
                
                # Get active streamers
                streamers = await db_service.get_active_streamers()
                
                if output_format == 'json':
                    output_data = {
                        'service_status': 'stopped',
                        'database': {
                            'status': db_health['status'],
                            'stats': db_stats
                        },
                        'streamers': {
                            'total': len(streamers),
                            'online': sum(1 for s in streamers if s.status.value == 'online'),
                            'offline': sum(1 for s in streamers if s.status.value == 'offline'),
                            'unknown': sum(1 for s in streamers if s.status.value == 'unknown')
                        }
                    }
                    print(json.dumps(output_data, indent=2))
                else:
                    print("Monitoring Service Status")
                    print("=" * 50)
                    print(f"Service Status: STOPPED")
                    print(f"Database Status: {db_health['status'].upper()}")
                    print(f"Total Streamers: {len(streamers)}")
                    
                    online_count = sum(1 for s in streamers if s.status.value == 'online')
                    offline_count = sum(1 for s in streamers if s.status.value == 'offline')
                    unknown_count = sum(1 for s in streamers if s.status.value == 'unknown')
                    
                    print(f"  • Online: {online_count}")
                    print(f"  • Offline: {offline_count}")
                    print(f"  • Unknown: {unknown_count}")
                    
                    print(f"\nDatabase Statistics:")
                    for key, value in db_stats.items():
                        if isinstance(value, int):
                            print(f"  • {key.replace('_', ' ').title()}: {value:,}")
                        else:
                            print(f"  • {key.replace('_', ' ').title()}: {value}")
                
                return 0
            
            finally:
                await db_service.disconnect()
        
        except Exception as e:
            print(f"Could not get status: {e}")
            return 1
    
    def _output_status_table(self, stats: Dict[str, Any], streamers: list) -> None:
        """Output service status as table."""
        service = stats['service_status']
        streamer_stats = stats['streamers']
        processing = stats['processing']
        connections = stats['connections']
        
        print("Monitoring Service Status")
        print("=" * 50)
        
        # Service info
        print(f"Status: {service['is_running'] and 'RUNNING' or 'STOPPED'}")
        print(f"Mode: {service['mode'].upper()}")
        print(f"Uptime: {service['uptime_seconds']:.0f} seconds")
        
        # Streamer stats
        print(f"\nStreamers:")
        print(f"  • Total Monitored: {streamer_stats['total_monitored']}")
        print(f"  • Online: {streamer_stats['online']}")
        print(f"  • Offline: {streamer_stats['offline']}")
        print(f"  • Unknown: {streamer_stats['unknown']}")
        print(f"  • Subscribed: {streamer_stats['subscribed']}")
        
        # Processing stats
        print(f"\nProcessing:")
        print(f"  • Events Processed: {processing['total_events_processed']:,}")
        print(f"  • Status Updates: {processing['total_status_updates']:,}")
        print(f"  • Database Writes: {processing['total_database_writes']:,}")
        print(f"  • Queue Size: {processing['queue_size']}")
        
        # Connection status
        ws_status = connections['websocket']
        print(f"\nConnections:")
        print(f"  • WebSocket: {'Connected' if ws_status['is_connected'] else 'Disconnected'}")
        print(f"  • Subscribed Channels: {ws_status['subscribed_channels']}")
        print(f"  • Total Messages: {ws_status['total_messages']:,}")
    
    def _load_database_config(self, config_file: Optional[str]) -> Optional[DatabaseConfig]:
        """Load database configuration."""
        try:
            if config_file and os.path.exists(config_file):
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
            if config_file and os.path.exists(config_file):
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
    
    def _load_pusher_config(self, config_file: Optional[str]) -> Optional[PusherConfig]:
        """Load Pusher WebSocket configuration."""
        try:
            # For now, use default Kick.com Pusher settings
            # In production, these would be configurable
            
            # These values would typically be discovered from Kick.com API
            app_key = "d44c06c23c42fd6e5c58"  # This is Kick.com's public Pusher app key
            cluster = "us2"
            
            return PusherConfig(
                app_key=app_key,
                cluster=cluster
            )
        
        except Exception as e:
            self.logger.debug(f"Could not load Pusher config: {e}")
            return None