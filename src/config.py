"""
Configuration management for the rate exporter.
Supports loading from environment variables, .env files, and YAML/JSON config files.
"""

import os
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExchangerConfig:
    """Configuration for a single exchanger."""
    id: str
    name: str
    url: Optional[str] = None
    enabled: bool = True


@dataclass
class DefaultFieldsConfig:
    """Default values for optional XML fields."""
    amount: str = "0"
    min_amount: str = "0"
    max_amount: str = "999999999"
    param: str = "0"
    in_amount: str = "1"


@dataclass
class NetworkConfig:
    """Network-related configuration."""
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0
    user_agent: str = "ExnodeRateExporter/1.0"


@dataclass
class Config:
    """Main application configuration."""
    # Exchanger settings
    exchangers: list[ExchangerConfig] = field(default_factory=list)

    # Exnode API settings
    exnode_base_url: str = "https://exnode.ru"
    exnode_api_endpoint: str = "/api/exchangers/{exchanger_id}/rates"
    exnode_directions_endpoint: str = "/api/exchangers/{exchanger_id}/directions"

    # Update settings
    update_interval_seconds: int = 30

    # Output settings
    output_path: str = "./request-exportxml.xml"
    output_encoding: str = "utf-8"

    # HTTP server settings (optional)
    http_enabled: bool = False
    http_host: str = "0.0.0.0"
    http_port: int = 8080

    # Default field values
    defaults: DefaultFieldsConfig = field(default_factory=DefaultFieldsConfig)

    # Network settings
    network: NetworkConfig = field(default_factory=NetworkConfig)

    # Logging
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # XML validation
    validate_xml: bool = True
    xsd_path: Optional[str] = None


def load_env_file(env_path: str = ".env") -> dict[str, str]:
    """Load environment variables from a .env file."""
    env_vars = {}
    path = Path(env_path)

    if not path.exists():
        return env_vars

    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    env_vars[key] = value
    except Exception as e:
        logger.warning(f"Failed to load .env file: {e}")

    return env_vars


def load_yaml_config(config_path: str) -> dict[str, Any]:
    """Load configuration from a YAML file."""
    try:
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        logger.warning("PyYAML not installed, skipping YAML config")
        return {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"Failed to load YAML config: {e}")
        return {}


def load_json_config(config_path: str) -> dict[str, Any]:
    """Load configuration from a JSON file."""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"Failed to load JSON config: {e}")
        return {}


def parse_exchangers(value: Any) -> list[ExchangerConfig]:
    """Parse exchanger configuration from various formats."""
    exchangers = []

    if isinstance(value, str):
        # Comma-separated list of IDs: "id1,id2,id3" or "id1:name1,id2:name2"
        for item in value.split(','):
            item = item.strip()
            if ':' in item:
                parts = item.split(':', 2)
                exchangers.append(ExchangerConfig(
                    id=parts[0].strip(),
                    name=parts[1].strip() if len(parts) > 1 else parts[0].strip(),
                    url=parts[2].strip() if len(parts) > 2 else None
                ))
            else:
                exchangers.append(ExchangerConfig(id=item, name=item))

    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                exchangers.append(ExchangerConfig(id=item, name=item))
            elif isinstance(item, dict):
                exchangers.append(ExchangerConfig(
                    id=str(item.get('id', '')),
                    name=str(item.get('name', item.get('id', ''))),
                    url=item.get('url'),
                    enabled=item.get('enabled', True)
                ))

    return [e for e in exchangers if e.id]


