"""Tests for the configuration module."""

import os
import tempfile
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import (
    Config,
    ExchangerConfig,
    DefaultFieldsConfig,
    NetworkConfig,
    parse_exchangers,
    load_env_file,
    load_json_config,
    load_config
)


class TestExchangerParsing:
    """Tests for exchanger configuration parsing."""

    def test_parse_comma_separated_ids(self):
        """Test parsing comma-separated exchanger IDs."""
        result = parse_exchangers("exc1,exc2,exc3")

        assert len(result) == 3
        assert result[0].id == "exc1"
        assert result[1].id == "exc2"
        assert result[2].id == "exc3"

    def test_parse_id_name_pairs(self):
        """Test parsing ID:name pairs."""
        result = parse_exchangers("id1:Name One,id2:Name Two")

        assert len(result) == 2
        assert result[0].id == "id1"
        assert result[0].name == "Name One"
        assert result[1].id == "id2"
        assert result[1].name == "Name Two"

    def test_parse_list_of_dicts(self):
        """Test parsing list of dictionaries."""
        result = parse_exchangers([
            {"id": "exc1", "name": "Exchanger 1", "enabled": True},
            {"id": "exc2", "name": "Exchanger 2", "url": "https://example.com"}
        ])

        assert len(result) == 2
        assert result[0].id == "exc1"
        assert result[0].name == "Exchanger 1"
        assert result[0].enabled is True
        assert result[1].url == "https://example.com"

    def test_parse_list_of_strings(self):
        """Test parsing list of strings."""
        result = parse_exchangers(["exc1", "exc2"])

        assert len(result) == 2
        assert result[0].id == "exc1"
        assert result[0].name == "exc1"

    def test_parse_empty(self):
        """Test parsing empty input."""
        assert parse_exchangers("") == []
        assert parse_exchangers([]) == []

    def test_parse_filters_empty_ids(self):
        """Test that empty IDs are filtered out."""
        result = parse_exchangers([{"id": ""}, {"id": "valid"}])

        assert len(result) == 1
        assert result[0].id == "valid"


class TestEnvFileLoading:
    """Tests for .env file loading."""

    def test_load_simple_env(self):
        """Test loading simple key=value pairs."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("KEY1=value1\n")
            f.write("KEY2=value2\n")
            f.name

        try:
            result = load_env_file(f.name)
            assert result["KEY1"] == "value1"
            assert result["KEY2"] == "value2"
        finally:
            os.unlink(f.name)

    def test_load_quoted_values(self):
        """Test loading quoted values."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write('KEY1="quoted value"\n')
            f.write("KEY2='single quoted'\n")

        try:
            result = load_env_file(f.name)
            assert result["KEY1"] == "quoted value"
            assert result["KEY2"] == "single quoted"
        finally:
            os.unlink(f.name)

    def test_load_comments_ignored(self):
        """Test that comments are ignored."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("# This is a comment\n")
            f.write("KEY=value\n")
            f.write("# Another comment\n")

        try:
            result = load_env_file(f.name)
            assert "KEY" in result
            assert "#" not in "".join(result.keys())
        finally:
            os.unlink(f.name)

    def test_nonexistent_file(self):
        """Test loading from non-existent file."""
        result = load_env_file("/nonexistent/path/.env")
        assert result == {}


class TestJSONConfigLoading:
    """Tests for JSON configuration loading."""

    def test_load_json_config(self):
        """Test loading JSON configuration."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{"update_interval_seconds": 60, "output_path": "./test.xml"}')

        try:
            result = load_json_config(f.name)
            assert result["update_interval_seconds"] == 60
            assert result["output_path"] == "./test.xml"
        finally:
            os.unlink(f.name)

    def test_load_complex_json(self):
        """Test loading complex JSON with nested structures."""
        config_data = {
            "exchangers": [
                {"id": "exc1", "name": "Exchanger 1"}
            ],
            "defaults": {
                "amount": "1000",
                "min_amount": "10"
            },
            "network": {
                "timeout_seconds": 60
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            import json
            json.dump(config_data, f)

        try:
            result = load_json_config(f.name)
            assert len(result["exchangers"]) == 1
            assert result["defaults"]["amount"] == "1000"
        finally:
            os.unlink(f.name)


class TestConfigDefaults:
    """Tests for configuration defaults."""

    def test_default_config_values(self):
        """Test that Config has sensible defaults."""
        config = Config()

        assert config.update_interval_seconds == 30
        assert config.output_path == "./request-exportxml.xml"
        assert config.output_encoding == "utf-8"
        assert config.http_enabled is False
        assert config.http_port == 8080
        assert config.validate_xml is True

    def test_default_fields_config(self):
        """Test DefaultFieldsConfig defaults."""
        defaults = DefaultFieldsConfig()

        assert defaults.amount == "0"
        assert defaults.min_amount == "0"
        assert defaults.max_amount == "999999999"
        assert defaults.param == "0"
        assert defaults.in_amount == "1"

    def test_network_config_defaults(self):
        """Test NetworkConfig defaults."""
        network = NetworkConfig()

        assert network.timeout_seconds == 30
        assert network.max_retries == 3
        assert network.retry_base_delay == 1.0
        assert network.retry_max_delay == 30.0


class TestConfigLoading:
    """Tests for the full config loading process."""

    def test_load_config_defaults(self):
        """Test loading config with defaults."""
        # Clear any environment variables that might interfere
        for key in list(os.environ.keys()):
            if key.startswith('EXNODE_') or key.startswith('UPDATE_'):
                del os.environ[key]

        config = load_config()

        assert config is not None
        assert config.update_interval_seconds == 30
        assert len(config.exchangers) > 0  # Has placeholder exchangers

    def test_load_config_from_env(self):
        """Test loading config from environment variables."""
        os.environ['UPDATE_INTERVAL_SECONDS'] = '45'
        os.environ['OUTPUT_PATH'] = './custom-output.xml'

        try:
            config = load_config()
            assert config.update_interval_seconds == 45
            assert config.output_path == './custom-output.xml'
        finally:
            del os.environ['UPDATE_INTERVAL_SECONDS']
            del os.environ['OUTPUT_PATH']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
