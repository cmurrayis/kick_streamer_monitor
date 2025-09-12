"""
Configuration data model with environment variable support.

Manages application configuration and runtime settings with proper
validation, encryption support, and environment variable integration.
"""

import os
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, Union, List
from pydantic import BaseModel, Field, validator, model_validator
from cryptography.fernet import Fernet
import base64
import json


class ConfigCategory(str, Enum):
    """Valid configuration categories."""
    AUTH = "auth"
    DATABASE = "database" 
    MONITORING = "monitoring"
    SYSTEM = "system"


class ConfigurationType(str, Enum):
    """Configuration value types."""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    JSON = "json"


class ConfigurationBase(BaseModel):
    """Base model for Configuration with validation."""
    
    key: str = Field(..., description="Configuration parameter name")
    value: str = Field(..., description="Configuration parameter value")
    description: Optional[str] = Field(None, description="Human-readable description")
    category: ConfigCategory = Field(ConfigCategory.SYSTEM, description="Configuration category")
    is_encrypted: bool = Field(False, description="Whether the value is encrypted at rest")
    value_type: ConfigurationType = Field(ConfigurationType.STRING, description="Type of the configuration value")
    is_sensitive: bool = Field(False, description="Whether this contains sensitive information")
    updated_by: str = Field("system", description="System or user that made the change")
    
    @validator('key')
    def validate_key(cls, v):
        """Validate configuration key format."""
        if not v or not v.strip():
            raise ValueError("Configuration key cannot be empty")
        
        # Key must follow naming convention: CATEGORY_PARAMETER_NAME
        key = v.strip().upper()
        if not re.match(r'^[A-Z][A-Z0-9_]*[A-Z0-9]$', key):
            raise ValueError(
                "Configuration key must follow format: CATEGORY_PARAMETER_NAME "
                "(uppercase letters, numbers, underscores only)"
            )
        
        return key
    
    @validator('value')
    def validate_value(cls, v):
        """Validate configuration value is not empty."""
        if not isinstance(v, str):
            v = str(v)
        return v
    
    @validator('description')
    def validate_description(cls, v):
        """Trim description if provided."""
        return v.strip() if v else None
    
    @model_validator(mode='before')
    @classmethod
    def validate_sensitive_encryption(cls, values):
        """Validate that sensitive values are marked for encryption."""
        if isinstance(values, dict):
            key = values.get('key', '')
            is_encrypted = values.get('is_encrypted', False)
            is_sensitive = values.get('is_sensitive', False)
            
            # Auto-detect sensitive keys
            sensitive_keywords = ['secret', 'password', 'token', 'key', 'credential', 'auth']
            key_lower = key.lower()
            
            if any(keyword in key_lower for keyword in sensitive_keywords):
                values['is_sensitive'] = True
                values['is_encrypted'] = True
            
            # Manual sensitive marking must have encryption
            if is_sensitive and not is_encrypted:
                values['is_encrypted'] = True
        
        return values
    
    @model_validator(mode='before')
    @classmethod
    def validate_category_key_consistency(cls, values):
        """Validate key matches category prefix."""
        if isinstance(values, dict):
            key = values.get('key', '')
            category = values.get('category')
            
            if category and key:
                expected_prefix = category.value.upper() + '_'
                if not key.startswith(expected_prefix):
                    raise ValueError(f"Key '{key}' should start with '{expected_prefix}' for category {category.value}")
        
        return values


class ConfigurationCreate(ConfigurationBase):
    """Model for creating a new configuration record."""
    pass


class ConfigurationUpdate(BaseModel):
    """Model for updating configuration values."""
    
    value: Optional[str] = None
    description: Optional[str] = None
    is_encrypted: Optional[bool] = None
    is_sensitive: Optional[bool] = None
    updated_by: Optional[str] = None
    
    @validator('value')
    def validate_value(cls, v):
        """Validate configuration value if provided."""
        if v is not None and not isinstance(v, str):
            v = str(v)
        return v
    
    @validator('description')
    def validate_description(cls, v):
        """Trim description if provided."""
        return v.strip() if v else None