def load_config(
    config_file: Optional[str] = None,
    env_file: str = ".env"
) -> Config:
    """
    Load configuration from multiple sources with priority:
    1. Environment variables (highest)
    2. Config file (YAML/JSON)
    3. .env file
    4. Default values (lowest)
    """
    # Load .env file first
    env_vars = load_env_file(env_file)
    for key, value in env_vars.items():
        if key not in os.environ:
            os.environ[key] = value

    # Load config file if specified
    file_config: dict[str, Any] = {}
    if config_file:
        if config_file.endswith('.yaml') or config_file.endswith('.yml'):
            file_config = load_yaml_config(config_file)
        elif config_file.endswith('.json'):
            file_config = load_json_config(config_file)
    else:
        # Try default config files
        for default_file in ['config.yaml', 'config.yml', 'config.json']:
            if Path(default_file).exists():
                if default_file.endswith('.json'):
                    file_config = load_json_config(default_file)
                else:
                    file_config = load_yaml_config(default_file)
                break

    # Build configuration with priority: env > file > defaults
    def get_value(key: str, default: Any, env_key: Optional[str] = None) -> Any:
        env_key = env_key or key.upper().replace('.', '_')
        if env_key in os.environ:
            return os.environ[env_key]

        # Navigate nested dict for dotted keys
        parts = key.split('.')
        value = file_config
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    # Parse exchangers
    exchangers_raw = get_value('exchangers', [], 'EXCHANGERS')
    exchangers = parse_exchangers(exchangers_raw)

    # If no exchangers configured, use placeholder defaults
    if not exchangers:
        exchangers = [
            ExchangerConfig(id="exchanger1", name="Exchanger 1"),
            ExchangerConfig(id="exchanger2", name="Exchanger 2"),
            ExchangerConfig(id="exchanger3", name="Exchanger 3"),
        ]
        logger.warning("No exchangers configured, using placeholders")

    # Build defaults config
    defaults = DefaultFieldsConfig(
        amount=str(get_value('defaults.amount', '0', 'DEFAULT_AMOUNT')),
        min_amount=str(get_value('defaults.min_amount', '0', 'DEFAULT_MIN_AMOUNT')),
        max_amount=str(get_value('defaults.max_amount', '999999999', 'DEFAULT_MAX_AMOUNT')),
        param=str(get_value('defaults.param', '0', 'DEFAULT_PARAM')),
        in_amount=str(get_value('defaults.in_amount', '1', 'DEFAULT_IN_AMOUNT')),
    )

    # Build network config
    network = NetworkConfig(
        timeout_seconds=int(get_value('network.timeout_seconds', 30, 'NETWORK_TIMEOUT')),
        max_retries=int(get_value('network.max_retries', 3, 'NETWORK_MAX_RETRIES')),
        retry_base_delay=float(get_value('network.retry_base_delay', 1.0, 'NETWORK_RETRY_BASE_DELAY')),
        retry_max_delay=float(get_value('network.retry_max_delay', 30.0, 'NETWORK_RETRY_MAX_DELAY')),
        user_agent=str(get_value('network.user_agent', 'ExnodeRateExporter/1.0', 'USER_AGENT')),
    )

    # Build main config
    config = Config(
        exchangers=exchangers,
        exnode_base_url=str(get_value('exnode_base_url', 'https://exnode.ru', 'EXNODE_BASE_URL')),
        exnode_api_endpoint=str(get_value('exnode_api_endpoint', '/api/exchangers/{exchanger_id}/rates', 'EXNODE_API_ENDPOINT')),
        exnode_directions_endpoint=str(get_value('exnode_directions_endpoint', '/api/exchangers/{exchanger_id}/directions', 'EXNODE_DIRECTIONS_ENDPOINT')),
        update_interval_seconds=int(get_value('update_interval_seconds', 30, 'UPDATE_INTERVAL_SECONDS')),
        output_path=str(get_value('output_path', './request-exportxml.xml', 'OUTPUT_PATH')),
        output_encoding=str(get_value('output_encoding', 'utf-8', 'OUTPUT_ENCODING')),
        http_enabled=str(get_value('http_enabled', 'false', 'HTTP_ENABLED')).lower() in ('true', '1', 'yes'),
        http_host=str(get_value('http_host', '0.0.0.0', 'HTTP_HOST')),
        http_port=int(get_value('http_port', 8080, 'HTTP_PORT')),
        defaults=defaults,
        network=network,
        log_level=str(get_value('log_level', 'INFO', 'LOG_LEVEL')),
        log_format=str(get_value('log_format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s', 'LOG_FORMAT')),
        validate_xml=str(get_value('validate_xml', 'true', 'VALIDATE_XML')).lower() in ('true', '1', 'yes'),
        xsd_path=get_value('xsd_path', None, 'XSD_PATH'),
    )

    return config


# Singleton config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: Config) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config
