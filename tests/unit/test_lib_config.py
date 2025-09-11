"""
Unit tests for configuration management system.

Tests cover configuration loading, validation, environment handling,
and error scenarios for the configuration management module.
"""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, mock_open
from typing import Dict, Any

from src.lib.config import (
    ConfigurationManager,
    ConfigValidationResult,
    ValidationLevel,
    ValidationIssue,
    IssueSeverity,
    ConfigurationError,
    _ConfigValidator,
)


class TestValidationLevel:
    """Test ValidationLevel enum."""
    
    def test_validation_levels(self):
        """Test all validation levels exist."""
        assert ValidationLevel.STRICT == "strict"
        assert ValidationLevel.WARNING == "warning"
        assert ValidationLevel.PERMISSIVE == "permissive"


class TestIssueSeverity:
    """Test IssueSeverity enum."""
    
    def test_issue_severities(self):
        """Test all issue severities exist."""
        assert IssueSeverity.ERROR == "error"
        assert IssueSeverity.WARNING == "warning"
        assert IssueSeverity.INFO == "info"


class TestValidationIssue:
    """Test ValidationIssue data class."""
    
    def test_issue_creation(self):
        """Test creating validation issues."""
        issue = ValidationIssue(
            severity=IssueSeverity.ERROR,
            key="test.key",
            message="Test error message",
            value="invalid_value"
        )
        
        assert issue.severity == IssueSeverity.ERROR
        assert issue.key == "test.key"
        assert issue.message == "Test error message"
        assert issue.value == "invalid_value"
    
    def test_issue_to_dict(self):
        """Test converting issue to dictionary."""
        issue = ValidationIssue(
            severity=IssueSeverity.WARNING,
            key="test.key",
            message="Test warning"
        )
        
        result = issue.to_dict()
        assert result['severity'] == 'warning'
        assert result['key'] == 'test.key'
        assert result['message'] == 'Test warning'
        assert result['value'] is None


class TestConfigValidationResult:
    """Test ConfigValidationResult data class."""
    
    def test_validation_result_creation(self):
        """Test creating validation results."""
        issues = [
            ValidationIssue(IssueSeverity.ERROR, "key1", "Error message"),
            ValidationIssue(IssueSeverity.WARNING, "key2", "Warning message")
        ]
        
        result = ConfigValidationResult(
            is_valid=False,
            issues=issues
        )
        
        assert result.is_valid is False
        assert len(result.issues) == 2
        assert result.issues[0].severity == IssueSeverity.ERROR
    
    def test_has_errors(self):
        """Test checking for errors in validation result."""
        # No errors
        result = ConfigValidationResult(
            is_valid=True,
            issues=[ValidationIssue(IssueSeverity.WARNING, "key", "Warning")]
        )
        assert not result.has_errors()
        
        # Has errors
        result = ConfigValidationResult(
            is_valid=False,
            issues=[ValidationIssue(IssueSeverity.ERROR, "key", "Error")]
        )
        assert result.has_errors()
    
    def test_get_errors(self):
        """Test getting only errors from validation result."""
        issues = [
            ValidationIssue(IssueSeverity.ERROR, "key1", "Error 1"),
            ValidationIssue(IssueSeverity.WARNING, "key2", "Warning"),
            ValidationIssue(IssueSeverity.ERROR, "key3", "Error 2"),
        ]
        
        result = ConfigValidationResult(is_valid=False, issues=issues)
        errors = result.get_errors()
        
        assert len(errors) == 2
        assert all(issue.severity == IssueSeverity.ERROR for issue in errors)
    
    def test_get_warnings(self):
        """Test getting only warnings from validation result."""
        issues = [
            ValidationIssue(IssueSeverity.ERROR, "key1", "Error"),
            ValidationIssue(IssueSeverity.WARNING, "key2", "Warning 1"),
            ValidationIssue(IssueSeverity.WARNING, "key3", "Warning 2"),
        ]
        
        result = ConfigValidationResult(is_valid=False, issues=issues)
        warnings = result.get_warnings()
        
        assert len(warnings) == 2
        assert all(issue.severity == IssueSeverity.WARNING for issue in warnings)
    
    def test_to_dict(self):
        """Test converting validation result to dictionary."""
        issues = [ValidationIssue(IssueSeverity.ERROR, "key", "Error")]
        result = ConfigValidationResult(is_valid=False, issues=issues)
        
        data = result.to_dict()
        assert data['is_valid'] is False
        assert len(data['issues']) == 1
        assert data['issues'][0]['severity'] == 'error'


