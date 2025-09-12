"""
Main CLI entry point for Kick Streamer Status Monitor.

Provides the primary command-line interface with argument parsing,
command routing, and global options handling.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

from .config import ConfigCommands
from .streamers import StreamerCommands  
from .service import ServiceCommands
from .database import DatabaseCommands
from lib.logging import setup_logging

# Version information
__version__ = "1.0.0"
__app_name__ = "Kick Streamer Status Monitor"


class CLIError(Exception):
    """CLI specific errors."""
    pass


def create_parser() -> argparse.ArgumentParser:
    """Create the main argument parser."""
    
    parser = argparse.ArgumentParser(
        prog='kick-monitor',
        description='Kick.com Streamer Status Monitor - Real-time monitoring service for Kick streamers',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  kick-monitor config generate-template > .env
  kick-monitor config validate
  kick-monitor db migrate
  kick-monitor streamers list
  kick-monitor streamers test username
  kick-monitor start --manual
  kick-monitor start --daemon
  kick-monitor status

For more help on a specific command:
  kick-monitor <command> --help

Report issues at: https://github.com/your-org/kick-monitor/issues
        """
    )
    
    # Global options
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__version__}'
    )
    
    parser.add_argument(
        '--config', '-c',
        type=str,
        help='Configuration file path (default: .env or environment variables)'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )
    
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress non-error output'
    )
    
    # Create subparsers
    subparsers = parser.add_subparsers(
        dest='command',
        help='Available commands',
        metavar='COMMAND'
    )
    
    # Configuration commands
    config_parser = subparsers.add_parser(
        'config',
        help='Configuration management',
        description='Manage application configuration settings'
    )
    config_subparsers = config_parser.add_subparsers(
        dest='config_command',
        help='Configuration subcommands'
    )
    
    # config show
    show_parser = config_subparsers.add_parser(
        'show',
        help='Show current configuration'
    )
    show_parser.add_argument(
        '--format',
        choices=['table', 'json', 'yaml'],
        default='table',
        help='Output format (default: table)'
    )
    show_parser.add_argument(
        '--mask-secrets',
        action='store_true',
        default=True,
        help='Mask sensitive values in output (default: True)'
    )
    show_parser.add_argument(
        '--no-mask-secrets',
        dest='mask_secrets',
        action='store_false',
        help='Show sensitive values in plain text'
    )
    show_parser.add_argument(
        '--category',
        choices=['auth', 'database', 'monitoring', 'system'],
        help='Filter by configuration category'
    )
    
    # config validate
    validate_parser = config_subparsers.add_parser(
        'validate',
        help='Validate configuration'
    )
    
    # config generate-template
    template_parser = config_subparsers.add_parser(
        'generate-template',
        help='Generate configuration template'
    )
    template_parser.add_argument(
        '--format',
        choices=['env', 'json', 'yaml'],
        default='env',
        help='Template format (default: env)'
    )
    template_parser.add_argument(
        '--output', '-o',
        type=str,
        help='Output file (default: stdout)'
    )
    template_parser.add_argument(
        '--no-examples',
        dest='examples',
        action='store_false',
        default=True,
        help='Exclude example values'
    )
    
    # Streamer commands
    streamers_parser = subparsers.add_parser(
        'streamers',
        help='Streamer management',
        description='Manage streamers being monitored'
    )
    streamers_subparsers = streamers_parser.add_subparsers(
        dest='streamers_command',
        help='Streamer subcommands'
    )
    
    # streamers list
    list_parser = streamers_subparsers.add_parser(
        'list',
        help='List monitored streamers'
    )
    list_parser.add_argument(
        '--format',
        choices=['table', 'json', 'csv'],
        default='table',
        help='Output format (default: table)'
    )
    list_parser.add_argument(
        '--status',
        choices=['online', 'offline', 'unknown'],
        help='Filter by status'
    )
    list_parser.add_argument(
        '--active',
        action='store_true',
        help='Show only active streamers'
    )
    
    # streamers test
    test_parser = streamers_subparsers.add_parser(
        'test',
        help='Test connection to a specific streamer'
    )
    test_parser.add_argument(
        'username',
        help='Streamer username to test'
    )
    test_parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed test results'
    )
    test_parser.add_argument(
        '--method',
        choices=['oauth', 'browser', 'hybrid'],
        default='hybrid',
        help='Test method (default: hybrid - OAuth with browser fallback)'
    )
    
    # streamers add
    add_parser = streamers_subparsers.add_parser(
        'add',
        help='Add streamer to monitoring'
    )
    add_parser.add_argument(
        'username',
        help='Streamer username to add'
    )
    add_parser.add_argument(
        '--force',
        action='store_true',
        help='Update if streamer already exists'
    )
    
    # Service commands
    service_parser = subparsers.add_parser(
        'start',
        help='Start monitoring service',
        description='Start the monitoring service in various modes'
    )
    service_parser.add_argument(
        '--daemon', '-d',
        action='store_true',
        help='Run as background daemon'
    )
    service_parser.add_argument(
        '--manual', '-m',
        action='store_true',
        help='Run in manual/interactive mode with UI'
    )
    service_parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run without making database changes (testing)'
    )
    service_parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help='Set logging level (default: INFO)'
    )
    service_parser.add_argument(
        '--browser-fallback',
        action='store_true',
        default=True,
        help='Enable browser fallback for Cloudflare bypass (default: True)'
    )
    service_parser.add_argument(
        '--no-browser-fallback',
        dest='browser_fallback',
        action='store_false',
        help='Disable browser fallback, OAuth only'
    )
    service_parser.add_argument(
        '--simple-mode',
        action='store_true',
        help='Use simple polling monitor (avoid WebSocket complexity)'
    )
    
    # stop command
    stop_parser = subparsers.add_parser(
        'stop',
        help='Stop monitoring service'
    )
    
    # restart command
    restart_parser = subparsers.add_parser(
        'restart',
        help='Restart monitoring service'
    )
    restart_parser.add_argument(
        '--daemon', '-d',
        action='store_true',
        help='Run as background daemon after restart'
    )
    restart_parser.add_argument(
        '--manual', '-m',
        action='store_true',
        help='Run in manual mode after restart'
    )
    restart_parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help='Set logging level (default: INFO)'
    )
    
    # status command
    status_parser = subparsers.add_parser(
        'status',
        help='Show service status'
    )
    status_parser.add_argument(
        '--format',
        choices=['table', 'json'],
        default='table',
        help='Output format (default: table)'
    )
    
    # Database commands
    db_parser = subparsers.add_parser(
        'db',
        help='Database operations',
        description='Database management and maintenance commands'
    )
    db_subparsers = db_parser.add_subparsers(
        dest='db_command',
        help='Database subcommands'
    )
    
    # db migrate
    migrate_parser = db_subparsers.add_parser(
        'migrate',
        help='Run database schema migration'
    )
    migrate_parser.add_argument(
        '--force',
        action='store_true',
        help='Force migration even if database is unhealthy'
    )
    
    # db health
    health_parser = db_subparsers.add_parser(
        'health',
        help='Check database health'
    )
    health_parser.add_argument(
        '--format',
        choices=['table', 'json'],
        default='table',
        help='Output format (default: table)'
    )
    
    # db reset
    reset_parser = db_subparsers.add_parser(
        'reset',
        help='Reset database (DANGEROUS - deletes all data)'
    )
    reset_parser.add_argument(
        '--confirm',
        action='store_true',
        help='Skip confirmation prompt'
    )
    
    # db backup
    backup_parser = db_subparsers.add_parser(
        'backup',
        help='Create database backup'
    )
    backup_parser.add_argument(
        '--output', '-o',
        type=str,
        help='Output backup file (default: auto-generated)'
    )
    
    # db restore
    restore_parser = db_subparsers.add_parser(
        'restore',
        help='Restore database from backup'
    )
    restore_parser.add_argument(
        'backup_file',
        help='Backup file to restore from'
    )
    restore_parser.add_argument(
        '--force',
        action='store_true',
        help='Skip confirmation prompt'
    )
    
    # Service management commands (future implementation)
    service_mgmt_parser = subparsers.add_parser(
        'service',
        help='System service management',
        description='Manage kick-monitor as system service'
    )
    service_mgmt_subparsers = service_mgmt_parser.add_subparsers(
        dest='service_command',
        help='Service management subcommands'
    )
    
    # service install
    install_parser = service_mgmt_subparsers.add_parser(
        'install',
        help='Install as system service'
    )
    install_parser.add_argument(
        '--user',
        action='store_true',
        help='Install as user service instead of system service'
    )
    
    # service uninstall
    uninstall_parser = service_mgmt_subparsers.add_parser(
        'uninstall',
        help='Uninstall system service'
    )
    
    # service enable
    enable_parser = service_mgmt_subparsers.add_parser(
        'enable',
        help='Enable system service'
    )
    
    # service disable
    disable_parser = service_mgmt_subparsers.add_parser(
        'disable',
        help='Disable system service'
    )
    
    return parser


