"""
CLI help text and usage examples for the Kick Streamer Monitor.

Provides comprehensive help documentation, usage examples, and command
reference for all CLI commands and features.
"""

from typing import Dict, List, Optional
from rich.console import Console
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.syntax import Syntax


class CLIHelpSystem:
    """Comprehensive help system for CLI commands."""
    
    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
    
    def show_main_help(self) -> None:
        """Display main help page with overview and commands."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold blue]Kick Streamer Status Monitor[/bold blue]\n"
            "Real-time monitoring service for Kick.com streamers",
            style="blue"
        ))
        
        # Quick start
        self.console.print("\n[bold yellow]Quick Start:[/bold yellow]")
        self.console.print("  kick-monitor config validate    # Validate configuration")
        self.console.print("  kick-monitor db migrate         # Setup database")
        self.console.print("  kick-monitor start --manual     # Start with interactive UI")
        
        # Main commands
        self.console.print("\n[bold yellow]Main Commands:[/bold yellow]")
        
        commands_table = Table(show_header=False, box=None, padding=(0, 2))
        commands_table.add_column("Command", style="cyan", no_wrap=True)
        commands_table.add_column("Description")
        
        main_commands = [
            ("start", "Start the monitoring service"),
            ("stop", "Stop the monitoring service"),
            ("status", "Show service status"),
            ("restart", "Restart the monitoring service"),
            ("config", "Configuration management"),
            ("db", "Database management"),
            ("streamers", "Streamer management"),
            ("validate", "Validate system components"),
            ("version", "Show version information"),
            ("help", "Show detailed help for commands")
        ]
        
        for cmd, desc in main_commands:
            commands_table.add_row(f"kick-monitor {cmd}", desc)
        
        self.console.print(commands_table)
        
        # Usage examples
        self.console.print("\n[bold yellow]Common Usage Examples:[/bold yellow]")
        examples = [
            "kick-monitor start --manual                    # Interactive monitoring",
            "kick-monitor start --daemon                    # Background service",
            "kick-monitor streamers add xqc                 # Add streamer to monitor",
            "kick-monitor config show                       # View configuration",
            "kick-monitor db health                         # Check database status"
        ]
        
        for example in examples:
            self.console.print(f"  {example}")
        
        # Get more help
        self.console.print("\n[bold yellow]Get More Help:[/bold yellow]")
        self.console.print("  kick-monitor help <command>     # Detailed command help")
        self.console.print("  kick-monitor --help             # This help message")
        self.console.print()
    
    def show_command_help(self, command: str) -> None:
        """Show detailed help for a specific command."""
        help_methods = {
            "start": self.show_start_help,
            "stop": self.show_stop_help,
            "status": self.show_status_help,
            "restart": self.show_restart_help,
            "config": self.show_config_help,
            "db": self.show_db_help,
            "streamers": self.show_streamers_help,
            "validate": self.show_validate_help,
            "version": self.show_version_help,
            "manual": self.show_manual_mode_help
        }
        
        if command in help_methods:
            help_methods[command]()
        else:
            self.console.print(f"[red]Unknown command: {command}[/red]")
            self.console.print("Use 'kick-monitor help' to see available commands.")
    
    def show_start_help(self) -> None:
        """Show help for the start command."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]kick-monitor start[/bold cyan]\n"
            "Start the Kick streamer monitoring service",
            style="cyan"
        ))
        
        self.console.print("\n[bold yellow]Usage:[/bold yellow]")
        self.console.print("  kick-monitor start [OPTIONS]")
        
        self.console.print("\n[bold yellow]Options:[/bold yellow]")
        options_table = Table(show_header=False, box=None, padding=(0, 2))
        options_table.add_column("Option", style="green", no_wrap=True)
        options_table.add_column("Description")
        
        options = [
            ("--manual", "Start with interactive manual mode UI"),
            ("--daemon", "Start as background daemon service"),
            ("--config FILE", "Use specific configuration file"),
            ("--log-level LEVEL", "Set logging level (DEBUG, INFO, WARNING, ERROR)"),
            ("--pid-file FILE", "Write process ID to file (daemon mode)"),
            ("--no-validate", "Skip configuration validation on startup"),
            ("--dry-run", "Validate configuration and exit"),
            ("--help", "Show this help message")
        ]
        
        for opt, desc in options:
            options_table.add_row(opt, desc)
        
        self.console.print(options_table)
        
        self.console.print("\n[bold yellow]Examples:[/bold yellow]")
        examples = [
            "kick-monitor start --manual                    # Interactive mode",
            "kick-monitor start --daemon                    # Background service",
            "kick-monitor start --config prod.env           # Custom config",
            "kick-monitor start --log-level DEBUG           # Debug logging",
            "kick-monitor start --daemon --pid-file /var/run/kick-monitor.pid"
        ]
        
        for example in examples:
            self.console.print(f"  {example}")
        
        self.console.print("\n[bold yellow]Manual Mode Features:[/bold yellow]")
        manual_features = [
            "Real-time streamer status display",
            "Color-coded status indicators",
            "Sortable columns and live updates",
            "Keyboard shortcuts for interaction",
            "Connection status and statistics"
        ]
        
        for feature in manual_features:
            self.console.print(f"  • {feature}")
        
        self.console.print()
    
    def show_config_help(self) -> None:
        """Show help for config commands."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]kick-monitor config[/bold cyan]\n"
            "Configuration management commands",
            style="cyan"
        ))
        
        self.console.print("\n[bold yellow]Subcommands:[/bold yellow]")
        
        subcommands_table = Table(show_header=False, box=None, padding=(0, 2))
        subcommands_table.add_column("Command", style="green", no_wrap=True)
        subcommands_table.add_column("Description")
        
        subcommands = [
            ("show", "Display current configuration settings"),
            ("validate", "Validate configuration and show any errors"),
            ("generate-template", "Generate .env.example template file"),
            ("test", "Test configuration with live services"),
            ("encrypt", "Encrypt sensitive configuration values"),
            ("decrypt", "Decrypt configuration values for viewing"),
            ("export", "Export configuration to different formats"),
            ("import", "Import configuration from file")
        ]
        
        for cmd, desc in subcommands:
            subcommands_table.add_row(f"config {cmd}", desc)
        
        self.console.print(subcommands_table)
        
        self.console.print("\n[bold yellow]Examples:[/bold yellow]")
        examples = [
            "kick-monitor config show                       # View all settings",
            "kick-monitor config show --category auth       # Show auth settings only",
            "kick-monitor config validate                   # Check configuration",
            "kick-monitor config validate --strict          # Strict validation",
            "kick-monitor config generate-template          # Create .env.example",
            "kick-monitor config test --api                 # Test API connections",
            "kick-monitor config export --format json       # Export as JSON"
        ]
        
        for example in examples:
            self.console.print(f"  {example}")
        
        self.console.print("\n[bold yellow]Configuration Categories:[/bold yellow]")
        categories = [
            ("auth", "Kick.com API authentication settings"),
            ("database", "PostgreSQL database connection"),
            ("monitoring", "Monitoring behavior and intervals"),
            ("logging", "Application logging configuration"),
            ("websocket", "WebSocket connection settings"),
            ("notifications", "Alert and notification settings")
        ]
        
        for cat, desc in categories:
            self.console.print(f"  [green]{cat:12}[/green] {desc}")
        
        self.console.print()
    
    def show_db_help(self) -> None:
        """Show help for database commands."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]kick-monitor db[/bold cyan]\n"
            "Database management and operations",
            style="cyan"
        ))
        
        self.console.print("\n[bold yellow]Subcommands:[/bold yellow]")
        
        subcommands_table = Table(show_header=False, box=None, padding=(0, 2))
        subcommands_table.add_column("Command", style="green", no_wrap=True)
        subcommands_table.add_column("Description")
        
        subcommands = [
            ("migrate", "Run database migrations to create/update schema"),
            ("health", "Check database connection and status"),
            ("backup", "Create database backup"),
            ("restore", "Restore database from backup"),
            ("reset", "Reset database (WARNING: destroys all data)"),
            ("stats", "Show database statistics and performance"),
            ("cleanup", "Clean up old data based on retention policies"),
            ("vacuum", "Optimize database performance")
        ]
        
        for cmd, desc in subcommands:
            subcommands_table.add_row(f"db {cmd}", desc)
        
        self.console.print(subcommands_table)
        
        self.console.print("\n[bold yellow]Examples:[/bold yellow]")
        examples = [
            "kick-monitor db migrate                        # Initialize database",
            "kick-monitor db health                         # Check connection",
            "kick-monitor db backup --file backup.sql       # Create backup",
            "kick-monitor db restore --file backup.sql      # Restore backup",
            "kick-monitor db stats                          # Show statistics",
            "kick-monitor db cleanup --days 30              # Remove old data"
        ]
        
        for example in examples:
            self.console.print(f"  {example}")
        
        self.console.print("\n[bold yellow]Database Setup:[/bold yellow]")
        setup_steps = [
            "1. Install PostgreSQL 12+ on your system",
            "2. Create database: CREATE DATABASE kick_monitor;",
            "3. Create user with permissions",
            "4. Configure DATABASE_* environment variables",
            "5. Run: kick-monitor db migrate"
        ]
        
        for step in setup_steps:
            self.console.print(f"  {step}")
        
        self.console.print()
    
    def show_streamers_help(self) -> None:
        """Show help for streamer management commands."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]kick-monitor streamers[/bold cyan]\n"
            "Manage monitored Kick.com streamers",
            style="cyan"
        ))
        
        self.console.print("\n[bold yellow]Subcommands:[/bold yellow]")
        
        subcommands_table = Table(show_header=False, box=None, padding=(0, 2))
        subcommands_table.add_column("Command", style="green", no_wrap=True)
        subcommands_table.add_column("Description")
        
        subcommands = [
            ("list", "List all monitored streamers"),
            ("add USERNAME", "Add streamer to monitoring"),
            ("remove USERNAME", "Remove streamer from monitoring"),
            ("test USERNAME", "Test API access for streamer"),
            ("status USERNAME", "Get current status of streamer"),
            ("history USERNAME", "Show status change history"),
            ("import FILE", "Import streamers from file"),
            ("export FILE", "Export streamers to file"),
            ("refresh", "Refresh all streamer information")
        ]
        
        for cmd, desc in subcommands:
            subcommands_table.add_row(f"streamers {cmd}", desc)
        
        self.console.print(subcommands_table)
        
        self.console.print("\n[bold yellow]Examples:[/bold yellow]")
        examples = [
            "kick-monitor streamers list                    # Show all streamers",
            "kick-monitor streamers add xqc                 # Add XQC to monitoring",
            "kick-monitor streamers remove trainwreckstv    # Remove from monitoring",
            "kick-monitor streamers test sodapoppin         # Test API access",
            "kick-monitor streamers status xqc              # Check current status",
            "kick-monitor streamers history xqc --days 7    # Last 7 days history",
            "kick-monitor streamers import streamers.txt    # Import from file",
            "kick-monitor streamers export --format json    # Export as JSON"
        ]
        
        for example in examples:
            self.console.print(f"  {example}")
        
        self.console.print("\n[bold yellow]Import/Export Formats:[/bold yellow]")
        formats = [
            ("txt", "Plain text file with one username per line"),
            ("csv", "CSV format with username,display_name columns"),
            ("json", "JSON format with full streamer objects")
        ]
        
        for fmt, desc in formats:
            self.console.print(f"  [green]{fmt:6}[/green] {desc}")
        
        self.console.print()
    
    def show_manual_mode_help(self) -> None:
        """Show help for manual mode interface."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]Manual Mode Interface[/bold cyan]\n"
            "Interactive real-time monitoring console",
            style="cyan"
        ))
        
        self.console.print("\n[bold yellow]Starting Manual Mode:[/bold yellow]")
        self.console.print("  kick-monitor start --manual")
        
        self.console.print("\n[bold yellow]Interface Features:[/bold yellow]")
        features = [
            "Real-time streamer status updates",
            "Color-coded status indicators",
            "Sortable data columns",
            "Connection status display",
            "Live statistics and metrics",
            "Keyboard shortcuts for control"
        ]
        
        for feature in features:
            self.console.print(f"  • {feature}")
        
        self.console.print("\n[bold yellow]Keyboard Shortcuts:[/bold yellow]")
        
        shortcuts_table = Table(show_header=False, box=None, padding=(0, 2))
        shortcuts_table.add_column("Key", style="green", no_wrap=True)
        shortcuts_table.add_column("Action")
        
        shortcuts = [
            ("q", "Quit application"),
            ("r", "Refresh data immediately"),
            ("s", "Sort by status (online/offline/unknown)"),
            ("u", "Sort by username (alphabetical)"),
            ("t", "Sort by last update time"),
            ("c", "Clear screen and reset display"),
            ("h", "Show help overlay"),
            ("p", "Pause/resume automatic updates"),
            ("f", "Toggle full-screen mode"),
            ("1-9", "Change refresh rate (1=fastest, 9=slowest)")
        ]
        
        for key, action in shortcuts:
            shortcuts_table.add_row(key, action)
        
        self.console.print(shortcuts_table)
        
        self.console.print("\n[bold yellow]Status Indicators:[/bold yellow]")
        self.console.print("  🟢 [green]Online[/green]   - Streamer is currently live")
        self.console.print("  🔴 [red]Offline[/red]  - Streamer is not streaming")
        self.console.print("  🟡 [yellow]Unknown[/yellow]  - Status not yet determined")
        self.console.print("  ⚠️  [orange1]Error[/orange1]    - Connection or API error")
        
        self.console.print("\n[bold yellow]Display Columns:[/bold yellow]")
        columns = [
            ("Username", "Kick.com username"),
            ("Status", "Current streaming status"),
            ("Last Seen", "Last time seen online"),
            ("Last Update", "Most recent status change"),
            ("Updates", "Total number of status changes"),
            ("Viewers", "Current viewer count (if online)")
        ]
        
        for col, desc in columns:
            self.console.print(f"  [green]{col:12}[/green] {desc}")
        
        self.console.print()
    
    def show_troubleshooting_help(self) -> None:
        """Show troubleshooting guide."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]Troubleshooting Guide[/bold cyan]\n"
            "Common issues and solutions",
            style="cyan"
        ))
        
        self.console.print("\n[bold yellow]Common Issues:[/bold yellow]")
        
        issues = [
            {
                "title": "Database Connection Failed",
                "symptoms": ["Connection refused", "Authentication failed", "Database not found"],
                "solutions": [
                    "Check PostgreSQL is running: sudo systemctl status postgresql",
                    "Verify database exists: psql -l",
                    "Test connection: kick-monitor db health",
                    "Check environment variables in .env file"
                ]
            },
            {
                "title": "API Authentication Errors",
                "symptoms": ["401 Unauthorized", "Invalid client credentials", "Token expired"],
                "solutions": [
                    "Verify KICK_CLIENT_ID and KICK_CLIENT_SECRET in .env",
                    "Check API credentials at https://dev.kick.com/",
                    "Test authentication: kick-monitor config test --api",
                    "Ensure application has correct permissions"
                ]
            },
            {
                "title": "WebSocket Connection Issues",
                "symptoms": ["Connection timeout", "Pusher errors", "No real-time updates"],
                "solutions": [
                    "Check network connectivity to ws-us2.pusher.com",
                    "Verify firewall allows WebSocket connections",
                    "Check Pusher configuration: kick-monitor config show",
                    "Monitor logs: tail -f logs/kick-monitor.log"
                ]
            },
            {
                "title": "Performance Issues",
                "symptoms": ["High memory usage", "Slow updates", "CPU spikes"],
                "solutions": [
                    "Check database performance: kick-monitor db stats",
                    "Optimize PostgreSQL configuration",
                    "Adjust monitoring intervals in configuration",
                    "Monitor system resources: htop"
                ]
            }
        ]
        
        for issue in issues:
            self.console.print(f"\n[bold red]{issue['title']}[/bold red]")
            
            self.console.print("  [yellow]Symptoms:[/yellow]")
            for symptom in issue['symptoms']:
                self.console.print(f"    • {symptom}")
            
            self.console.print("  [yellow]Solutions:[/yellow]")
            for solution in issue['solutions']:
                self.console.print(f"    • {solution}")
        
        self.console.print("\n[bold yellow]Diagnostic Commands:[/bold yellow]")
        diagnostics = [
            ("kick-monitor config validate", "Check configuration"),
            ("kick-monitor db health", "Test database connection"),
            ("kick-monitor config test", "Test all external services"),
            ("kick-monitor status", "Show service status"),
            ("kick-monitor --version", "Show version information")
        ]
        
        for cmd, desc in diagnostics:
            self.console.print(f"  [green]{cmd:35}[/green] {desc}")
        
        self.console.print("\n[bold yellow]Log Files:[/bold yellow]")
        self.console.print("  Default location: logs/kick-monitor.log")
        self.console.print("  Systemd logs: journalctl -u kick-monitor.service")
        self.console.print("  Debug logging: SYSTEM_LOG_LEVEL=DEBUG")
        
        self.console.print()
    
    def show_api_examples(self) -> None:
        """Show API integration examples."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]API Integration Examples[/bold cyan]\n"
            "Using the monitoring service programmatically",
            style="cyan"
        ))
        
        # Python example
        python_code = '''from src.services.monitor import MonitoringService
from src.lib.config import ConfigurationManager

# Initialize monitoring service
config = ConfigurationManager()
service = MonitoringService(config)

# Start monitoring
await service.start()

# Add streamers to monitor
await service.add_streamer("xqc")
await service.add_streamer("trainwreckstv")

# Get streamer status
status = await service.get_streamer_status("xqc")
print(f"XQC is {status}")

# Listen for status changes
def on_status_change(streamer, old_status, new_status):
    print(f"{streamer} changed from {old_status} to {new_status}")

service.add_status_listener(on_status_change)'''
        
        self.console.print("\n[bold yellow]Python Integration:[/bold yellow]")
        syntax = Syntax(python_code, "python", theme="monokai", line_numbers=True)
        self.console.print(syntax)
        
        # REST API examples
        self.console.print("\n[bold yellow]REST API Examples:[/bold yellow]")
        
        api_examples = [
            ("GET /health", "Check service health"),
            ("GET /streamers", "List all monitored streamers"),
            ("GET /streamers/{username}/status", "Get specific streamer status"),
            ("GET /streamers/{username}/history", "Get status history"),
            ("POST /streamers", "Add new streamer to monitoring"),
            ("DELETE /streamers/{username}", "Remove streamer"),
            ("GET /metrics", "Get performance metrics")
        ]
        
        for endpoint, desc in api_examples:
            self.console.print(f"  [green]{endpoint:35}[/green] {desc}")
        
        # WebSocket example
        websocket_code = '''import asyncio
import websockets
import json

async def monitor_events():
    uri = "ws://localhost:8080/ws"
    async with websockets.connect(uri) as websocket:
        # Subscribe to streamer events
        await websocket.send(json.dumps({
            "action": "subscribe",
            "streamers": ["xqc", "trainwreckstv"]
        }))
        
        # Listen for events
        async for message in websocket:
            event = json.loads(message)
            print(f"Event: {event}")

# Run the monitor
asyncio.run(monitor_events())'''
        
        self.console.print("\n[bold yellow]WebSocket Integration:[/bold yellow]")
        syntax = Syntax(websocket_code, "python", theme="monokai", line_numbers=True)
        self.console.print(syntax)
        
        self.console.print()
    
    def show_version_help(self) -> None:
        """Show version and system information."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]kick-monitor version[/bold cyan]\n"
            "Display version and system information",
            style="cyan"
        ))
        
        self.console.print("\n[bold yellow]Usage:[/bold yellow]")
        self.console.print("  kick-monitor version [OPTIONS]")
        
        self.console.print("\n[bold yellow]Options:[/bold yellow]")
        options = [
            ("--detailed", "Show detailed system information"),
            ("--json", "Output in JSON format"),
            ("--check-updates", "Check for available updates")
        ]
        
        for opt, desc in options:
            self.console.print(f"  [green]{opt:15}[/green] {desc}")
        
        self.console.print("\n[bold yellow]Information Displayed:[/bold yellow]")
        info_items = [
            "Application version",
            "Python version",
            "Operating system",
            "Dependencies versions",
            "Configuration file locations",
            "Database schema version"
        ]
        
        for item in info_items:
            self.console.print(f"  • {item}")
        
        self.console.print()
    
    def show_validate_help(self) -> None:
        """Show validation command help."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]kick-monitor validate[/bold cyan]\n"
            "Validate system configuration and connectivity",
            style="cyan"
        ))
        
        self.console.print("\n[bold yellow]Usage:[/bold yellow]")
        self.console.print("  kick-monitor validate [OPTIONS]")
        
        self.console.print("\n[bold yellow]Options:[/bold yellow]")
        options = [
            ("--config-only", "Validate configuration files only"),
            ("--database", "Test database connectivity"),
            ("--api", "Test Kick.com API connectivity"),
            ("--websocket", "Test WebSocket connectivity"),
            ("--all", "Run all validation tests (default)"),
            ("--fix", "Attempt to fix common issues"),
            ("--report", "Generate detailed validation report")
        ]
        
        for opt, desc in options:
            options_table = Table(show_header=False, box=None, padding=(0, 2))
            self.console.print(f"  [green]{opt:15}[/green] {desc}")
        
        self.console.print("\n[bold yellow]Validation Tests:[/bold yellow]")
        tests = [
            "Configuration file syntax and values",
            "Required environment variables",
            "Database connection and schema",
            "Kick.com API authentication",
            "WebSocket connectivity to Pusher",
            "File permissions and directories",
            "Network connectivity",
            "System resource availability"
        ]
        
        for test in tests:
            self.console.print(f"  • {test}")
        
        self.console.print("\n[bold yellow]Exit Codes:[/bold yellow]")
        exit_codes = [
            ("0", "All validations passed"),
            ("1", "Configuration errors found"),
            ("2", "Database connectivity issues"),
            ("3", "API authentication failures"),
            ("4", "Network connectivity problems"),
            ("5", "System resource issues")
        ]
        
        for code, desc in exit_codes:
            self.console.print(f"  [green]{code:3}[/green] {desc}")
        
        self.console.print()
    
    def show_stop_help(self) -> None:
        """Show stop command help."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]kick-monitor stop[/bold cyan]\n"
            "Stop the monitoring service gracefully",
            style="cyan"
        ))
        
        self.console.print("\n[bold yellow]Usage:[/bold yellow]")
        self.console.print("  kick-monitor stop [OPTIONS]")
        
        self.console.print("\n[bold yellow]Options:[/bold yellow]")
        options = [
            ("--force", "Force stop without graceful shutdown"),
            ("--timeout SECONDS", "Timeout for graceful shutdown (default: 30)"),
            ("--save-state", "Save current state before stopping"),
            ("--pid-file FILE", "Use specific PID file")
        ]
        
        for opt, desc in options:
            self.console.print(f"  [green]{opt:20}[/green] {desc}")
        
        self.console.print("\n[bold yellow]Shutdown Process:[/bold yellow]")
        process_steps = [
            "1. Send graceful shutdown signal",
            "2. Close WebSocket connections",
            "3. Finish processing queued events",
            "4. Close database connections",
            "5. Save final state",
            "6. Exit process"
        ]
        
        for step in process_steps:
            self.console.print(f"  {step}")
        
        self.console.print()
    
    def show_status_help(self) -> None:
        """Show status command help."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]kick-monitor status[/bold cyan]\n"
            "Display current service status and statistics",
            style="cyan"
        ))
        
        self.console.print("\n[bold yellow]Usage:[/bold yellow]")
        self.console.print("  kick-monitor status [OPTIONS]")
        
        self.console.print("\n[bold yellow]Options:[/bold yellow]")
        options = [
            ("--detailed", "Show detailed status information"),
            ("--json", "Output in JSON format"),
            ("--watch", "Continuously monitor status"),
            ("--refresh SECONDS", "Refresh interval for watch mode")
        ]
        
        for opt, desc in options:
            self.console.print(f"  [green]{opt:20}[/green] {desc}")
        
        self.console.print("\n[bold yellow]Status Information:[/bold yellow]")
        status_info = [
            "Service running state",
            "Uptime and start time",
            "Number of monitored streamers",
            "WebSocket connection status",
            "Database connection health",
            "Recent error count",
            "Performance metrics",
            "Memory and CPU usage"
        ]
        
        for info in status_info:
            self.console.print(f"  • {info}")
        
        self.console.print()
    
    def show_restart_help(self) -> None:
        """Show restart command help."""
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]kick-monitor restart[/bold cyan]\n"
            "Restart the monitoring service",
            style="cyan"
        ))
        
        self.console.print("\n[bold yellow]Usage:[/bold yellow]")
        self.console.print("  kick-monitor restart [OPTIONS]")
        
        self.console.print("\n[bold yellow]Options:[/bold yellow]")
        options = [
            ("--force", "Force restart without graceful shutdown"),
            ("--config FILE", "Use different configuration on restart"),
            ("--upgrade", "Restart with upgraded binary"),
            ("--backup", "Create backup before restart")
        ]
        
        for opt, desc in options:
            self.console.print(f"  [green]{opt:15}[/green] {desc}")
        
        self.console.print("\n[bold yellow]Restart Process:[/bold yellow]")
        process_steps = [
            "1. Validate new configuration",
            "2. Stop current service gracefully",
            "3. Wait for clean shutdown",
            "4. Start service with new settings",
            "5. Verify successful startup"
        ]
        
        for step in process_steps:
            self.console.print(f"  {step}")
        
        self.console.print()