class TestConfigValidator:
    """Test _ConfigValidator class."""
    
    def test_validate_required_keys_success(self):
        """Test successful required key validation."""
        validator = _ConfigValidator()
        config = {'key1': 'value1', 'key2': 'value2'}
        required = ['key1', 'key2']
        
        issues = validator._validate_required_keys(config, required)
        assert len(issues) == 0
    
    def test_validate_required_keys_missing(self):
        """Test missing required key validation."""
        validator = _ConfigValidator()
        config = {'key1': 'value1'}
        required = ['key1', 'key2', 'key3']
        
        issues = validator._validate_required_keys(config, required)
        assert len(issues) == 2
        assert all(issue.severity == IssueSeverity.ERROR for issue in issues)
        assert 'key2' in [issue.key for issue in issues]
        assert 'key3' in [issue.key for issue in issues]
    
    def test_validate_type_conversions_success(self):
        """Test successful type conversion validation."""
        validator = _ConfigValidator()
        config = {'int_key': '123', 'bool_key': 'true', 'float_key': '3.14'}
        type_specs = {
            'int_key': int,
            'bool_key': bool,
            'float_key': float
        }
        
        converted, issues = validator._validate_type_conversions(config, type_specs)
        
        assert len(issues) == 0
        assert converted['int_key'] == 123
        assert converted['bool_key'] is True
        assert converted['float_key'] == 3.14
    
    def test_validate_type_conversions_failure(self):
        """Test failed type conversion validation."""
        validator = _ConfigValidator()
        config = {'int_key': 'not_a_number', 'bool_key': 'maybe'}
        type_specs = {'int_key': int, 'bool_key': bool}
        
        converted, issues = validator._validate_type_conversions(config, type_specs)
        
        assert len(issues) == 2
        assert all(issue.severity == IssueSeverity.ERROR for issue in issues)
    
    def test_validate_constraints_success(self):
        """Test successful constraint validation."""
        validator = _ConfigValidator()
        config = {'port': 8080, 'timeout': 30}
        constraints = {
            'port': {'min': 1, 'max': 65535},
            'timeout': {'min': 1}
        }
        
        issues = validator._validate_constraints(config, constraints)
        assert len(issues) == 0
    
    def test_validate_constraints_failure(self):
        """Test failed constraint validation."""
        validator = _ConfigValidator()
        config = {'port': 70000, 'timeout': -5}
        constraints = {
            'port': {'min': 1, 'max': 65535},
            'timeout': {'min': 1}
        }
        
        issues = validator._validate_constraints(config, constraints)
        assert len(issues) == 2
        assert all(issue.severity == IssueSeverity.ERROR for issue in issues)
    
    def test_convert_boolean_values(self):
        """Test boolean value conversion."""
        validator = _ConfigValidator()
        
        # True values
        for value in ['true', 'True', 'TRUE', '1', 'yes', 'on']:
            assert validator._convert_boolean(value) is True
        
        # False values
        for value in ['false', 'False', 'FALSE', '0', 'no', 'off']:
            assert validator._convert_boolean(value) is False
        
        # Invalid values
        with pytest.raises(ValueError):
            validator._convert_boolean('maybe')