async def run_command(args: argparse.Namespace) -> int:
    """Run the appropriate command based on parsed arguments."""
    
    # Handle global options
    if args.verbose:
        log_level = logging.DEBUG
    elif args.quiet:
        log_level = logging.WARNING
    else:
        log_level = logging.INFO
    
    # Setup logging
    level_name = logging.getLevelName(log_level)
    setup_logging(level=level_name)
    logger = logging.getLogger('kick-monitor')
    
    try:
        # Route to appropriate command handler
        if args.command == 'config':
            return await run_config_command(args)
        elif args.command == 'streamers':
            return await run_streamers_command(args)
        elif args.command in ['start', 'stop', 'restart', 'status']:
            return await run_service_command(args)
        elif args.command == 'db':
            return await run_database_command(args)
        elif args.command == 'service':
            return await run_service_mgmt_command(args)
        else:
            print("Error: No command specified", file=sys.stderr)
            return 6
    
    except KeyboardInterrupt:
        print("\nOperation cancelled by user", file=sys.stderr)
        return 1
    except Exception as e:
        logger.error(f"Command execution failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1


async def run_config_command(args: argparse.Namespace) -> int:
    """Run configuration commands."""
    commands = ConfigCommands()
    
    if args.config_command == 'show':
        return await commands.show(args)
    elif args.config_command == 'validate':
        return await commands.validate(args)
    elif args.config_command == 'generate-template':
        return await commands.generate_template(args)
    else:
        print("Error: No configuration subcommand specified", file=sys.stderr)
        return 6


async def run_streamers_command(args: argparse.Namespace) -> int:
    """Run streamer management commands."""
    commands = StreamerCommands()
    
    if args.streamers_command == 'list':
        return await commands.list_streamers(args)
    elif args.streamers_command == 'test':
        return await commands.test_streamer(args)
    elif args.streamers_command == 'add':
        return await commands.add_streamer(args)
    else:
        print("Error: No streamers subcommand specified", file=sys.stderr)
        return 6


async def run_service_command(args: argparse.Namespace) -> int:
    """Run service management commands."""
    commands = ServiceCommands()
    
    if args.command == 'start':
        return await commands.start(args)
    elif args.command == 'stop':
        return await commands.stop(args)
    elif args.command == 'restart':
        return await commands.restart(args)
    elif args.command == 'status':
        return await commands.status(args)
    else:
        print("Error: Unknown service command", file=sys.stderr)
        return 6


async def run_database_command(args: argparse.Namespace) -> int:
    """Run database commands."""
    commands = DatabaseCommands()
    
    if args.db_command == 'migrate':
        return await commands.migrate(args)
    elif args.db_command == 'health':
        return await commands.health(args)
    elif args.db_command == 'reset':
        return await commands.reset(args)
    elif args.db_command == 'backup':
        return await commands.backup(args)
    elif args.db_command == 'restore':
        return await commands.restore(args)
    else:
        print("Error: No database subcommand specified", file=sys.stderr)
        return 6


async def run_service_mgmt_command(args: argparse.Namespace) -> int:
    """Run system service management commands (future implementation)."""
    print("System service management commands are not yet implemented.")
    print("For now, manage the service manually or use your system's service manager.")
    print("\nPlanned commands:")
    print("  kick-monitor service install    # Install as systemd service")
    print("  kick-monitor service uninstall  # Remove systemd service")
    print("  kick-monitor service enable     # Enable auto-start")
    print("  kick-monitor service disable    # Disable auto-start")
    return 8


def main() -> int:
    """Main entry point."""
    try:
        # Create parser and parse arguments
        parser = create_parser()
        args = parser.parse_args()
        
        # If no command specified, show help
        if not hasattr(args, 'command') or args.command is None:
            parser.print_help()
            return 0
        
        # Run the command asynchronously
        return asyncio.run(run_command(args))
    
    except KeyboardInterrupt:
        print("\nOperation cancelled", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())