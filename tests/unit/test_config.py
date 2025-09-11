"""
Unit tests for configuration validation and management.

Tests configuration loading, validation, environment variable handling,
and type conversion for the ConfigurationManager system.
"""

import os
import pytest
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any
from unittest.mock import patch, mock_open

from src.lib.config import (
    ConfigurationManager, ConfigurationError, ValidationLevel,
    ConfigValidationResult, get_config, load_config, reload_config
)


class TestConfigValidationResult:
    """Test ConfigValidationResult functionality."""
    
    def test_validation_result_creation(self):
        """Test creating validation result."""
        result = ConfigValidationResult(is_valid=True)
        assert result.is_valid is True
        assert len(result.errors) == 0
        assert len(result.warnings) == 0
        assert len(result.missing_required) == 0
        assert len(result.invalid_values) == 0
    
    def test_has_errors_property(self):
        """Test has_errors property."""
        result = ConfigValidationResult(is_valid=True)
        assert result.has_errors is False
        
        result.add_error("Test error")
        assert result.has_errors is True
        assert result.is_valid is False
    
    def test_has_warnings_property(self):
        """Test has_warnings property."""
        result = ConfigValidationResult(is_valid=True)
        assert result.has_warnings is False
        
        result.add_warning("Test warning")
        assert result.has_warnings is True
        assert result.is_valid is True  # Warnings don't affect validity
    
    def test_add_error(self):
        """Test adding error messages."""
        result = ConfigValidationResult(is_valid=True)
        result.add_error("First error")
        result.add_error("Second error")
        
        assert len(result.errors) == 2
        assert "First error" in result.errors
        assert "Second error" in result.errors
        assert result.is_valid is False
    
    def test_add_warning(self):
        """Test adding warning messages."""
        result = ConfigValidationResult(is_valid=True)
        result.add_warning("First warning")
        result.add_warning("Second warning")
        
        assert len(result.warnings) == 2
        assert "First warning" in result.warnings
        assert "Second warning" in result.warnings
        assert result.is_valid is True
    
    def test_add_missing_required(self):
        """Test adding missing required settings."""
        result = ConfigValidationResult(is_valid=True)
        result.add_missing_required("KICK_CLIENT_ID")
        result.add_missing_required("DATABASE_PASSWORD")
        
        assert len(result.missing_required) == 2
        assert "KICK_CLIENT_ID" in result.missing_required
        assert "DATABASE_PASSWORD" in result.missing_required
        assert result.is_valid is False
    
    def test_add_invalid_value(self):
        """Test adding invalid value errors."""
        result = ConfigValidationResult(is_valid=True)
        result.add_invalid_value("DATABASE_PORT", "Not a number")
        result.add_invalid_value("LOG_LEVEL", "Invalid level")
        
        assert len(result.invalid_values) == 2
        assert "DATABASE_PORT: Not a number" in result.invalid_values
        assert "LOG_LEVEL: Invalid level" in result.invalid_values
        assert result.is_valid is False