class TestConfigurationManager:
    """Test ConfigurationManager class."""
    
    def test_initialization_defaults(self):
        """Test default initialization."""
        manager = ConfigurationManager(auto_load=False)
        
        assert manager.validation_level == ValidationLevel.STRICT
        assert manager.env_file is None
        assert len(manager._config) == 0
    
    def test_initialization_with_env_file(self):
        """Test initialization with env file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write('TEST_KEY=test_value\n')
            f.flush()
            
            try:
                manager = ConfigurationManager(env_file=f.name, auto_load=False)
                assert manager.env_file == Path(f.name)
            finally:
                os.unlink(f.name)
    
    @patch.dict(os.environ, {'TEST_KEY': 'env_value'})
    def test_load_from_environment(self):
        """Test loading configuration from environment."""
        manager = ConfigurationManager(auto_load=False)
        manager.load_from_environment(['TEST_KEY'])
        
        assert manager.get('TEST_KEY') == 'env_value'
        assert manager._config_sources['TEST_KEY'] == 'environment'
    
    def test_load_from_env_file(self):
        """Test loading configuration from .env file."""
        env_content = """
# Comment line
TEST_KEY1=value1
TEST_KEY2=value2
EMPTY_KEY=
        """
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write(env_content)
            f.flush()
            
            try:
                manager = ConfigurationManager(auto_load=False)
                manager.load_from_env_file(f.name)
                
                assert manager.get('TEST_KEY1') == 'value1'
                assert manager.get('TEST_KEY2') == 'value2'
                assert manager.get('EMPTY_KEY') == ''
                assert manager._config_sources['TEST_KEY1'] == f'env_file:{f.name}'
            finally:
                os.unlink(f.name)
    
    def test_load_from_env_file_not_found(self):
        """Test loading from non-existent .env file."""
        manager = ConfigurationManager(auto_load=False)
        
        with pytest.raises(ConfigurationError) as exc_info:
            manager.load_from_env_file('/nonexistent/file.env')
        assert "Environment file not found" in str(exc_info.value)
    
    def test_load_from_dict(self):
        """Test loading configuration from dictionary."""
        config_dict = {
            'key1': 'value1',
            'nested': {'key2': 'value2'}
        }
        
        manager = ConfigurationManager(auto_load=False)
        manager.load_from_dict(config_dict)
        
        assert manager.get('key1') == 'value1'
        assert manager.get('nested.key2') == 'value2'
        assert manager._config_sources['key1'] == 'dict'
    
    def test_set_and_get_config(self):
        """Test setting and getting configuration values."""
        manager = ConfigurationManager(auto_load=False)
        
        manager.set('test.key', 'test_value')
        assert manager.get('test.key') == 'test_value'
        assert manager.get('nonexistent.key') is None
        assert manager.get('nonexistent.key', 'default') == 'default'
    
    def test_get_nested_config(self):
        """Test getting nested configuration values."""
        manager = ConfigurationManager(auto_load=False)
        manager._config = {
            'level1': {
                'level2': {
                    'key': 'nested_value'
                }
            }
        }
        
        assert manager.get('level1.level2.key') == 'nested_value'
        assert manager.get('level1.level2') == {'key': 'nested_value'}
    
    def test_config_source_priority(self):
        """Test configuration source priority."""
        env_content = "TEST_KEY=env_file_value"
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write(env_content)
            f.flush()
            
            try:
                with patch.dict(os.environ, {'TEST_KEY': 'env_value'}):
                    manager = ConfigurationManager(env_file=f.name, auto_load=False)
                    
                    # Load in order: dict, env file, environment
                    manager.load_from_dict({'TEST_KEY': 'dict_value'})
                    manager.load_from_env_file(f.name)
                    manager.load_from_environment(['TEST_KEY'])
                    
                    # Environment should have highest priority
                    assert manager.get('TEST_KEY') == 'env_value'
                    assert manager._config_sources['TEST_KEY'] == 'environment'
            finally:
                os.unlink(f.name)
    
    def test_validate_strict_mode(self):
        """Test configuration validation in strict mode."""
        manager = ConfigurationManager(
            validation_level=ValidationLevel.STRICT,
            auto_load=False
        )
        manager._config = {'valid_key': 'valid_value'}
        
        # Should pass with valid configuration
        result = manager.validate(['valid_key'])
        assert result.is_valid is True
        assert len(result.issues) == 0
        
        # Should fail with missing required key
        result = manager.validate(['valid_key', 'missing_key'])
        assert result.is_valid is False
        assert result.has_errors()
    
    def test_validate_warning_mode(self):
        """Test configuration validation in warning mode."""
        manager = ConfigurationManager(
            validation_level=ValidationLevel.WARNING,
            auto_load=False
        )
        manager._config = {'valid_key': 'valid_value'}
        
        result = manager.validate(['valid_key', 'missing_key'])
        assert result.is_valid is True  # Still valid in warning mode
        assert len(result.get_warnings()) > 0  # But has warnings
    
    def test_validate_permissive_mode(self):
        """Test configuration validation in permissive mode."""
        manager = ConfigurationManager(
            validation_level=ValidationLevel.PERMISSIVE,
            auto_load=False
        )
        manager._config = {}
        
        result = manager.validate(['any_key'])
        assert result.is_valid is True
        assert len(result.issues) == 0
    
    def test_get_all_config(self):
        """Test getting all configuration."""
        manager = ConfigurationManager(auto_load=False)
        config_data = {
            'key1': 'value1',
            'key2': 'value2'
        }
        manager.load_from_dict(config_data)
        
        all_config = manager.get_all()
        assert all_config == config_data
    
    def test_get_config_sources(self):
        """Test getting configuration sources."""
        manager = ConfigurationManager(auto_load=False)
        manager.set('manual_key', 'manual_value')
        manager.load_from_dict({'dict_key': 'dict_value'})
        
        sources = manager.get_sources()
        assert 'manual_key' in sources
        assert 'dict_key' in sources
        assert sources['manual_key'] == 'manual'
        assert sources['dict_key'] == 'dict'
    
    def test_auto_load_functionality(self):
        """Test automatic loading on initialization."""
        env_content = "AUTO_LOAD_KEY=auto_value"
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write(env_content)
            f.flush()
            
            try:
                with patch.dict(os.environ, {'ENV_KEY': 'env_value'}):
                    manager = ConfigurationManager(
                        env_file=f.name,
                        auto_load=True
                    )
                    
                    # Should have loaded automatically
                    assert manager.get('AUTO_LOAD_KEY') == 'auto_value'
                    assert manager.get('ENV_KEY') == 'env_value'
            finally:
                os.unlink(f.name)


@pytest.fixture
def sample_config_manager():
    """Fixture providing a sample configuration manager."""
    manager = ConfigurationManager(auto_load=False)
    manager.load_from_dict({
        'database': {
            'host': 'localhost',
            'port': '5432',
            'name': 'kick_monitor'
        },
        'websocket': {
            'url': 'wss://ws-us2.pusher.com',
            'timeout': '30'
        },
        'logging': {
            'level': 'INFO'
        }
    })
    return manager


class TestConfigurationManagerFixture:
    """Test using the configuration manager fixture."""
    
    def test_fixture_usage(self, sample_config_manager):
        """Test that fixture provides valid configuration manager."""
        assert sample_config_manager.get('database.host') == 'localhost'
        assert sample_config_manager.get('websocket.url') == 'wss://ws-us2.pusher.com'
        assert sample_config_manager.get('logging.level') == 'INFO'
    
    def test_fixture_type_conversion(self, sample_config_manager):
        """Test type conversion with fixture data."""
        type_specs = {
            'database.port': int,
            'websocket.timeout': int
        }
        
        result = sample_config_manager.validate(
            required_keys=['database.host', 'database.port'],
            type_specs=type_specs
        )
        
        assert result.is_valid is True
        assert sample_config_manager.get('database.port') == 5432
        assert sample_config_manager.get('websocket.timeout') == 30


class TestConfigurationError:
    """Test ConfigurationError exception."""
    
    def test_configuration_error_creation(self):
        """Test creating configuration errors."""
        error = ConfigurationError("Test error message")
        assert str(error) == "Test error message"
        assert isinstance(error, Exception)
    
    def test_configuration_error_with_details(self):
        """Test configuration error with additional details."""
        error = ConfigurationError("Error", details={'key': 'value'})
        assert error.details == {'key': 'value'}
        assert "Error" in str(error)