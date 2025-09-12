"""
Configuration loading and validation with .env file support.

Provides centralized configuration management with support for environment
variables, .env files, and runtime validation for the Kick streamer monitor.
"""

import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, List, Union, Tuple
from dataclasses import dataclass, field
from enum import Enum

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

from ..models import (
    ConfigurationDefaults, EnvironmentConfigLoader, ConfigCategory,
    ConfigurationType
)
from ..services import DatabaseConfig, OAuthConfig, PusherConfig

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Configuration related errors."""
    pass


class ValidationLevel(str, Enum):
    """Configuration validation levels."""
    STRICT = "strict"      # All required settings must be present and valid
    LENIENT = "lenient"    # Missing settings get defaults, warn on issues
    MINIMAL = "minimal"    # Only validate critical settings


@dataclass
class ConfigValidationResult:
    """Result of configuration validation."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    missing_required: List[str] = field(default_factory=list)
    invalid_values: List[str] = field(default_factory=list)
    
    @property
    def has_errors(self) -> bool:
        """Check if validation has any errors."""
        return len(self.errors) > 0 or len(self.missing_required) > 0 or len(self.invalid_values) > 0
    
    @property
    def has_warnings(self) -> bool:
        """Check if validation has any warnings."""
        return len(self.warnings) > 0
    
    def add_error(self, message: str) -> None:
        """Add an error message."""
        self.errors.append(message)
        self.is_valid = False
    
    def add_warning(self, message: str) -> None:
        """Add a warning message."""
        self.warnings.append(message)
    
    def add_missing_required(self, key: str) -> None:
        """Add a missing required setting."""
        self.missing_required.append(key)
        self.is_valid = False
    
    def add_invalid_value(self, key: str, reason: str) -> None:
        """Add an invalid value error."""
        self.invalid_values.append(f"{key}: {reason}")
        self.is_valid = False