class TestConfigurationManager:
    """Test ConfigurationManager functionality."""
    
    @pytest.fixture
    def temp_env_file(self):
        """Create temporary .env file for testing."""
        temp_dir = tempfile.mkdtemp()
        env_file = Path(temp_dir) / ".env"
        
        env_content = """
# Test configuration file
KICK_CLIENT_ID=test_client_id
KICK_CLIENT_SECRET=test_client_secret
DATABASE_HOST=localhost
DATABASE_NAME=test_db
DATABASE_USER=test_user
DATABASE_PASSWORD=test_password
DATABASE_PORT=5432
SYSTEM_LOG_LEVEL=DEBUG
"""
        env_file.write_text(env_content.strip())
        
        yield env_file
        
        # Cleanup
        shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def config_manager_no_auto_load(self):
        """Create ConfigurationManager without auto-loading."""
        return ConfigurationManager(auto_load=False)
    
    def test_configuration_manager_creation(self):
        """Test creating ConfigurationManager."""
        with patch.object(ConfigurationManager, 'load_configuration'):
            config = ConfigurationManager(auto_load=False)
            assert config.validation_level == ValidationLevel.STRICT
            assert config._is_loaded is False
            assert len(config._config) == 0
    
    def test_configuration_manager_auto_load(self):
        """Test ConfigurationManager with auto_load=True."""
        with patch.object(ConfigurationManager, 'load_configuration') as mock_load:
            ConfigurationManager(auto_load=True)
            mock_load.assert_called_once()
    
    def test_load_configuration_basic(self, config_manager_no_auto_load):
        """Test basic configuration loading."""
        config = config_manager_no_auto_load
        
        with patch.object(config, '_load_defaults'), \
             patch.object(config, '_load_from_environment'), \
             patch.object(config, '_load_from_auto_detected_env_file'), \
             patch.object(config, '_apply_overrides'):
            
            config.load_configuration()
            assert config._is_loaded is True
    
    def test_load_from_env_file(self, config_manager_no_auto_load, temp_env_file):
        """Test loading configuration from .env file."""
        config = config_manager_no_auto_load
        config.env_file = temp_env_file
        
        config._load_from_env_file()
        
        assert config.get('KICK_CLIENT_ID') == 'test_client_id'
        assert config.get('KICK_CLIENT_SECRET') == 'test_client_secret'
        assert config.get('DATABASE_HOST') == 'localhost'
        assert config.get('DATABASE_PORT') == '5432'
        assert config.get_source('KICK_CLIENT_ID') == str(temp_env_file)
    
    def test_load_from_env_file_nonexistent(self, config_manager_no_auto_load):
        """Test loading from non-existent .env file."""
        config = config_manager_no_auto_load
        config.env_file = Path("/nonexistent/.env")
        
        # Should not raise exception
        config._load_from_env_file()
        assert len(config._config) == 0
    
    def test_load_from_environment(self, config_manager_no_auto_load):
        """Test loading configuration from environment variables."""
        config = config_manager_no_auto_load
        
        test_env = {
            'KICK_CLIENT_ID': 'env_client_id',
            'DATABASE_HOST': 'env_host',
            'UNRELATED_VAR': 'should_be_ignored'
        }
        
        with patch.dict(os.environ, test_env, clear=False):
            config._load_from_environment()
        
        assert config.get('KICK_CLIENT_ID') == 'env_client_id'
        assert config.get('DATABASE_HOST') == 'env_host'
        assert config.get('UNRELATED_VAR') is None
        assert config.get_source('KICK_CLIENT_ID') == 'environment'
    
    def test_configuration_priority_order(self, config_manager_no_auto_load, temp_env_file):
        """Test configuration priority order (env vars > .env file > defaults)."""
        config = config_manager_no_auto_load
        config.env_file = temp_env_file
        
        # Set environment variable that conflicts with .env file
        test_env = {'KICK_CLIENT_ID': 'env_override_value'}
        
        with patch.dict(os.environ, test_env, clear=False):
            config.load_configuration()
        
        # Environment variable should take priority
        assert config.get('KICK_CLIENT_ID') == 'env_override_value'
        assert config.get_source('KICK_CLIENT_ID') == 'environment'
        
        # .env file value should be used for non-overridden values
        assert config.get('DATABASE_HOST') == 'localhost'
    
    def test_get_methods(self, config_manager_no_auto_load):
        """Test configuration getter methods."""
        config = config_manager_no_auto_load
        
        # Set test values
        config.set('STRING_VAL', 'test_string')
        config.set('INT_VAL', '42')
        config.set('FLOAT_VAL', '3.14')
        config.set('BOOL_TRUE', 'true')
        config.set('BOOL_FALSE', 'false')
        config.set('LIST_VAL', 'item1,item2,item3')
        
        # Test get methods
        assert config.get('STRING_VAL') == 'test_string'
        assert config.get('NONEXISTENT', 'default') == 'default'
        
        assert config.get_int('INT_VAL') == 42
        assert config.get_int('NONEXISTENT', 99) == 99
        
        assert config.get_float('FLOAT_VAL') == 3.14
        assert config.get_float('NONEXISTENT', 1.5) == 1.5
        
        assert config.get_bool('BOOL_TRUE') is True
        assert config.get_bool('BOOL_FALSE') is False
        assert config.get_bool('NONEXISTENT', True) is True
        
        assert config.get_list('LIST_VAL') == ['item1', 'item2', 'item3']
        assert config.get_list('NONEXISTENT', ['default']) == ['default']
    
    def test_get_int_invalid_value(self, config_manager_no_auto_load):
        """Test get_int with invalid value."""
        config = config_manager_no_auto_load
        config.set('INVALID_INT', 'not_a_number')
        
        with patch('src.lib.config.logger') as mock_logger:
            result = config.get_int('INVALID_INT', 100)
            assert result == 100
            mock_logger.warning.assert_called_once()
    
    def test_get_float_invalid_value(self, config_manager_no_auto_load):
        """Test get_float with invalid value."""
        config = config_manager_no_auto_load
        config.set('INVALID_FLOAT', 'not_a_float')
        
        with patch('src.lib.config.logger') as mock_logger:
            result = config.get_float('INVALID_FLOAT', 5.5)
            assert result == 5.5
            mock_logger.warning.assert_called_once()
    
    def test_parse_bool_values(self, config_manager_no_auto_load):
        """Test boolean parsing with various input formats."""
        config = config_manager_no_auto_load
        
        # True values
        true_values = ['true', 'True', 'TRUE', '1', 'yes', 'YES', 'on', 'enabled']
        for val in true_values:
            assert config._parse_bool(val) is True, f"Failed for: {val}"
        
        # False values
        false_values = ['false', 'False', 'FALSE', '0', 'no', 'NO', 'off', 'disabled', '']
        for val in false_values:
            assert config._parse_bool(val) is False, f"Failed for: {val}"
        
        # Boolean inputs
        assert config._parse_bool(True) is True
        assert config._parse_bool(False) is False
    
    def test_validate_configuration_strict_mode(self, config_manager_no_auto_load):
        """Test configuration validation in strict mode."""
        config = config_manager_no_auto_load
        config.validation_level = ValidationLevel.STRICT
        config._is_loaded = True
        
        # Missing required settings
        result = config.validate_configuration()
        assert result.is_valid is False
        assert len(result.missing_required) > 0
        assert 'KICK_CLIENT_ID' in result.missing_required
        assert 'DATABASE_PASSWORD' in result.missing_required
    
    def test_validate_configuration_lenient_mode(self, config_manager_no_auto_load):
        """Test configuration validation in lenient mode."""
        config = config_manager_no_auto_load
        config.validation_level = ValidationLevel.LENIENT
        config._is_loaded = True
        
        result = config.validate_configuration()
        # Should have warnings instead of errors for missing required settings
        assert len(result.warnings) > 0
    
    def test_validate_configuration_with_valid_settings(self, config_manager_no_auto_load):
        """Test configuration validation with all required settings."""
        config = config_manager_no_auto_load
        config._is_loaded = True
        
        # Set all required settings
        required_settings = {
            'KICK_CLIENT_ID': 'test_client_id_123',
            'KICK_CLIENT_SECRET': 'test_client_secret_456789',
            'DATABASE_HOST': 'localhost',
            'DATABASE_NAME': 'test_db',
            'DATABASE_USER': 'test_user',
            'DATABASE_PASSWORD': 'test_password'
        }
        
        for key, value in required_settings.items():
            config.set(key, value)
        
        result = config.validate_configuration()
        assert result.is_valid is True
        assert len(result.errors) == 0
        assert len(result.missing_required) == 0
    
    def test_validate_database_config(self, config_manager_no_auto_load):
        """Test database-specific configuration validation."""
        config = config_manager_no_auto_load
        config._is_loaded = True
        
        # Test invalid port
        config.set('DATABASE_PORT', '99999')
        result = config.validate_configuration()
        assert any('DATABASE_PORT' in inv for inv in result.invalid_values)
        
        # Test invalid pool size
        config.set('DATABASE_PORT', '5432')  # Fix port
        config.set('DATABASE_POOL_SIZE', '150')  # Too high
        result = config.validate_configuration()
        assert any('DATABASE_POOL_SIZE' in warning for warning in result.warnings)
    
    def test_validate_auth_config(self, config_manager_no_auto_load):
        """Test authentication-specific configuration validation."""
        config = config_manager_no_auto_load
        config._is_loaded = True
        
        # Set short client ID (should warn)
        config.set('KICK_CLIENT_ID', 'short')
        config.set('KICK_CLIENT_SECRET', 'alsoshort')
        
        result = config.validate_configuration()
        warnings_text = ' '.join(result.warnings)
        assert 'KICK_CLIENT_ID' in warnings_text
        assert 'KICK_CLIENT_SECRET' in warnings_text
    
    def test_validate_monitoring_config(self, config_manager_no_auto_load):
        """Test monitoring-specific configuration validation."""
        config = config_manager_no_auto_load
        config._is_loaded = True
        
        # Test invalid update interval
        config.set('MONITORING_STATUS_UPDATE_INTERVAL', '0')
        result = config.validate_configuration()
        assert any('MONITORING_STATUS_UPDATE_INTERVAL' in inv for inv in result.invalid_values)
        
        # Test high update interval (should warn)
        config.set('MONITORING_STATUS_UPDATE_INTERVAL', '500')
        result = config.validate_configuration()
        assert any('MONITORING_STATUS_UPDATE_INTERVAL' in warning for warning in result.warnings)
    
    def test_get_database_config(self, config_manager_no_auto_load):
        """Test getting database configuration object."""
        config = config_manager_no_auto_load
        
        # Set database configuration
        config.set('DATABASE_HOST', 'test_host')
        config.set('DATABASE_PORT', '5433')
        config.set('DATABASE_NAME', 'test_db')
        config.set('DATABASE_USER', 'test_user')
        config.set('DATABASE_PASSWORD', 'test_pass')
        config.set('DATABASE_POOL_SIZE', '15')
        
        db_config = config.get_database_config()
        assert db_config.host == 'test_host'
        assert db_config.port == 5433
        assert db_config.database == 'test_db'
        assert db_config.username == 'test_user'
        assert db_config.password == 'test_pass'
        assert db_config.min_connections == 15
    
    def test_get_oauth_config(self, config_manager_no_auto_load):
        """Test getting OAuth configuration object."""
        config = config_manager_no_auto_load
        
        # Set OAuth configuration
        config.set('KICK_CLIENT_ID', 'test_client_id')
        config.set('KICK_CLIENT_SECRET', 'test_client_secret')
        config.set('AUTH_MAX_RETRY_ATTEMPTS', '5')
        
        oauth_config = config.get_oauth_config()
        assert oauth_config.client_id == 'test_client_id'
        assert oauth_config.client_secret == 'test_client_secret'
        assert oauth_config.max_retries == 5
    
    def test_get_oauth_config_missing_credentials(self, config_manager_no_auto_load):
        """Test getting OAuth config with missing credentials."""
        config = config_manager_no_auto_load
        
        with pytest.raises(ConfigurationError) as exc_info:
            config.get_oauth_config()
        
        assert "Missing required OAuth configuration" in str(exc_info.value)
    
    def test_get_pusher_config(self, config_manager_no_auto_load):
        """Test getting Pusher configuration object."""
        config = config_manager_no_auto_load
        
        # Set Pusher configuration
        config.set('PUSHER_APP_KEY', 'custom_app_key')
        config.set('PUSHER_CLUSTER', 'eu')
        config.set('MONITORING_RECONNECT_DELAY', '15')
        
        pusher_config = config.get_pusher_config()
        assert pusher_config.app_key == 'custom_app_key'
        assert pusher_config.cluster == 'eu'
        assert pusher_config.reconnect_interval == 15
    
    def test_export_to_env_file(self, config_manager_no_auto_load):
        """Test exporting configuration to .env file."""
        config = config_manager_no_auto_load
        
        # Set test configuration
        config.set('KICK_CLIENT_ID', 'test_id')
        config.set('KICK_CLIENT_SECRET', 'secret_value')
        config.set('DATABASE_HOST', 'localhost')
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.env') as f:
            temp_path = Path(f.name)
        
        try:
            config.export_to_env_file(temp_path, include_comments=True)
            
            content = temp_path.read_text()
            assert 'KICK_CLIENT_ID=' in content
            assert 'DATABASE_HOST=localhost' in content
            assert '# Authentication Configuration' in content
            assert 'your_kick_client_secret_here' in content  # Masked secret
            
        finally:
            temp_path.unlink()
    
    def test_export_to_env_file_no_comments(self, config_manager_no_auto_load):
        """Test exporting configuration without comments."""
        config = config_manager_no_auto_load
        config.set('TEST_VAR', 'test_value')
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.env') as f:
            temp_path = Path(f.name)
        
        try:
            config.export_to_env_file(temp_path, include_comments=False)
            
            content = temp_path.read_text()
            assert 'TEST_VAR=test_value' in content
            assert '#' not in content
            
        finally:
            temp_path.unlink()
    
    def test_reload_configuration(self, config_manager_no_auto_load):
        """Test reloading configuration."""
        config = config_manager_no_auto_load
        
        with patch.object(config, 'load_configuration') as mock_load:
            config.reload()
            mock_load.assert_called_once()
    
    def test_configuration_manager_repr(self, config_manager_no_auto_load):
        """Test string representation of ConfigurationManager."""
        config = config_manager_no_auto_load
        config._is_loaded = True
        config.set('TEST_VAR', 'value')
        
        repr_str = repr(config)
        assert 'ConfigurationManager' in repr_str
        assert 'loaded=True' in repr_str
        assert 'settings=1' in repr_str


