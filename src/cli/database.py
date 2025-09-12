"""
CLI database commands for Kick Streamer Status Monitor.

Provides commands for database operations including schema migration,
health checking, and maintenance operations.
"""

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from models import ConfigurationDefaults
from services import DatabaseService, DatabaseConfig
from lib.logging import get_logger


class DatabaseCommandError(Exception):
    """Database command specific errors."""
    pass


class DatabaseCommands:
    """Database management CLI commands."""
    
    def __init__(self):
        self.logger = get_logger(__name__)
    
    async def migrate(self, args: argparse.Namespace) -> int:
        """Run database schema migration."""
        try:
            config_file = getattr(args, 'config', None)
            force = getattr(args, 'force', False)
            
            print("Running database schema migration...")
            
            # Load database configuration
            db_config = self._load_database_config(config_file)
            if not db_config:
                print("Error: Could not load database configuration")
                return 2
            
            # Connect to database
            db_service = DatabaseService(db_config)
            await db_service.connect()
            
            try:
                # Check if database is accessible
                health = await db_service.health_check()
                if health['status'] != 'healthy':
                    print(f"Error: Database is not healthy: {health.get('error')}")
                    if not force:
                        print("Use --force to attempt migration anyway")
                        return 2
                
                # Load schema SQL
                schema_path = Path(__file__).parent.parent / "models" / "schema.sql"
                if not schema_path.exists():
                    print(f"Error: Schema file not found: {schema_path}")
                    return 1
                
                print(f"Loading schema from: {schema_path}")
                
                with open(schema_path, 'r') as f:
                    schema_sql = f.read()
                
                # Check current schema version if possible
                try:
                    current_version = await db_service.get_configuration("SCHEMA_VERSION")
                    if current_version:
                        print(f"Current schema version: {current_version.value}")
                except Exception:
                    print("No existing schema version found")
                
                # Ask for confirmation unless forced
                if not force:
                    print("\nThis will create/update database tables and indexes.")
                    print("Existing data will be preserved, but schema changes may be applied.")
                    
                    confirm = input("Continue? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("Migration cancelled")
                        return 0
                
                print("\nExecuting schema migration...")
                
                # Execute migration
                await db_service.execute_schema_migration(schema_sql)
                
                # Insert default configurations
                print("Setting up default configuration...")
                defaults = ConfigurationDefaults.get_default_configurations()
                
                for default_config in defaults:
                    try:
                        existing = await db_service.get_configuration(default_config.key)
                        if not existing:
                            await db_service.create_configuration(default_config)
                            print(f"  • Added: {default_config.key}")
                        else:
                            print(f"  • Exists: {default_config.key}")
                    except Exception as e:
                        print(f"  • Error adding {default_config.key}: {e}")
                
                print("\n✓ Database migration completed successfully")
                
                # Show final status
                final_stats = await db_service.get_database_stats()
                print(f"\nDatabase Statistics:")
                for key, value in final_stats.items():
                    if isinstance(value, int):
                        print(f"  • {key.replace('_', ' ').title()}: {value:,}")
                    else:
                        print(f"  • {key.replace('_', ' ').title()}: {value}")
                
                return 0
            
            finally:
                await db_service.disconnect()
        
        except Exception as e:
            self.logger.error(f"Database migration failed: {e}")
            print(f"Error: {e}")
            return 1
    
    async def health(self, args: argparse.Namespace) -> int:
        """Check database health and connectivity."""
        try:
            config_file = getattr(args, 'config', None)
            output_format = getattr(args, 'format', 'table')
            
            print("Checking database health...")
            
            # Load database configuration
            db_config = self._load_database_config(config_file)
            if not db_config:
                print("Error: Could not load database configuration")
                return 2
            
            # Test database connection
            db_service = DatabaseService(db_config)
            
            health_info = {
                'connection': {},
                'health': {},
                'statistics': {},
                'schema': {},
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            # Test connection
            try:
                print("Testing database connection...")
                await db_service.connect()
                health_info['connection'] = {
                    'status': 'success',
                    'host': db_config.host,
                    'port': db_config.port,
                    'database': db_config.database,
                    'username': db_config.username
                }
                print("✓ Connection successful")
                
                # Get health check
                print("Running health check...")
                health = await db_service.health_check()
                health_info['health'] = health
                
                if health['status'] == 'healthy':
                    print("✓ Database health OK")
                else:
                    print(f"⚠ Database health issues: {health.get('error')}")
                
                # Get statistics
                print("Gathering statistics...")
                stats = await db_service.get_database_stats()
                health_info['statistics'] = stats
                
                # Check schema version
                print("Checking schema...")
                try:
                    schema_version = await db_service.get_configuration("SCHEMA_VERSION")
                    health_info['schema'] = {
                        'version': schema_version.value if schema_version else 'unknown',
                        'status': 'found' if schema_version else 'missing'
                    }
                    
                    if schema_version:
                        print(f"✓ Schema version: {schema_version.value}")
                    else:
                        print("⚠ Schema version not found")
                
                except Exception as e:
                    health_info['schema'] = {
                        'status': 'error',
                        'error': str(e)
                    }
                    print(f"⚠ Schema check error: {e}")
                
                await db_service.disconnect()
                
                # Output results
                if output_format == 'json':
                    print("\n" + json.dumps(health_info, indent=2))
                else:
                    self._output_health_table(health_info)
                
                # Determine return code
                if health['status'] == 'healthy' and schema_version:
                    return 0
                else:
                    return 1
            
            except Exception as e:
                health_info['connection'] = {
                    'status': 'failed',
                    'error': str(e)
                }
                
                if output_format == 'json':
                    print(json.dumps(health_info, indent=2))
                else:
                    print(f"✗ Connection failed: {e}")
                
                return 2
        
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            print(f"Error: {e}")
            return 1
    
    async def reset(self, args: argparse.Namespace) -> int:
        """Reset database (dangerous operation)."""
        try:
            config_file = getattr(args, 'config', None)
            confirm = getattr(args, 'confirm', False)
            
            print("WARNING: This will delete all data in the database!")
            print("This operation cannot be undone.")
            
            if not confirm:
                print("\nThis operation requires explicit confirmation.")
                confirm_input = input("Type 'DELETE ALL DATA' to confirm: ").strip()
                if confirm_input != 'DELETE ALL DATA':
                    print("Reset cancelled")
                    return 0
            
            # Load database configuration
            db_config = self._load_database_config(config_file)
            if not db_config:
                print("Error: Could not load database configuration")
                return 2
            
            # Connect to database
            db_service = DatabaseService(db_config)
            await db_service.connect()
            
            try:
                print("Dropping all tables...")
                
                # Drop tables in correct order (reverse dependency order)
                drop_commands = [
                    "DROP TABLE IF EXISTS status_event CASCADE;",
                    "DROP TABLE IF EXISTS configuration CASCADE;",
                    "DROP TABLE IF EXISTS streamer CASCADE;",
                ]
                
                for command in drop_commands:
                    await db_service.execute_schema_migration(command)
                
                print("✓ All tables dropped")
                
                # Optionally recreate schema
                recreate = input("Recreate schema? (y/N): ").strip().lower()
                if recreate == 'y':
                    print("Recreating schema...")
                    
                    # Load and execute schema
                    schema_path = Path(__file__).parent.parent / "models" / "schema.sql"
                    if schema_path.exists():
                        with open(schema_path, 'r') as f:
                            schema_sql = f.read()
                        
                        await db_service.execute_schema_migration(schema_sql)
                        print("✓ Schema recreated")
                    else:
                        print("⚠ Schema file not found, manual recreation needed")
                
                return 0
            
            finally:
                await db_service.disconnect()
        
        except Exception as e:
            self.logger.error(f"Database reset failed: {e}")
            print(f"Error: {e}")
            return 1
    
    async def backup(self, args: argparse.Namespace) -> int:
        """Create database backup."""
        try:
            config_file = getattr(args, 'config', None)
            output_file = getattr(args, 'output', None)
            
            if not output_file:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_file = f"kick_monitor_backup_{timestamp}.sql"
            
            print(f"Creating database backup: {output_file}")
            
            # Load database configuration
            db_config = self._load_database_config(config_file)
            if not db_config:
                print("Error: Could not load database configuration")
                return 2
            
            # Use pg_dump for PostgreSQL backup
            import subprocess
            
            # Build pg_dump command
            pg_dump_cmd = [
                'pg_dump',
                f'--host={db_config.host}',
                f'--port={db_config.port}',
                f'--username={db_config.username}',
                f'--dbname={db_config.database}',
                '--verbose',
                '--clean',
                '--no-owner',
                '--no-privileges',
                f'--file={output_file}'
            ]
            
            # Set password environment variable
            env = os.environ.copy()
            env['PGPASSWORD'] = db_config.password
            
            try:
                result = subprocess.run(
                    pg_dump_cmd,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True
                )
                
                print(f"✓ Backup created successfully: {output_file}")
                return 0
                
            except subprocess.CalledProcessError as e:
                print(f"✗ Backup failed: {e}")
                if e.stderr:
                    print(f"Error details: {e.stderr}")
                return 1
            except FileNotFoundError:
                print("Error: pg_dump command not found")
                print("Please install PostgreSQL client tools")
                return 8
        
        except Exception as e:
            self.logger.error(f"Database backup failed: {e}")
            print(f"Error: {e}")
            return 1
    
    async def restore(self, args: argparse.Namespace) -> int:
        """Restore database from backup."""
        try:
            backup_file = args.backup_file
            config_file = getattr(args, 'config', None)
            force = getattr(args, 'force', False)
            
            if not os.path.exists(backup_file):
                print(f"Error: Backup file not found: {backup_file}")
                return 1
            
            print(f"Restoring database from: {backup_file}")
            
            if not force:
                print("WARNING: This will replace all existing data!")
                confirm = input("Continue? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("Restore cancelled")
                    return 0
            
            # Load database configuration
            db_config = self._load_database_config(config_file)
            if not db_config:
                print("Error: Could not load database configuration")
                return 2
            
            # Use psql for PostgreSQL restore
            import subprocess
            
            # Build psql command
            psql_cmd = [
                'psql',
                f'--host={db_config.host}',
                f'--port={db_config.port}',
                f'--username={db_config.username}',
                f'--dbname={db_config.database}',
                '--file', backup_file
            ]
            
            # Set password environment variable
            env = os.environ.copy()
            env['PGPASSWORD'] = db_config.password
            
            try:
                result = subprocess.run(
                    psql_cmd,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True
                )
                
                print("✓ Database restored successfully")
                return 0
                
            except subprocess.CalledProcessError as e:
                print(f"✗ Restore failed: {e}")
                if e.stderr:
                    print(f"Error details: {e.stderr}")
                return 1
            except FileNotFoundError:
                print("Error: psql command not found")
                print("Please install PostgreSQL client tools")
                return 8
        
        except Exception as e:
            self.logger.error(f"Database restore failed: {e}")
            print(f"Error: {e}")
            return 1
    
    def _output_health_table(self, health_info: Dict[str, Any]) -> None:
        """Output health check results as table."""
        print("\n" + "="*60)
        print("DATABASE HEALTH REPORT")
        print("="*60)
        
        # Connection info
        connection = health_info.get('connection', {})
        print(f"Connection Status: {connection.get('status', 'unknown').upper()}")
        if connection.get('status') == 'success':
            print(f"Host: {connection.get('host')}:{connection.get('port')}")
            print(f"Database: {connection.get('database')}")
            print(f"Username: {connection.get('username')}")
        elif connection.get('error'):
            print(f"Connection Error: {connection.get('error')}")
        
        # Health status
        health = health_info.get('health', {})
        print(f"\nHealth Status: {health.get('status', 'unknown').upper()}")
        if health.get('pool_stats'):
            pool_stats = health['pool_stats']
            print(f"Connection Pool:")
            print(f"  • Size: {pool_stats.get('size')}/{pool_stats.get('max_size')}")
            print(f"  • Idle: {pool_stats.get('idle_size')}")
        
        # Schema info
        schema = health_info.get('schema', {})
        print(f"\nSchema Status: {schema.get('status', 'unknown').upper()}")
        if schema.get('version'):
            print(f"Schema Version: {schema.get('version')}")
        
        # Statistics
        statistics = health_info.get('statistics', {})
        if statistics:
            print(f"\nDatabase Statistics:")
            for key, value in statistics.items():
                if isinstance(value, int):
                    print(f"  • {key.replace('_', ' ').title()}: {value:,}")
                else:
                    print(f"  • {key.replace('_', ' ').title()}: {value}")
        
        print(f"\nReport generated: {health_info.get('timestamp')}")
    
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