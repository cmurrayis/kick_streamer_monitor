"""
CLI configuration commands for Kick Streamer Status Monitor.

Provides commands for managing application configuration including
showing current settings, validating configuration, and generating templates.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List
import yaml

from models import (
    Configuration, ConfigurationCreate, ConfigCategory, ConfigurationType,
    EnvironmentConfigLoader, ConfigurationDefaults
)
from services import DatabaseService, DatabaseConfig
from lib.logging import setup_logging


class ConfigCommandError(Exception):
    """Configuration command specific errors."""
    pass


class ConfigCommands:
    """Configuration management CLI commands."""
    
    def __init__(self):
        self.logger = setup_logging(__name__)
    
    async def show(self, args: argparse.Namespace) -> int:
        """Show current configuration."""
        try:
            # Determine output format
            output_format = getattr(args, 'format', 'table')
            mask_secrets = getattr(args, 'mask_secrets', True)
            category_filter = getattr(args, 'category', None)
            
            # Load configuration from database if available
            config_data = await self._load_configuration_from_database(args.config)
            
            # If no database config, load from environment
            if not config_data:
                config_data = self._load_configuration_from_environment()
            
            # Filter by category if specified
            if category_filter:
                try:
                    filter_cat = ConfigCategory(category_filter.lower())
                    config_data = [c for c in config_data if c.get('category') == filter_cat.value]
                except ValueError:
                    print(f"Error: Invalid category '{category_filter}'. Valid categories: {', '.join([c.value for c in ConfigCategory])}")
                    return 6
            
            # Output configuration
            if output_format == 'json':
                self._output_json(config_data, mask_secrets)
            elif output_format == 'yaml':
                self._output_yaml(config_data, mask_secrets)
            else:  # table format
                self._output_table(config_data, mask_secrets)
            
            return 0
            
        except Exception as e:
            self.logger.error(f"Failed to show configuration: {e}")
            print(f"Error: {e}")
            return 1
    
    async def validate(self, args: argparse.Namespace) -> int:
        """Validate configuration."""
        try:
            config_file = getattr(args, 'config', None)
            
            if config_file and os.path.exists(config_file):
                print(f"Validating configuration from: {config_file}")
                validation_result = await self._validate_config_file(config_file)
            else:
                print("Validating environment configuration...")
                validation_result = await self._validate_environment_config()
            
            # Display validation results
            if validation_result['valid']:
                print("✓ Configuration validation passed")
                
                # Show summary
                print(f"\nConfiguration Summary:")
                print(f"  • Total settings: {validation_result['total_settings']}")
                print(f"  • Categories: {', '.join(validation_result['categories'])}")
                print(f"  • Sensitive settings: {validation_result['sensitive_count']}")
                
                if validation_result['warnings']:
                    print(f"\nWarnings:")
                    for warning in validation_result['warnings']:
                        print(f"  ⚠ {warning}")
                
                return 0
            else:
                print("✗ Configuration validation failed")
                print(f"\nErrors:")
                for error in validation_result['errors']:
                    print(f"  ✗ {error}")
                
                if validation_result['warnings']:
                    print(f"\nWarnings:")
                    for warning in validation_result['warnings']:
                        print(f"  ⚠ {warning}")
                
                return 1
        
        except Exception as e:
            self.logger.error(f"Configuration validation failed: {e}")
            print(f"Error: {e}")
            return 1
    
    async def generate_template(self, args: argparse.Namespace) -> int:
        """Generate configuration template."""
        try:
            output_format = getattr(args, 'format', 'env')
            output_file = getattr(args, 'output', None)
            include_examples = getattr(args, 'examples', True)
            
            # Generate template content
            if output_format == 'env':
                template_content = self._generate_env_template(include_examples)
            elif output_format == 'json':
                template_content = self._generate_json_template(include_examples)
            elif output_format == 'yaml':
                template_content = self._generate_yaml_template(include_examples)
            else:
                print(f"Error: Unsupported format '{output_format}'. Use: env, json, yaml")
                return 6
            
            # Output template
            if output_file:
                with open(output_file, 'w') as f:
                    f.write(template_content)
                print(f"Configuration template written to: {output_file}")
            else:
                print(template_content)
            
            return 0
            
        except Exception as e:
            self.logger.error(f"Failed to generate template: {e}")
            print(f"Error: {e}")
            return 1
    
    async def _load_configuration_from_database(self, config_file: Optional[str]) -> List[Dict[str, Any]]:
        """Load configuration from database."""
        try:
            # Load database config
            db_config = self._load_database_config(config_file)
            if not db_config:
                return []
            
            # Connect to database and fetch configuration
            db_service = DatabaseService(db_config)
            await db_service.connect()
            
            try:
                configurations = await db_service.get_all_configurations()
                return [config.to_dict(mask_sensitive=False) for config in configurations]
            finally:
                await db_service.disconnect()
        
        except Exception as e:
            self.logger.debug(f"Could not load from database: {e}")
            return []
    
    def _load_configuration_from_environment(self) -> List[Dict[str, Any]]:
        """Load configuration from environment variables."""
        configs = EnvironmentConfigLoader.load_from_env()
        defaults = ConfigurationDefaults.get_default_configurations()
        
        all_configs = configs + defaults
        return [config.dict() for config in all_configs]
    
    def _load_database_config(self, config_file: Optional[str]) -> Optional[DatabaseConfig]:
        """Load database configuration from file or environment."""
        try:
            if config_file and os.path.exists(config_file):
                # Load from .env file
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
    
    async def _validate_config_file(self, config_file: str) -> Dict[str, Any]:
        """Validate configuration from file."""
        errors = []
        warnings = []
        categories = set()
        sensitive_count = 0
        total_settings = 0
        
        try:
            # Check file exists and is readable
            if not os.path.exists(config_file):
                errors.append(f"Configuration file not found: {config_file}")
                return {'valid': False, 'errors': errors, 'warnings': warnings, 'total_settings': 0, 'categories': [], 'sensitive_count': 0}
            
            # Parse configuration file
            config_vars = {}
            with open(config_file, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '=' not in line:
                            warnings.append(f"Line {line_num}: Invalid format, missing '=' separator")
                            continue
                        
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"\'')
                        
                        if not key:
                            warnings.append(f"Line {line_num}: Empty configuration key")
                            continue
                        
                        config_vars[key] = value
                        total_settings += 1
            
            # Validate required settings
            required_settings = {
                'KICK_CLIENT_ID': 'Kick.com OAuth client ID',
                'KICK_CLIENT_SECRET': 'Kick.com OAuth client secret',
                'DATABASE_HOST': 'Database hostname',
                'DATABASE_NAME': 'Database name',
                'DATABASE_USER': 'Database username',
                'DATABASE_PASSWORD': 'Database password'
            }
            
            for key, description in required_settings.items():
                if key not in config_vars or not config_vars[key]:
                    errors.append(f"Missing required setting: {key} ({description})")
                elif any(sensitive in key.lower() for sensitive in ['secret', 'password', 'token']):
                    sensitive_count += 1
            
            # Validate specific settings
            if 'DATABASE_PORT' in config_vars:
                try:
                    port = int(config_vars['DATABASE_PORT'])
                    if port < 1 or port > 65535:
                        errors.append("DATABASE_PORT must be between 1 and 65535")
                except ValueError:
                    errors.append("DATABASE_PORT must be a valid integer")
            
            # Test database connection if possible
            try:
                db_config = self._load_database_config(config_file)
                if db_config:
                    db_service = DatabaseService(db_config)
                    await db_service.connect()
                    health = await db_service.health_check()
                    await db_service.disconnect()
                    
                    if health['status'] != 'healthy':
                        warnings.append("Database connection test failed")
            except Exception as e:
                warnings.append(f"Could not test database connection: {e}")
            
            # Categorize settings
            for key in config_vars.keys():
                if key.startswith('AUTH_') or 'CLIENT' in key:
                    categories.add('auth')
                elif key.startswith('DATABASE_'):
                    categories.add('database')
                elif key.startswith('MONITOR_') or key.startswith('WEBSOCKET_'):
                    categories.add('monitoring')
                else:
                    categories.add('system')
            
            return {
                'valid': len(errors) == 0,
                'errors': errors,
                'warnings': warnings,
                'total_settings': total_settings,
                'categories': sorted(list(categories)),
                'sensitive_count': sensitive_count
            }
        
        except Exception as e:
            errors.append(f"Error reading configuration file: {e}")
            return {'valid': False, 'errors': errors, 'warnings': warnings, 'total_settings': 0, 'categories': [], 'sensitive_count': 0}
    
    async def _validate_environment_config(self) -> Dict[str, Any]:
        """Validate configuration from environment variables."""
        errors = []
        warnings = []
        categories = set()
        sensitive_count = 0
        
        # Check for required environment variables
        required_env_vars = [
            'KICK_CLIENT_ID',
            'KICK_CLIENT_SECRET', 
            'DATABASE_HOST',
            'DATABASE_NAME',
            'DATABASE_USER',
            'DATABASE_PASSWORD'
        ]
        
        env_vars = {}
        for var in required_env_vars:
            value = os.getenv(var)
            if not value:
                errors.append(f"Missing required environment variable: {var}")
            else:
                env_vars[var] = value
                if any(sensitive in var.lower() for sensitive in ['secret', 'password', 'token']):
                    sensitive_count += 1
        
        # Load all environment configs
        configs = EnvironmentConfigLoader.load_from_env()
        total_settings = len(configs)
        
        for config in configs:
            categories.add(config.category.value)
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'total_settings': total_settings,
            'categories': sorted(list(categories)),
            'sensitive_count': sensitive_count
        }
    
    def _output_table(self, config_data: List[Dict[str, Any]], mask_secrets: bool) -> None:
        """Output configuration as table."""
        if not config_data:
            print("No configuration found.")
            return
        
        # Calculate column widths
        max_key_width = max(len(config.get('key', '')) for config in config_data)
        max_value_width = 50  # Max width for value column
        max_category_width = max(len(config.get('category', '')) for config in config_data)
        
        # Print header
        header = f"{'Key':<{max_key_width}} | {'Category':<{max_category_width}} | {'Value':<{max_value_width}} | {'Sensitive'}"
        print(header)
        print("-" * len(header))
        
        # Print configuration entries
        for config in sorted(config_data, key=lambda x: (x.get('category', ''), x.get('key', ''))):
            key = config.get('key', '')
            category = config.get('category', '')
            value = config.get('value', '')
            is_sensitive = config.get('is_sensitive', False)
            
            # Mask sensitive values if requested
            if mask_secrets and is_sensitive and value:
                if len(value) <= 4:
                    display_value = "*" * len(value)
                else:
                    display_value = value[:2] + "*" * (len(value) - 4) + value[-2:]
            else:
                display_value = value
            
            # Truncate long values
            if len(display_value) > max_value_width:
                display_value = display_value[:max_value_width-3] + "..."
            
            sensitive_marker = "Yes" if is_sensitive else "No"
            
            print(f"{key:<{max_key_width}} | {category:<{max_category_width}} | {display_value:<{max_value_width}} | {sensitive_marker}")
    
    def _output_json(self, config_data: List[Dict[str, Any]], mask_secrets: bool) -> None:
        """Output configuration as JSON."""
        output_data = {}
        
        for config in config_data:
            key = config.get('key', '')
            value = config.get('value', '')
            is_sensitive = config.get('is_sensitive', False)
            
            # Mask sensitive values if requested
            if mask_secrets and is_sensitive and value:
                if len(value) <= 4:
                    display_value = "*" * len(value)
                else:
                    display_value = value[:2] + "*" * (len(value) - 4) + value[-2:]
            else:
                display_value = value
            
            output_data[key] = {
                'value': display_value,
                'category': config.get('category', ''),
                'description': config.get('description', ''),
                'sensitive': is_sensitive
            }
        
        print(json.dumps(output_data, indent=2, sort_keys=True))
    
    def _output_yaml(self, config_data: List[Dict[str, Any]], mask_secrets: bool) -> None:
        """Output configuration as YAML."""
        output_data = {}
        
        for config in config_data:
            key = config.get('key', '')
            value = config.get('value', '')
            is_sensitive = config.get('is_sensitive', False)
            
            # Mask sensitive values if requested
            if mask_secrets and is_sensitive and value:
                if len(value) <= 4:
                    display_value = "*" * len(value)
                else:
                    display_value = value[:2] + "*" * (len(value) - 4) + value[-2:]
            else:
                display_value = value
            
            output_data[key] = {
                'value': display_value,
                'category': config.get('category', ''),
                'description': config.get('description', ''),
                'sensitive': is_sensitive
            }
        
        print(yaml.dump(output_data, default_flow_style=False, sort_keys=True))
    
    def _generate_env_template(self, include_examples: bool) -> str:
        """Generate .env template."""
        template = []
        template.append("# Kick Streamer Status Monitor Configuration")
        template.append("# Copy this file to .env and fill in your values")
        template.append("")
        
        # Authentication settings
        template.append("# ============================================================================")
        template.append("# AUTHENTICATION - Kick.com API Credentials")
        template.append("# ============================================================================")
        template.append("# Register at https://dev.kick.com/ to get these values")
        template.append("")
        template.append("KICK_CLIENT_ID=your_client_id_here")
        template.append("KICK_CLIENT_SECRET=your_client_secret_here")
        
        if include_examples:
            template.append("")
            template.append("# Optional authentication settings")
            template.append("# AUTH_TOKEN_REFRESH_MARGIN=300")
            template.append("# AUTH_MAX_RETRY_ATTEMPTS=3")
        
        template.append("")
        
        # Database settings
        template.append("# ============================================================================")
        template.append("# DATABASE - PostgreSQL Connection")
        template.append("# ============================================================================")
        template.append("")
        template.append("DATABASE_HOST=localhost")
        template.append("DATABASE_PORT=5432")
        template.append("DATABASE_NAME=kick_monitor")
        template.append("DATABASE_USER=kick_monitor")
        template.append("DATABASE_PASSWORD=your_password_here")
        
        if include_examples:
            template.append("")
            template.append("# Optional database settings")
            template.append("# DATABASE_POOL_SIZE=10")
            template.append("# DATABASE_MAX_OVERFLOW=20")
            template.append("# DATABASE_QUERY_TIMEOUT=30")
        
        template.append("")
        
        # Monitoring settings
        template.append("# ============================================================================")
        template.append("# MONITORING - WebSocket and Status Tracking")
        template.append("# ============================================================================")
        
        if include_examples:
            template.append("")
            template.append("# Optional monitoring settings")
            template.append("# MONITORING_STATUS_UPDATE_INTERVAL=5")
            template.append("# MONITORING_RECONNECT_DELAY=10")
            template.append("# MONITORING_MAX_RECONNECT_ATTEMPTS=5")
        
        template.append("")
        
        # System settings
        template.append("# ============================================================================")
        template.append("# SYSTEM - Logging and Application")
        template.append("# ============================================================================")
        
        if include_examples:
            template.append("")
            template.append("# Optional system settings")
            template.append("# SYSTEM_LOG_LEVEL=INFO")
            template.append("# SYSTEM_MAX_LOG_FILE_SIZE=10485760")
            template.append("# SYSTEM_LOG_RETENTION_DAYS=7")
        
        template.append("")
        template.append("# End of configuration template")
        
        return "\n".join(template)
    
    def _generate_json_template(self, include_examples: bool) -> str:
        """Generate JSON configuration template."""
        template = {
            "_comment": "Kick Streamer Status Monitor Configuration Template",
            "_instructions": "Fill in your actual values and remove comments",
            
            "authentication": {
                "KICK_CLIENT_ID": "your_client_id_here",
                "KICK_CLIENT_SECRET": "your_client_secret_here"
            },
            
            "database": {
                "DATABASE_HOST": "localhost",
                "DATABASE_PORT": "5432",
                "DATABASE_NAME": "kick_monitor",
                "DATABASE_USER": "kick_monitor", 
                "DATABASE_PASSWORD": "your_password_here"
            }
        }
        
        if include_examples:
            template["optional_settings"] = {
                "_comment": "These are optional - remove if not needed",
                "AUTH_TOKEN_REFRESH_MARGIN": "300",
                "DATABASE_POOL_SIZE": "10",
                "MONITORING_STATUS_UPDATE_INTERVAL": "5",
                "SYSTEM_LOG_LEVEL": "INFO"
            }
        
        return json.dumps(template, indent=2)
    
    def _generate_yaml_template(self, include_examples: bool) -> str:
        """Generate YAML configuration template."""
        template = {
            '# Kick Streamer Status Monitor Configuration Template': None,
            '# Fill in your actual values': None,
            
            'authentication': {
                'KICK_CLIENT_ID': 'your_client_id_here',
                'KICK_CLIENT_SECRET': 'your_client_secret_here'
            },
            
            'database': {
                'DATABASE_HOST': 'localhost',
                'DATABASE_PORT': '5432',
                'DATABASE_NAME': 'kick_monitor',
                'DATABASE_USER': 'kick_monitor',
                'DATABASE_PASSWORD': 'your_password_here'
            }
        }
        
        if include_examples:
            template['optional_settings'] = {
                '# These are optional - remove if not needed': None,
                'AUTH_TOKEN_REFRESH_MARGIN': '300',
                'DATABASE_POOL_SIZE': '10',
                'MONITORING_STATUS_UPDATE_INTERVAL': '5',
                'SYSTEM_LOG_LEVEL': 'INFO'
            }
        
        return yaml.dump(template, default_flow_style=False)