class Configuration(ConfigurationBase):
    """Complete configuration model with database fields."""
    
    id: Optional[int] = Field(None, description="Database primary key")
    created_at: Optional[datetime] = Field(None, description="Record creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Record modification timestamp")
    
    class Config:
        """Pydantic configuration."""
        orm_mode = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }
    
    def get_typed_value(self) -> Union[str, int, float, bool, Dict, List]:
        """
        Get configuration value converted to its proper type.
        
        Returns:
            Value converted to the specified type
        """
        if self.value_type == ConfigurationType.STRING:
            return self.value
        elif self.value_type == ConfigurationType.INTEGER:
            return int(self.value)
        elif self.value_type == ConfigurationType.FLOAT:
            return float(self.value)
        elif self.value_type == ConfigurationType.BOOLEAN:
            return self.value.lower() in ('true', '1', 'yes', 'on')
        elif self.value_type == ConfigurationType.JSON:
            return json.loads(self.value)
        else:
            return self.value
    
    def set_typed_value(self, value: Union[str, int, float, bool, Dict, List]) -> str:
        """
        Set value from typed input.
        
        Args:
            value: Typed value to store
            
        Returns:
            String representation for storage
        """
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        elif isinstance(value, bool):
            return str(value).lower()
        else:
            return str(value)
    
    def mask_value(self) -> str:
        """
        Get masked version of value for display.
        
        Returns:
            Masked value if sensitive, otherwise original value
        """
        if self.is_sensitive:
            if len(self.value) <= 4:
                return "*" * len(self.value)
            else:
                return self.value[:2] + "*" * (len(self.value) - 4) + self.value[-2:]
        return self.value
    
    def to_dict(self, mask_sensitive: bool = False) -> Dict[str, Any]:
        """
        Convert to dictionary representation.
        
        Args:
            mask_sensitive: Whether to mask sensitive values
            
        Returns:
            Dictionary representation
        """
        data = self.dict(exclude_unset=True)
        
        if mask_sensitive and self.is_sensitive:
            data['value'] = self.mask_value()
        
        return data
    
    def __repr__(self) -> str:
        """String representation of configuration."""
        value_display = self.mask_value() if self.is_sensitive else self.value
        return f"<Configuration(key='{self.key}', value='{value_display}', category={self.category})>"


class ConfigurationEncryption:
    """Handles encryption/decryption of sensitive configuration values."""
    
    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize encryption handler.
        
        Args:
            encryption_key: Base64-encoded encryption key (generates new if None)
        """
        if encryption_key:
            self.key = encryption_key.encode()
        else:
            self.key = Fernet.generate_key()
        
        self.cipher = Fernet(self.key)
    
    @classmethod
    def generate_key(cls) -> str:
        """Generate a new encryption key."""
        return Fernet.generate_key().decode()
    
    def encrypt_value(self, value: str) -> str:
        """
        Encrypt a configuration value.
        
        Args:
            value: Plain text value to encrypt
            
        Returns:
            Encrypted value as base64 string
        """
        encrypted = self.cipher.encrypt(value.encode())
        return base64.b64encode(encrypted).decode()
    
    def decrypt_value(self, encrypted_value: str) -> str:
        """
        Decrypt a configuration value.
        
        Args:
            encrypted_value: Base64 encrypted value
            
        Returns:
            Decrypted plain text value
        """
        encrypted_bytes = base64.b64decode(encrypted_value.encode())
        decrypted = self.cipher.decrypt(encrypted_bytes)
        return decrypted.decode()


class EnvironmentConfigLoader:
    """Loads configuration from environment variables."""
    
    ENV_PREFIX = "KICK_MONITOR_"
    
    @classmethod
    def load_from_env(cls) -> List[ConfigurationCreate]:
        """
        Load configuration from environment variables.
        
        Returns:
            List of configuration objects from environment
        """
        configs = []
        
        for key, value in os.environ.items():
            if key.startswith(cls.ENV_PREFIX):
                config_key = key[len(cls.ENV_PREFIX):]
                category = cls._detect_category(config_key)
                
                config = ConfigurationCreate(
                    key=config_key,
                    value=value,
                    category=category,
                    description=f"Loaded from environment variable {key}",
                    updated_by="environment"
                )
                configs.append(config)
        
        return configs
    
    @classmethod
    def _detect_category(cls, key: str) -> ConfigCategory:
        """Detect configuration category from key."""
        key_upper = key.upper()
        
        if key_upper.startswith('AUTH_'):
            return ConfigCategory.AUTH
        elif key_upper.startswith('DATABASE_') or key_upper.startswith('DB_'):
            return ConfigCategory.DATABASE
        elif key_upper.startswith('MONITOR_') or key_upper.startswith('WEBSOCKET_'):
            return ConfigCategory.MONITORING
        else:
            return ConfigCategory.SYSTEM
    
    @classmethod
    def get_env_value(cls, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get environment variable value with prefix.
        
        Args:
            key: Configuration key (without prefix)
            default: Default value if not found
            
        Returns:
            Environment variable value or default
        """
        env_key = cls.ENV_PREFIX + key
        return os.getenv(env_key, default)
    
    @classmethod
    def set_env_value(cls, key: str, value: str) -> None:
        """
        Set environment variable with prefix.
        
        Args:
            key: Configuration key (without prefix)
            value: Value to set
        """
        env_key = cls.ENV_PREFIX + key
        os.environ[env_key] = value