class TestConfigurationManagerEdgeCases:
    """Test edge cases and error handling for ConfigurationManager."""
    
    def test_env_file_parsing_invalid_lines(self, config_manager_no_auto_load):
        """Test parsing .env file with invalid lines."""
        config = config_manager_no_auto_load
        
        invalid_env_content = """
VALID_VAR=valid_value
invalid_line_without_equals
# Comment line
ANOTHER_VALID=value

EMPTY_VALUE=
"""
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.env') as f:
            f.write(invalid_env_content)
            temp_path = Path(f.name)
        
        try:
            config.env_file = temp_path
            
            with patch('src.lib.config.logger') as mock_logger:
                config._load_from_env_file()
            
            # Should log warning for invalid line
            mock_logger.warning.assert_called()
            
            # Should still load valid variables
            assert config.get('VALID_VAR') == 'valid_value'
            assert config.get('ANOTHER_VALID') == 'value'
            assert config.get('EMPTY_VALUE') == ''
            
        finally:
            temp_path.unlink()
    
    def test_env_file_loading_error(self, config_manager_no_auto_load):
        """Test error handling when .env file loading fails."""
        config = config_manager_no_auto_load
        config.env_file = Path("/nonexistent/path/.env")
        
        # Mock file open to raise exception
        with patch('builtins.open', side_effect=PermissionError("Access denied")):
            with pytest.raises(ConfigurationError) as exc_info:
                config._load_from_env_file()
            
            assert "Failed to load .env file" in str(exc_info.value)
    
    def test_auto_detect_env_file_multiple_locations(self, config_manager_no_auto_load):
        """Test auto-detection of .env file from multiple locations."""
        config = config_manager_no_auto_load
        
        # Mock Path.exists to return True for second location
        with patch('pathlib.Path.exists') as mock_exists:
            mock_exists.side_effect = lambda: mock_exists.call_count == 2  # True on second call
            
            with patch.object(config, '_load_from_env_file') as mock_load:
                config._load_from_auto_detected_env_file()
                
                # Should have found and tried to load the second location
                mock_load.assert_called_once()
                assert config.env_file.name == '.env.local'
    
    def test_validation_not_loaded(self, config_manager_no_auto_load):
        """Test validation when configuration not loaded."""
        config = config_manager_no_auto_load
        # Don't load configuration
        
        result = config.validate_configuration()
        assert result.is_valid is False
        assert "Configuration not loaded" in result.errors
    
    def test_type_conversion_edge_cases(self, config_manager_no_auto_load):
        """Test type conversion edge cases."""
        config = config_manager_no_auto_load
        
        # Test list parsing with different separators
        config.set('COMMA_LIST', 'a,b,c')
        config.set('SEMICOLON_LIST', 'x;y;z')
        config.set('EMPTY_LIST', '')
        config.set('WHITESPACE_LIST', ' item1 , item2 , item3 ')
        
        assert config.get_list('COMMA_LIST') == ['a', 'b', 'c']
        assert config.get_list('SEMICOLON_LIST', separator=';') == ['x', 'y', 'z']
        assert config.get_list('EMPTY_LIST') == []
        assert config.get_list('WHITESPACE_LIST') == ['item1', 'item2', 'item3']
        
        # Test with list input (should return as-is)
        config.set('ALREADY_LIST', ['item1', 'item2'])
        assert config.get_list('ALREADY_LIST') == ['item1', 'item2']


