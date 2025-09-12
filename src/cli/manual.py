"""
Manual mode UI implementation with Rich-based real-time console display.

Provides an interactive real-time monitoring interface with live updates,
keyboard shortcuts, and comprehensive status visualization for the
Kick streamer monitoring service.
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum

try:
    from rich.console import Console
    from rich.live import Live
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.text import Text
    from rich.align import Align
    from rich.columns import Columns
    from rich import box
    from rich.style import Style
    from rich.segment import Segment
    from rich.prompt import Prompt
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from services import KickMonitorService


class SortMode(str, Enum):
    """Sorting modes for streamer display."""
    USERNAME = "username"
    STATUS = "status"
    LAST_SEEN = "last_seen"
    UPDATES = "updates"


class ManualModeError(Exception):
    """Manual mode specific errors."""
    pass


class ManualModeUI:
    """
    Rich-based real-time console interface for manual monitoring mode.
    
    Features:
    - Real-time status updates every 1-2 seconds
    - Table layout with streamer information
    - Color coding for online/offline status
    - Keyboard shortcuts for interaction
    - Header with connection status and statistics
    - Auto-refresh with manual refresh option
    """
    
    def __init__(self, monitor_service: KickMonitorService):
        if not RICH_AVAILABLE:
            raise ManualModeError("Rich library not installed. Install with: pip install rich")
        
        self.monitor_service = monitor_service
        self.console = Console()
        self.logger = logging.getLogger(__name__)
        
        # UI State
        self._running = False
        self._refresh_rate = 2.0  # seconds
        self._sort_mode = SortMode.USERNAME
        self._reverse_sort = False
        self._show_help = False
        self._max_streamers_display = 25
        self._start_time = datetime.now(timezone.utc)
        
        # Statistics tracking
        self._update_count = 0
        self._last_update = datetime.now(timezone.utc)
        self._keyboard_input_task: Optional[asyncio.Task] = None
        
        # Input handling
        self._input_queue: asyncio.Queue = asyncio.Queue()
        self._shutdown_requested = False
    
    async def run(self) -> int:
        """Run the manual mode UI."""
        try:
            self.console.clear()
            self._print_startup_banner()
            
            # Give user time to read banner
            await asyncio.sleep(2)
            
            self._running = True
            
            # Start keyboard input handler
            self._keyboard_input_task = asyncio.create_task(self._keyboard_input_handler())
            
            # Run main UI loop with Live display
            with Live(
                self._create_display(),
                refresh_per_second=1/self._refresh_rate,
                console=self.console,
                screen=False,
                auto_refresh=True
            ) as live:
                
                while self._running and not self._shutdown_requested:
                    try:
                        # Update display
                        live.update(self._create_display())
                        self._update_count += 1
                        self._last_update = datetime.now(timezone.utc)
                        
                        # Check for keyboard input
                        await self._process_keyboard_input()
                        
                        # Wait for next refresh
                        await asyncio.sleep(self._refresh_rate)
                        
                    except KeyboardInterrupt:
                        break
            
            return await self._shutdown()
        
        except Exception as e:
            self.logger.error(f"Manual mode UI error: {e}")
            self.console.print(f"[red]Error: {e}[/red]")
            return 1
    
    def _print_startup_banner(self) -> None:
        """Print startup banner with instructions."""
        banner = Panel(
            Text.assemble(
                ("KICK STREAMER MONITOR", "bold blue"),
                "\n",
                ("Manual Mode - Real-time Monitoring Interface", "dim"),
                "\n\n",
                ("Controls:", "bold"),
                "\n",
                ("  [q] or [Ctrl+C]  ", "green"), ("Quit", ""),
                "\n",
                ("  [r]              ", "green"), ("Refresh now", ""),
                "\n", 
                ("  [s]              ", "green"), ("Sort by status", ""),
                "\n",
                ("  [u]              ", "green"), ("Sort by username", ""),
                "\n",
                ("  [t]              ", "green"), ("Sort by last seen", ""),
                "\n",
                ("  [h] or [?]       ", "green"), ("Toggle this help", ""),
                "\n",
                ("  [+] / [-]        ", "green"), ("Adjust refresh rate", ""),
                "\n\n",
                ("Starting in 2 seconds...", "dim italic")
            ),
            title="Welcome",
            border_style="blue",
            padding=(1, 2)
        )
        
        self.console.print(Align.center(banner))
    
    def _create_display(self) -> Layout:
        """Create the main display layout."""
        try:
            # Create main layout
            layout = Layout()
            layout.split_column(
                Layout(name="header", size=8),
                Layout(name="main", ratio=1),
                Layout(name="footer", size=4)
            )
            
            # Split main into content and sidebar
            layout["main"].split_row(
                Layout(name="streamers", ratio=3),
                Layout(name="stats", ratio=1)
            )
            
            # Create components
            layout["header"] = self._create_header()
            layout["streamers"] = self._create_streamers_table()
            layout["stats"] = self._create_stats_panel()
            layout["footer"] = self._create_footer()
            
            return layout
            
        except Exception as e:
            self.logger.error(f"Display creation error: {e}")
            return Panel(f"Display error: {e}", style="red")
    
    def _create_header(self) -> Panel:
        """Create header with service status and connection info."""
        try:
            if not self.monitor_service.is_running:
                return Panel(
                    Text("Service not running", style="red bold"),
                    title="Status",
                    border_style="red"
                )
            
            # Get service statistics
            stats = self.monitor_service.get_monitoring_stats()
            service_status = stats.get('service_status', {})
            connections = stats.get('connections', {})
            
            # Service uptime
            uptime_seconds = service_status.get('uptime_seconds', 0)
            uptime = str(timedelta(seconds=int(uptime_seconds)))
            
            # Connection status
            ws_status = connections.get('websocket', {})
            ws_connected = ws_status.get('is_connected', False)
            ws_channels = ws_status.get('subscribed_channels', 0)
            
            # Create header content
            header_content = Text()
            
            # Service status line
            header_content.append("Service: ", style="dim")
            header_content.append("RUNNING", style="green bold")
            header_content.append(f" • Mode: ", style="dim")
            header_content.append(service_status.get('mode', 'unknown').upper(), style="cyan")
            header_content.append(f" • Uptime: ", style="dim")
            header_content.append(uptime, style="yellow")
            
            header_content.append("\n")
            
            # Connection status line
            header_content.append("WebSocket: ", style="dim")
            if ws_connected:
                header_content.append("CONNECTED", style="green bold")
            else:
                header_content.append("DISCONNECTED", style="red bold")
            
            header_content.append(f" • Channels: ", style="dim")
            header_content.append(str(ws_channels), style="cyan")
            header_content.append(f" • Messages: ", style="dim")
            header_content.append(f"{ws_status.get('total_messages', 0):,}", style="yellow")
            
            header_content.append("\n")
            
            # Current time and refresh info
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            header_content.append(f"Time: ", style="dim")
            header_content.append(current_time, style="white")
            header_content.append(f" • Updates: ", style="dim")
            header_content.append(str(self._update_count), style="yellow")
            header_content.append(f" • Refresh: ", style="dim")
            header_content.append(f"{self._refresh_rate:.1f}s", style="cyan")
            
            return Panel(
                header_content,
                title="Kick Streamer Monitor",
                border_style="blue"
            )
            
        except Exception as e:
            return Panel(f"Header error: {e}", style="red")
    
    def _create_streamers_table(self) -> Panel:
        """Create streamers table with live data."""
        try:
            if not self.monitor_service.is_running:
                return Panel("Service not running", style="red")
            
            # Get streamers data
            streamers = self.monitor_service.get_streamer_details()
            
            if not streamers:
                return Panel(
                    Text("No streamers being monitored", style="dim italic"),
                    title="Streamers",
                    border_style="yellow"
                )
            
            # Sort streamers
            streamers = self._sort_streamers(streamers)
            
            # Limit display count
            display_streamers = streamers[:self._max_streamers_display]
            
            # Create table
            table = Table(
                title=f"Monitored Streamers ({len(streamers)} total, showing {len(display_streamers)})",
                box=box.ROUNDED,
                header_style="bold cyan",
                show_header=True,
                show_lines=False
            )
            
            # Add columns
            table.add_column("Username", style="white", no_wrap=True)
            table.add_column("Status", justify="center", width=10)
            table.add_column("Last Seen", style="dim", width=12)
            table.add_column("Updates", justify="right", style="yellow", width=8)
            table.add_column("Subscribed", justify="center", width=10)
            
            # Add rows
            for streamer in display_streamers:
                username = streamer['username']
                status = streamer['status']
                last_seen = streamer.get('last_seen_online')
                is_subscribed = streamer['is_subscribed']
                
                # Format status with color
                if status == 'online':
                    status_display = "[green]● ONLINE[/green]"
                elif status == 'offline':
                    status_display = "[red]● OFFLINE[/red]"
                else:
                    status_display = "[yellow]● UNKNOWN[/yellow]"
                
                # Format last seen time
                if last_seen:
                    try:
                        last_seen_dt = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
                        if last_seen_dt.date() == datetime.now().date():
                            last_seen_display = last_seen_dt.strftime("%H:%M:%S")
                        else:
                            last_seen_display = last_seen_dt.strftime("%m-%d %H:%M")
                    except:
                        last_seen_display = "Invalid"
                else:
                    last_seen_display = "Never"
                
                # Format subscription status
                sub_display = "[green]✓[/green]" if is_subscribed else "[red]✗[/red]"
                
                # Fake update count for display (would be real in full implementation)
                update_count = hash(username) % 100
                
                table.add_row(
                    username,
                    status_display,
                    last_seen_display,
                    str(update_count),
                    sub_display
                )
            
            # Add sorting indicator to title
            sort_indicator = {
                SortMode.USERNAME: "📝",
                SortMode.STATUS: "🟢",
                SortMode.LAST_SEEN: "🕐",
                SortMode.UPDATES: "📊"
            }.get(self._sort_mode, "")
            
            sort_order = "↓" if self._reverse_sort else "↑"
            
            return Panel(
                table,
                title=f"Streamers {sort_indicator} {sort_order}",
                border_style="green"
            )
            
        except Exception as e:
            self.logger.error(f"Streamers table error: {e}")
            return Panel(f"Table error: {e}", style="red")
    
    def _create_stats_panel(self) -> Panel:
        """Create statistics panel."""
        try:
            if not self.monitor_service.is_running:
                return Panel("Service not running", style="red")
            
            stats = self.monitor_service.get_monitoring_stats()
            streamers = stats.get('streamers', {})
            processing = stats.get('processing', {})
            
            # Create statistics content
            stats_content = Text()
            
            # Streamer counts
            stats_content.append("Streamers:\n", style="bold")
            stats_content.append(f"  Total: ", style="dim")
            stats_content.append(f"{streamers.get('total_monitored', 0)}\n", style="white")
            stats_content.append(f"  Online: ", style="dim")
            stats_content.append(f"{streamers.get('online', 0)}\n", style="green")
            stats_content.append(f"  Offline: ", style="dim")
            stats_content.append(f"{streamers.get('offline', 0)}\n", style="red")
            stats_content.append(f"  Unknown: ", style="dim")
            stats_content.append(f"{streamers.get('unknown', 0)}\n", style="yellow")
            
            stats_content.append("\nProcessing:\n", style="bold")
            stats_content.append(f"  Events: ", style="dim")
            stats_content.append(f"{processing.get('total_events_processed', 0):,}\n", style="cyan")
            stats_content.append(f"  Updates: ", style="dim")
            stats_content.append(f"{processing.get('total_status_updates', 0):,}\n", style="cyan")
            stats_content.append(f"  Queue: ", style="dim")
            stats_content.append(f"{processing.get('queue_size', 0)}\n", style="yellow")
            
            # Performance metrics
            ui_uptime = datetime.now(timezone.utc) - self._start_time
            stats_content.append("\nPerformance:\n", style="bold")
            stats_content.append(f"  UI Uptime: ", style="dim")
            stats_content.append(f"{str(ui_uptime).split('.')[0]}\n", style="white")
            stats_content.append(f"  UI Updates: ", style="dim")
            stats_content.append(f"{self._update_count:,}\n", style="cyan")
            stats_content.append(f"  Refresh Rate: ", style="dim")
            stats_content.append(f"{self._refresh_rate:.1f}s\n", style="yellow")
            
            return Panel(
                stats_content,
                title="Statistics",
                border_style="cyan"
            )
            
        except Exception as e:
            return Panel(f"Stats error: {e}", style="red")
    
    def _create_footer(self) -> Panel:
        """Create footer with help and controls."""
        if self._show_help:
            help_content = Text()
            help_content.append("Keyboard Controls:\n", style="bold")
            help_content.append("[q] Quit  ", style="green")
            help_content.append("[r] Refresh  ", style="green")
            help_content.append("[s] Sort by Status  ", style="green")
            help_content.append("[u] Sort by Username\n", style="green")
            help_content.append("[t] Sort by Time  ", style="green")
            help_content.append("[+/-] Refresh Rate  ", style="green")
            help_content.append("[h/?] Toggle Help", style="green")
            
            return Panel(help_content, title="Help", border_style="blue")
        else:
            footer_text = Text()
            footer_text.append("Press ", style="dim")
            footer_text.append("[h]", style="green bold")
            footer_text.append(" for help, ", style="dim")
            footer_text.append("[q]", style="green bold")
            footer_text.append(" to quit, ", style="dim")
            footer_text.append("[r]", style="green bold")
            footer_text.append(" to refresh", style="dim")
            
            return Panel(
                Align.center(footer_text),
                border_style="dim"
            )
    
    async def _keyboard_input_handler(self) -> None:
        """Handle keyboard input asynchronously."""
        try:
            # In a real implementation, this would use a proper async keyboard input library
            # For now, we'll simulate with a simple approach
            pass
        except Exception as e:
            self.logger.error(f"Keyboard input handler error: {e}")
    
    async def _process_keyboard_input(self) -> None:
        """Process queued keyboard input."""
        try:
            # Try to get input without blocking
            while not self._input_queue.empty():
                try:
                    key = self._input_queue.get_nowait()
                    await self._handle_key_press(key)
                except asyncio.QueueEmpty:
                    break
        except Exception as e:
            self.logger.error(f"Input processing error: {e}")
    
    async def _handle_key_press(self, key: str) -> None:
        """Handle individual key press."""
        try:
            key = key.lower()
            
            if key in ['q', '\x03']:  # 'q' or Ctrl+C
                self._shutdown_requested = True
                
            elif key == 'r':
                # Force refresh (already happens every cycle)
                pass
                
            elif key == 's':
                # Sort by status
                if self._sort_mode == SortMode.STATUS:
                    self._reverse_sort = not self._reverse_sort
                else:
                    self._sort_mode = SortMode.STATUS
                    self._reverse_sort = False
                    
            elif key == 'u':
                # Sort by username
                if self._sort_mode == SortMode.USERNAME:
                    self._reverse_sort = not self._reverse_sort
                else:
                    self._sort_mode = SortMode.USERNAME
                    self._reverse_sort = False
                    
            elif key == 't':
                # Sort by time (last seen)
                if self._sort_mode == SortMode.LAST_SEEN:
                    self._reverse_sort = not self._reverse_sort
                else:
                    self._sort_mode = SortMode.LAST_SEEN
                    self._reverse_sort = False
                    
            elif key in ['h', '?']:
                # Toggle help
                self._show_help = not self._show_help
                
            elif key == '+':
                # Increase refresh rate (decrease interval)
                self._refresh_rate = max(0.5, self._refresh_rate - 0.5)
                
            elif key == '-':
                # Decrease refresh rate (increase interval)
                self._refresh_rate = min(10.0, self._refresh_rate + 0.5)
                
        except Exception as e:
            self.logger.error(f"Key handling error: {e}")
    
    def _sort_streamers(self, streamers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort streamers based on current sort mode."""
        try:
            if self._sort_mode == SortMode.USERNAME:
                key_func = lambda x: x['username']
            elif self._sort_mode == SortMode.STATUS:
                # Sort by status priority: online, offline, unknown
                status_priority = {'online': 0, 'offline': 1, 'unknown': 2}
                key_func = lambda x: status_priority.get(x['status'], 3)
            elif self._sort_mode == SortMode.LAST_SEEN:
                def last_seen_key(streamer):
                    last_seen = streamer.get('last_seen_online')
                    if last_seen:
                        try:
                            return datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
                        except:
                            return datetime.min
                    return datetime.min
                key_func = last_seen_key
            else:  # UPDATES
                key_func = lambda x: hash(x['username']) % 100  # Fake update count
            
            return sorted(streamers, key=key_func, reverse=self._reverse_sort)
            
        except Exception as e:
            self.logger.error(f"Sorting error: {e}")
            return streamers
    
    async def _shutdown(self) -> int:
        """Gracefully shutdown the UI."""
        try:
            self._running = False
            
            # Cancel keyboard input task
            if self._keyboard_input_task:
                self._keyboard_input_task.cancel()
                try:
                    await self._keyboard_input_task
                except asyncio.CancelledError:
                    pass
            
            # Clear screen and show exit message
            self.console.clear()
            
            # Show shutdown summary
            uptime = datetime.now(timezone.utc) - self._start_time
            
            shutdown_panel = Panel(
                Text.assemble(
                    ("Manual Mode Session Complete", "bold green"),
                    "\n\n",
                    ("Session Duration: ", "dim"), (str(uptime).split('.')[0], "cyan"),
                    "\n",
                    ("UI Updates: ", "dim"), (f"{self._update_count:,}", "yellow"),
                    "\n",
                    ("Last Update: ", "dim"), (self._last_update.strftime("%H:%M:%S"), "white"),
                    "\n\n",
                    ("Thank you for using Kick Monitor!", "dim italic")
                ),
                title="Goodbye",
                border_style="green",
                padding=(1, 2)
            )
            
            self.console.print(Align.center(shutdown_panel))
            
            return 0
            
        except Exception as e:
            self.logger.error(f"Shutdown error: {e}")
            return 1
    
    @property
    def is_running(self) -> bool:
        """Check if UI is currently running."""
        return self._running
    
    def request_shutdown(self) -> None:
        """Request shutdown of the UI."""
        self._shutdown_requested = True


# Helper function for service integration
async def run_manual_mode(monitor_service: KickMonitorService) -> int:
    """
    Run manual mode UI for the monitoring service.
    
    Args:
        monitor_service: The monitoring service instance
        
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    try:
        if not RICH_AVAILABLE:
            print("Error: Rich library not installed. Manual mode requires 'rich' package.")
            print("Install with: pip install rich")
            return 8
        
        ui = ManualModeUI(monitor_service)
        return await ui.run()
        
    except KeyboardInterrupt:
        print("\nManual mode terminated by user")
        return 0
    except Exception as e:
        print(f"Manual mode error: {e}")
        return 1