class ConfigurationManager:
    """
    Centralized configuration management system.
    
    Handles loading configuration from multiple sources with priority order:
    1. Explicit overrides (passed directly)
    2. Environment variables
    3. .env file
    4. Default values
    """
    
    def __init__(
        self,
        env_file: Optional[Union[str, Path]] = None,
        validation_level: ValidationLevel = ValidationLevel.STRICT,
        auto_load: bool = True
    ):
        self.env_file = Path(env_file) if env_file else None
        self.validation_level = validation_level
        
        # Configuration storage
        self._config: Dict[str, Any] = {}
        self._config_sources: Dict[str, str] = {}
        self._is_loaded = False
        
        # Validation rules
        self._required_settings = {
            # Authentication
            'KICK_CLIENT_ID': 'Kick.com OAuth client ID',
            'KICK_CLIENT_SECRET': 'Kick.com OAuth client secret',
            
            # Database
            'DATABASE_HOST': 'Database hostname',
            'DATABASE_NAME': 'Database name',
            'DATABASE_USER': 'Database username',
            'DATABASE_PASSWORD': 'Database password',
        }
        
        self._optional_settings = {
            # Database optional
            'DATABASE_PORT': ('5432', int, 'Database port number'),
            'DATABASE_POOL_SIZE': ('10', int, 'Database connection pool size'),
            'DATABASE_MAX_OVERFLOW': ('20', int, 'Database max overflow connections'),
            'DATABASE_QUERY_TIMEOUT': ('30', int, 'Database query timeout in seconds'),
            
            # Authentication optional
            'AUTH_TOKEN_REFRESH_MARGIN': ('300', int, 'Seconds before token expiry to refresh'),
            'AUTH_MAX_RETRY_ATTEMPTS': ('3', int, 'Maximum OAuth retry attempts'),
            
            # Monitoring
            'MONITORING_STATUS_UPDATE_INTERVAL': ('5', int, 'Status update interval in seconds'),
            'MONITORING_RECONNECT_DELAY': ('10', int, 'WebSocket reconnect delay in seconds'),
            'MONITORING_MAX_RECONNECT_ATTEMPTS': ('5', int, 'Maximum WebSocket reconnect attempts'),
            
            # System
            'SYSTEM_LOG_LEVEL': ('INFO', str, 'Application log level'),
            'SYSTEM_MAX_LOG_FILE_SIZE': ('10485760', int, 'Maximum log file size in bytes'),
            'SYSTEM_LOG_RETENTION_DAYS': ('7', int, 'Log file retention in days'),
            
            # Pusher WebSocket (usually discovered from API)
            'PUSHER_APP_KEY': ('d44c06c23c42fd6e5c58', str, 'Pusher application key'),
            'PUSHER_CLUSTER': ('us2', str, 'Pusher cluster'),
        }
        
        if auto_load:
            self.load_configuration()
    
    def load_configuration(self) -> None:
        """Load configuration from all sources."""
        logger.info("Loading application configuration")
        
        # Clear existing configuration
        self._config.clear()
        self._config_sources.clear()
        
        # 1. Load defaults first
        self._load_defaults()
        
        # 2. Load from .env file if available
        if self.env_file and self.env_file.exists():
            self._load_from_env_file()
        else:
            # Try to find .env file in common locations
            self._load_from_auto_detected_env_file()
        
        # 3. Load from environment variables (highest priority)
        self._load_from_environment()
        
        # 4. Apply any programmatic overrides
        self._apply_overrides()
        
        self._is_loaded = True
        logger.info(f"Configuration loaded from {len(set(self._config_sources.values()))} sources")
    
    def _load_defaults(self) -> None:
        """Load default configuration values."""
        defaults = ConfigurationDefaults.get_default_configurations()
        
        for config in defaults:
            self._config[config.key] = config.value
            self._config_sources[config.key] = "defaults"
        
        logger.debug(f"Loaded {len(defaults)} default configuration values")
    
    def _load_from_env_file(self) -> None:
        """Load configuration from specified .env file."""
        if not self.env_file or not self.env_file.exists():
            return
        
        logger.info(f"Loading configuration from: {self.env_file}")
        
        try:
            if DOTENV_AVAILABLE:
                # Use python-dotenv if available
                load_dotenv(self.env_file, override=False)
                logger.debug("Used python-dotenv for .env file loading")
            else:
                # Manual parsing if python-dotenv not available
                logger.warning("python-dotenv not available, using manual parsing")
            
            # Parse manually to track sources
            with open(self.env_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    
                    # Parse key=value pairs
                    if '=' not in line:
                        logger.warning(f"Invalid line {line_num} in {self.env_file}: {line}")
                        continue
                    
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"\'')  # Remove quotes
                    
                    if key and not key in os.environ:  # Don't override env vars
                        self._config[key] = value
                        self._config_sources[key] = str(self.env_file)
            
        except Exception as e:
            logger.error(f"Error loading .env file {self.env_file}: {e}")
            raise ConfigurationError(f"Failed to load .env file: {e}") from e
    
    def _load_from_auto_detected_env_file(self) -> None:
        """Try to find and load .env file from common locations."""
        possible_locations = [
            Path('.env'),
            Path('.env.local'),
            Path('config/.env'),
            Path(os.path.expanduser('~/.kick-monitor/.env')),
        ]
        
        for env_path in possible_locations:
            if env_path.exists():
                logger.info(f"Auto-detected .env file: {env_path}")
                self.env_file = env_path
                self._load_from_env_file()
                break
    
    def _load_from_environment(self) -> None:
        """Load configuration from environment variables."""
        env_count = 0
        
        # Load all environment variables that match our patterns
        for key in os.environ:
            # Check for kick monitor specific variables
            if (key.startswith(('KICK_', 'DATABASE_', 'AUTH_', 'MONITORING_', 'SYSTEM_', 'PUSHER_')) or
                key in self._required_settings or
                key in self._optional_settings):
                
                self._config[key] = os.environ[key]
                self._config_sources[key] = "environment"
                env_count += 1
        
        if env_count > 0:
            logger.debug(f"Loaded {env_count} settings from environment variables")
    
    def _apply_overrides(self) -> None:
        """Apply any programmatic configuration overrides."""
        # This method can be extended to apply runtime overrides
        pass
    
    def validate_configuration(self) -> ConfigValidationResult:
        """Validate the loaded configuration."""
        result = ConfigValidationResult(is_valid=True)
        
        if not self._is_loaded:
            result.add_error("Configuration not loaded")
            return result
        
        # Check required settings
        for key, description in self._required_settings.items():
            if not self.get(key):
                result.add_missing_required(key)
                if self.validation_level == ValidationLevel.STRICT:
                    result.add_error(f"Missing required setting: {key} ({description})")
                else:
                    result.add_warning(f"Missing required setting: {key} ({description})")
        
        # Validate optional settings with type checking
        for key, (default_val, expected_type, description) in self._optional_settings.items():
            value = self.get(key)
            if value is not None:
                try:
                    if expected_type == int:
                        int(value)
                    elif expected_type == float:
                        float(value)
                    elif expected_type == bool:
                        self._parse_bool(value)
                except ValueError:
                    result.add_invalid_value(key, f"Expected {expected_type.__name__}, got: {value}")
        
        # Validate specific settings
        self._validate_database_config(result)
        self._validate_auth_config(result)
        self._validate_monitoring_config(result)
        
        return result
    
    def _validate_database_config(self, result: ConfigValidationResult) -> None:
        """Validate database-specific configuration."""
        # Database port validation
        port = self.get('DATABASE_PORT')
        if port:
            try:
                port_num = int(port)
                if port_num < 1 or port_num > 65535:
                    result.add_invalid_value('DATABASE_PORT', 'Port must be between 1 and 65535')
            except ValueError:
                result.add_invalid_value('DATABASE_PORT', 'Port must be a valid integer')
        
        # Pool size validation
        pool_size = self.get('DATABASE_POOL_SIZE')
        if pool_size:
            try:
                pool_num = int(pool_size)
                if pool_num < 1 or pool_num > 100:
                    result.add_warning('DATABASE_POOL_SIZE should be between 1 and 100')
            except ValueError:
                result.add_invalid_value('DATABASE_POOL_SIZE', 'Must be a valid integer')
    
    def _validate_auth_config(self, result: ConfigValidationResult) -> None:
        """Validate authentication-specific configuration."""
        client_id = self.get('KICK_CLIENT_ID')
        client_secret = self.get('KICK_CLIENT_SECRET')
        
        if client_id and len(client_id) < 10:
            result.add_warning('KICK_CLIENT_ID seems too short - verify this is correct')
        
        if client_secret and len(client_secret) < 20:
            result.add_warning('KICK_CLIENT_SECRET seems too short - verify this is correct')
    
    def _validate_monitoring_config(self, result: ConfigValidationResult) -> None:
        """Validate monitoring-specific configuration."""
        # Update interval validation
        interval = self.get('MONITORING_STATUS_UPDATE_INTERVAL')
        if interval:
            try:
                interval_num = int(interval)
                if interval_num < 1:
                    result.add_invalid_value('MONITORING_STATUS_UPDATE_INTERVAL', 'Must be at least 1 second')
                elif interval_num > 300:
                    result.add_warning('MONITORING_STATUS_UPDATE_INTERVAL is very high - consider reducing')
            except ValueError:
                result.add_invalid_value('MONITORING_STATUS_UPDATE_INTERVAL', 'Must be a valid integer')
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key."""
        return self._config.get(key, default)
    
    def get_int(self, key: str, default: int = 0) -> int:
        """Get configuration value as integer."""
        value = self.get(key, str(default))
        try:
            return int(value)
        except ValueError:
            logger.warning(f"Invalid integer value for {key}: {value}, using default: {default}")
            return default
    
    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get configuration value as float."""
        value = self.get(key, str(default))
        try:
            return float(value)
        except ValueError:
            logger.warning(f"Invalid float value for {key}: {value}, using default: {default}")
            return default
    
    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get configuration value as boolean."""
        value = self.get(key, str(default).lower())
        return self._parse_bool(value)
    
    def _parse_bool(self, value: Any) -> bool:
        """Parse boolean value from string."""
        if isinstance(value, bool):
            return value
        
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 'on', 'enabled')
        
        return bool(value)
    
    def get_list(self, key: str, separator: str = ',', default: Optional[List[str]] = None) -> List[str]:
        """Get configuration value as list."""
        value = self.get(key)
        if not value:
            return default or []
        
        if isinstance(value, list):
            return value
        
        return [item.strip() for item in str(value).split(separator) if item.strip()]
    
    def set(self, key: str, value: Any, source: str = "programmatic") -> None:
        """Set configuration value programmatically."""
        self._config[key] = value
        self._config_sources[key] = source
    
    def get_source(self, key: str) -> Optional[str]:
        """Get the source of a configuration value."""
        return self._config_sources.get(key)
    
    def get_all_config(self, include_sources: bool = False) -> Dict[str, Any]:
        """Get all configuration as dictionary."""
        if include_sources:
            return {
                'config': self._config.copy(),
                'sources': self._config_sources.copy()
            }
        return self._config.copy()
    
    def get_database_config(self) -> DatabaseConfig:
        """Get database configuration object."""
        return DatabaseConfig(
            host=self.get('DATABASE_HOST', 'localhost'),
            port=self.get_int('DATABASE_PORT', 5432),
            database=self.get('DATABASE_NAME', 'kick_monitor'),
            username=self.get('DATABASE_USER', 'kick_monitor'),
            password=self.get('DATABASE_PASSWORD', ''),
            min_connections=self.get_int('DATABASE_POOL_SIZE', 10),
            max_connections=self.get_int('DATABASE_MAX_OVERFLOW', 20),
            command_timeout=self.get_float('DATABASE_QUERY_TIMEOUT', 30.0)
        )
    
    def get_oauth_config(self) -> OAuthConfig:
        """Get OAuth configuration object."""
        client_id = self.get('KICK_CLIENT_ID')
        client_secret = self.get('KICK_CLIENT_SECRET')
        
        if not client_id or not client_secret:
            raise ConfigurationError("Missing required OAuth configuration (KICK_CLIENT_ID, KICK_CLIENT_SECRET)")
        
        return OAuthConfig(
            client_id=client_id,
            client_secret=client_secret,
            max_retries=self.get_int('AUTH_MAX_RETRY_ATTEMPTS', 3),
            refresh_margin_seconds=self.get_int('AUTH_TOKEN_REFRESH_MARGIN', 300)
        )
    
    def get_pusher_config(self) -> PusherConfig:
        """Get Pusher WebSocket configuration object."""
        return PusherConfig(
            app_key=self.get('PUSHER_APP_KEY', 'd44c06c23c42fd6e5c58'),
            cluster=self.get('PUSHER_CLUSTER', 'us2'),
            reconnect_interval=self.get_int('MONITORING_RECONNECT_DELAY', 10),
            max_reconnect_attempts=self.get_int('MONITORING_MAX_RECONNECT_ATTEMPTS', 5)
        )
    
    def export_to_env_file(self, file_path: Union[str, Path], include_comments: bool = True) -> None:
        """Export current configuration to .env file."""
        file_path = Path(file_path)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            if include_comments:
                f.write("# Kick Streamer Status Monitor Configuration\n")
                f.write("# Generated automatically - modify as needed\n")
                f.write(f"# Generated on: {os.popen('date').read().strip()}\n\n")
            
            # Group by category
            categories = {
                'Authentication': ['KICK_CLIENT_ID', 'KICK_CLIENT_SECRET', 'AUTH_'],
                'Database': ['DATABASE_'],
                'Monitoring': ['MONITORING_'],
                'System': ['SYSTEM_'],
                'Pusher': ['PUSHER_']
            }
            
            for category, prefixes in categories.items():
                category_items = []
                for key, value in self._config.items():
                    if any(key.startswith(prefix) for prefix in prefixes):
                        category_items.append((key, value))
                
                if category_items:
                    if include_comments:
                        f.write(f"# {category} Configuration\n")
                    
                    for key, value in sorted(category_items):
                        # Mask sensitive values
                        if any(sensitive in key.lower() for sensitive in ['secret', 'password', 'token']):
                            display_value = "your_" + key.lower() + "_here"
                        else:
                            display_value = value
                        
                        f.write(f"{key}={display_value}\n")
                    
                    f.write("\n")
        
        logger.info(f"Configuration exported to: {file_path}")
    
    def reload(self) -> None:
        """Reload configuration from all sources."""
        self.load_configuration()
    
    @property
    def is_loaded(self) -> bool:
        """Check if configuration has been loaded."""
        return self._is_loaded
    
    def __repr__(self) -> str:
        """String representation of configuration manager."""
        source_count = len(set(self._config_sources.values()))
        return f"<ConfigurationManager(loaded={self._is_loaded}, settings={len(self._config)}, sources={source_count})>"


# Global configuration instance
_global_config: Optional[ConfigurationManager] = None


def get_config() -> ConfigurationManager:
    """Get global configuration instance."""
    global _global_config
    if _global_config is None:
        _global_config = ConfigurationManager()
    return _global_config


def load_config(env_file: Optional[Union[str, Path]] = None, 
                validation_level: ValidationLevel = ValidationLevel.STRICT) -> ConfigurationManager:
    """Load and return configuration manager."""
    global _global_config
    _global_config = ConfigurationManager(env_file=env_file, validation_level=validation_level)
    return _global_config


def reload_config() -> ConfigurationManager:
    """Reload global configuration."""
    config = get_config()
    config.reload()
    return config