class ConfigurationDefaults:
    """Default configuration values for the application."""
    
    DEFAULTS = {
        # Authentication
        "AUTH_TOKEN_REFRESH_MARGIN": ("300", "Seconds before token expiry to refresh", ConfigurationType.INTEGER),
        "AUTH_MAX_RETRY_ATTEMPTS": ("3", "Maximum OAuth retry attempts", ConfigurationType.INTEGER),
        
        # Database
        "DATABASE_POOL_SIZE": ("10", "Database connection pool size", ConfigurationType.INTEGER),
        "DATABASE_MAX_OVERFLOW": ("20", "Maximum database connection overflow", ConfigurationType.INTEGER),
        "DATABASE_QUERY_TIMEOUT": ("30", "Database query timeout in seconds", ConfigurationType.INTEGER),
        
        # Monitoring
        "MONITORING_STATUS_UPDATE_INTERVAL": ("5", "Status update interval in seconds", ConfigurationType.INTEGER),
        "MONITORING_RECONNECT_DELAY": ("10", "WebSocket reconnect delay in seconds", ConfigurationType.INTEGER),
        "MONITORING_MAX_RECONNECT_ATTEMPTS": ("5", "Maximum WebSocket reconnect attempts", ConfigurationType.INTEGER),
        
        # System
        "SYSTEM_LOG_LEVEL": ("INFO", "Application log level", ConfigurationType.STRING),
        "SYSTEM_MAX_LOG_FILE_SIZE": ("10485760", "Maximum log file size in bytes", ConfigurationType.INTEGER),
        "SYSTEM_LOG_RETENTION_DAYS": ("7", "Log file retention in days", ConfigurationType.INTEGER),
    }
    
    @classmethod
    def get_default_configurations(cls) -> List[ConfigurationCreate]:
        """
        Get list of default configuration objects.
        
        Returns:
            List of default configuration objects
        """
        configs = []
        
        for key, (value, description, value_type) in cls.DEFAULTS.items():
            category = cls._detect_category(key)
            
            config = ConfigurationCreate(
                key=key,
                value=value,
                description=description,
                category=category,
                value_type=value_type,
                updated_by="system_defaults"
            )
            configs.append(config)
        
        return configs
    
    @classmethod
    def _detect_category(cls, key: str) -> ConfigCategory:
        """Detect configuration category from key."""
        if key.startswith('AUTH_'):
            return ConfigCategory.AUTH
        elif key.startswith('DATABASE_'):
            return ConfigCategory.DATABASE
        elif key.startswith('MONITORING_'):
            return ConfigCategory.MONITORING
        elif key.startswith('SYSTEM_'):
            return ConfigCategory.SYSTEM
        else:
            return ConfigCategory.SYSTEM