class TestGlobalConfigurationFunctions:
    """Test global configuration management functions."""
    
    def test_get_config_singleton(self):
        """Test that get_config returns singleton instance."""
        with patch('src.lib.config._global_config', None):
            with patch.object(ConfigurationManager, '__init__', return_value=None) as mock_init:
                config1 = get_config()
                config2 = get_config()
                
                # Should only initialize once
                mock_init.assert_called_once()
                assert config1 is config2
    
    def test_load_config_replaces_global(self):
        """Test that load_config replaces global configuration."""
        with patch('src.lib.config._global_config', None):
            with patch.object(ConfigurationManager, '__init__', return_value=None) as mock_init:
                config = load_config(env_file=".env.test", validation_level=ValidationLevel.LENIENT)
                
                mock_init.assert_called_once_with(
                    env_file=".env.test", 
                    validation_level=ValidationLevel.LENIENT
                )
    
    def test_reload_config(self):
        """Test reloading global configuration."""
        with patch('src.lib.config.get_config') as mock_get_config:
            mock_config = mock_get_config.return_value
            
            result = reload_config()
            
            mock_get_config.assert_called_once()
            mock_config.reload.assert_called_once()
            assert result is mock_config


class TestValidationLevels:
    """Test different validation levels."""
    
    @pytest.fixture
    def config_with_missing_required(self):
        """Create config manager with missing required settings."""
        config = ConfigurationManager(auto_load=False)
        config._is_loaded = True
        # Don't set any required settings
        return config
    
    def test_strict_validation_level(self, config_with_missing_required):
        """Test strict validation level behavior."""
        config = config_with_missing_required
        config.validation_level = ValidationLevel.STRICT
        
        result = config.validate_configuration()
        assert result.is_valid is False
        assert len(result.errors) > 0
        assert len(result.missing_required) > 0
    
    def test_lenient_validation_level(self, config_with_missing_required):
        """Test lenient validation level behavior."""
        config = config_with_missing_required
        config.validation_level = ValidationLevel.LENIENT
        
        result = config.validate_configuration()
        # Should have warnings instead of errors
        assert len(result.warnings) > 0
        # But should still mark missing required settings
        assert len(result.missing_required) > 0
    
    def test_minimal_validation_level(self, config_with_missing_required):
        """Test minimal validation level behavior."""
        config = config_with_missing_required
        config.validation_level = ValidationLevel.MINIMAL
        
        result = config.validate_configuration()
        # Minimal validation should be very permissive
        # (Implementation would depend on actual minimal validation logic)
        assert isinstance(result, ConfigValidationResult)