def get_command_examples() -> Dict[str, List[str]]:
    """Get comprehensive command examples for all CLI commands."""
    return {
        "start": [
            "kick-monitor start --manual",
            "kick-monitor start --daemon",
            "kick-monitor start --config /etc/kick-monitor/.env",
            "kick-monitor start --log-level DEBUG --no-validate",
            "kick-monitor start --daemon --pid-file /var/run/kick-monitor.pid"
        ],
        "config": [
            "kick-monitor config show",
            "kick-monitor config show --category auth",
            "kick-monitor config validate",
            "kick-monitor config validate --strict",
            "kick-monitor config generate-template",
            "kick-monitor config test --api --database",
            "kick-monitor config export --format json --file config.json"
        ],
        "db": [
            "kick-monitor db migrate",
            "kick-monitor db health",
            "kick-monitor db backup --file backup-$(date +%Y%m%d).sql",
            "kick-monitor db restore --file backup.sql",
            "kick-monitor db stats --detailed",
            "kick-monitor db cleanup --days 30"
        ],
        "streamers": [
            "kick-monitor streamers list",
            "kick-monitor streamers list --status online",
            "kick-monitor streamers add xqc",
            "kick-monitor streamers remove trainwreckstv",
            "kick-monitor streamers test sodapoppin",
            "kick-monitor streamers status xqc",
            "kick-monitor streamers history xqc --days 7",
            "kick-monitor streamers import streamers.txt",
            "kick-monitor streamers export --format csv --file streamers.csv"
        ],
        "validate": [
            "kick-monitor validate",
            "kick-monitor validate --config-only",
            "kick-monitor validate --database --api",
            "kick-monitor validate --fix",
            "kick-monitor validate --report --file validation-report.json"
        ],
        "general": [
            "kick-monitor --help",
            "kick-monitor --version",
            "kick-monitor status --watch",
            "kick-monitor stop --timeout 60",
            "kick-monitor restart --backup"
        ]
    }


def get_keyboard_shortcuts() -> Dict[str, str]:
    """Get manual mode keyboard shortcuts."""
    return {
        "q": "Quit application",
        "r": "Refresh data immediately", 
        "s": "Sort by status",
        "u": "Sort by username",
        "t": "Sort by last update time",
        "c": "Clear screen",
        "h": "Show help overlay",
        "p": "Pause/resume updates",
        "f": "Toggle fullscreen",
        "1-9": "Change refresh rate"
    }


def show_help(command: Optional[str] = None, console: Optional[Console] = None) -> None:
    """Show help for specific command or general help."""
    help_system = CLIHelpSystem(console)
    
    if command:
        help_system.show_command_help(command)
    else:
        help_system.show_main_help()


# Export for CLI usage
__all__ = [
    'CLIHelpSystem',
    'show_help',
    'get_command_examples',
    'get_keyboard_shortcuts'
]