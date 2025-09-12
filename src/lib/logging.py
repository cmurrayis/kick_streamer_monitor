"""
Logging configuration for kick-monitor.
Provides structured JSON logging with different levels and outputs.
"""

import json
import logging
import logging.config
import sys
from datetime import datetime
from typing import Any, Dict, Optional
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler


class JSONFormatter(logging.Formatter):
    """
    Custom JSON formatter for structured logging.
    Formats log records as JSON with consistent fields.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as JSON."""
        # Create the base log entry
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception information if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields from the log record
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        # Add common extra fields
        extra_fields = {
            "thread_id": record.thread,
            "process_id": record.process,
        }

        # Add streamer context if available
        if hasattr(record, "streamer_id"):
            extra_fields["streamer_id"] = record.streamer_id
        if hasattr(record, "streamer_username"):
            extra_fields["streamer_username"] = record.streamer_username

        # Add API context if available
        if hasattr(record, "api_endpoint"):
            extra_fields["api_endpoint"] = record.api_endpoint
        if hasattr(record, "response_status"):
            extra_fields["response_status"] = record.response_status

        # Add WebSocket context if available
        if hasattr(record, "websocket_event"):
            extra_fields["websocket_event"] = record.websocket_event
        if hasattr(record, "connection_id"):
            extra_fields["connection_id"] = record.connection_id

        log_entry.update(extra_fields)

        return json.dumps(log_entry, ensure_ascii=False)


class ContextLogger:
    """
    Context-aware logger that adds structured fields to log records.
    Allows adding context like streamer_id, api_endpoint, etc.
    """

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self.context: Dict[str, Any] = {}

    def set_context(self, **kwargs: Any) -> None:
        """Set context fields that will be added to all log records."""
        self.context.update(kwargs)

    def clear_context(self) -> None:
        """Clear all context fields."""
        self.context.clear()

    def _log_with_context(self, level: int, msg: str, *args, **kwargs) -> None:
        """Log a message with context fields."""
        extra = kwargs.get("extra", {})
        extra.update(self.context)
        kwargs["extra"] = extra
        self.logger.log(level, msg, *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs) -> None:
        """Log a debug message with context."""
        self._log_with_context(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        """Log an info message with context."""
        self._log_with_context(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        """Log a warning message with context."""
        self._log_with_context(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        """Log an error message with context."""
        self._log_with_context(logging.ERROR, msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        """Log a critical message with context."""
        self._log_with_context(logging.CRITICAL, msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        """Log an exception message with context."""
        kwargs["exc_info"] = True
        self.error(msg, *args, **kwargs)


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    console_output: bool = True,
    json_format: bool = True,
    rich_console: bool = False,
) -> None:
    """
    Setup logging configuration for the application.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file (optional)
        console_output: Whether to output to console
        json_format: Whether to use JSON formatting
        rich_console: Whether to use Rich console formatting (overrides json_format for console)
    """
    # Convert string level to logging constant
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Create formatters
    if json_format and not rich_console:
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handlers = []

    # Console handler
    if console_output:
        if rich_console:
            console = Console(stderr=True)
            console_handler = RichHandler(
                console=console,
                show_time=True,
                show_path=True,
                rich_tracebacks=True,
            )
        else:
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setFormatter(formatter)

        console_handler.setLevel(numeric_level)
        handlers.append(console_handler)

    # File handler
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(JSONFormatter())  # Always use JSON for file output
        file_handler.setLevel(numeric_level)
        handlers.append(file_handler)

    # Add handlers to root logger
    for handler in handlers:
        root_logger.addHandler(handler)

    # Set up logger for third-party libraries
    # Reduce noise from verbose libraries
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> ContextLogger:
    """
    Get a context-aware logger instance.

    Args:
        name: Logger name (usually __name__)

    Returns:
        ContextLogger instance
    """
    return ContextLogger(name)


# Convenience function for getting application loggers
def get_app_logger(module_name: str) -> ContextLogger:
    """
    Get an application logger with consistent naming.

    Args:
        module_name: Module name (e.g., 'services.auth', 'models.streamer')

    Returns:
        ContextLogger instance with app prefix
    """
    return get_logger(f"kick_monitor.{module_name}")


# Pre-configured loggers for common components
auth_logger = get_app_logger("services.auth")
websocket_logger = get_app_logger("services.websocket")
database_logger = get_app_logger("services.database")
monitor_logger = get_app_logger("services.monitor")
cli_logger = get_app_logger("cli")