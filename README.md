# Exnode Rate Exporter

A production-ready rate exporter for exnode.ru exchangers. Fetches exchange rates for configured exchangers and exports them in BestChange-compatible XML format.

## Features

- **Multi-exchanger support**: Monitor up to 3 (or more) exchangers simultaneously
- **Automatic updates**: Configurable update interval (default: 30 seconds)
- **BestChange-compatible XML**: Output format compatible with BestChange aggregator
- **Robust networking**: Timeouts, retries, exponential backoff
- **Fallback mechanisms**: Keeps serving last known good data if fetch fails
- **Flexible configuration**: YAML, JSON, environment variables, or .env files
- **Optional HTTP server**: Built-in endpoint for serving XML
- **Atomic file writes**: Never serves partially written files
- **Cross-platform**: Works on Linux, macOS, Windows (with .exe build option)

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/yourname/exnode-rate-exporter.git
cd exnode-rate-exporter

# Install dependencies
pip install -r requirements.txt

# Or install with pip
pip install -e .
```

### Configuration

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` or `config.yaml` with your exchanger IDs:
   ```yaml
   exchangers:
     - id: "your_exchanger_id_1"
       name: "Your Exchanger 1"
     - id: "your_exchanger_id_2"
       name: "Your Exchanger 2"
     - id: "your_exchanger_id_3"
       name: "Your Exchanger 3"
   ```

### Running

```bash
# Run as daemon (continuous updates)
python main.py

# Run once and exit (for cron/scheduled tasks)
python main.py --once

# Generate sample XML
python main.py --sample

# Use custom config file
python main.py -c /path/to/config.yaml

# Verbose logging
python main.py -v
```

## Configuration Options

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `EXCHANGERS` | Comma-separated exchanger IDs | `exchanger1,exchanger2,exchanger3` |
| `UPDATE_INTERVAL_SECONDS` | Update frequency | `30` |
| `OUTPUT_PATH` | Output XML file path | `./request-exportxml.xml` |
| `HTTP_ENABLED` | Enable HTTP server | `false` |
| `HTTP_PORT` | HTTP server port | `8080` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

### Config File (config.yaml)

```yaml
exchangers:
  - id: "exc1"
    name: "Exchanger 1"
    enabled: true

update_interval_seconds: 30
output_path: "./request-exportxml.xml"

http_enabled: true
http_port: 8080

defaults:
  amount: "0"
  min_amount: "0"
  max_amount: "999999999"
  param: "0"

network:
  timeout_seconds: 30
  max_retries: 3
```

## Output Format

The exporter generates BestChange-compatible XML:

```xml
<?xml version="1.0" encoding="utf-8"?>
<rates generated="2024-01-15T12:00:00" count="100">
  <item>
    <from>USDTTRC20</from>
    <to>SBERRUB</to>
    <in>1</in>
    <out>92.5</out>
    <amount>1000000</amount>
    <minamount>100</minamount>
    <maxamount>50000</maxamount>
    <param>0</param>
  </item>
  <!-- More items... -->
</rates>
```

## Deployment Options

### 1. Static File Hosting (Nginx/Apache)

Run the exporter to generate the XML file, then serve it with your web server:

```nginx
# nginx.conf
location /request-exportxml.xml {
    alias /path/to/request-exportxml.xml;
    add_header Content-Type application/xml;
    add_header Cache-Control "max-age=30";
}
```

### 2. Built-in HTTP Server

Enable the built-in HTTP server in configuration:

```yaml
http_enabled: true
http_port: 8080
```

Available endpoints:
- `/` or `/request-exportxml.xml` - XML feed
- `/health` - Health check
- `/metrics` - Service metrics

### 3. Docker

```bash
# Build image
docker build -t exnode-exporter .

# Run container
docker run -d \
  -p 8080:8080 \
  -v $(pwd)/output:/app/output \
  -e EXCHANGERS="exc1,exc2,exc3" \
  exnode-exporter
```

### 4. Docker Compose

```bash
docker-compose up -d
```

### 5. Systemd Service

Create `/etc/systemd/system/exnode-exporter.service`:

```ini
[Unit]
Description=Exnode Rate Exporter
After=network.target

[Service]
Type=simple
User=exnode
WorkingDirectory=/opt/exnode-exporter
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable exnode-exporter
sudo systemctl start exnode-exporter
```

### 6. Cron (Single Execution Mode)

```bash
# Run every minute
* * * * * cd /opt/exnode-exporter && python main.py --once >> /var/log/exnode.log 2>&1
```

### 7. Windows .exe

Build a standalone Windows executable:

```bash
pip install pyinstaller
pyinstaller --onefile --name exnode-exporter main.py
```

The executable will be in `dist/exnode-exporter.exe`.

## Testing

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_normalizer.py

# Run with coverage
pytest --cov=src tests/
```

## Project Structure

```
exnode-rate-exporter/
├── main.py              # Entry point
├── src/
│   ├── __init__.py
│   ├── config.py        # Configuration management
│   ├── fetcher.py       # HTTP data fetching
│   ├── parser.py        # Data parsing (JSON/HTML)
│   ├── normalizer.py    # Currency/rate normalization
│   ├── exporter_xml.py  # XML generation
│   ├── service.py       # Main service/scheduler
│   └── utils.py         # Utilities (retry, logging)
├── tests/
│   ├── test_config.py
│   ├── test_exporter.py
│   ├── test_normalizer.py
│   ├── test_parser.py
│   └── test_utils.py
├── config.yaml          # Default configuration
├── .env.example         # Environment variables template
├── requirements.txt     # Python dependencies
├── pyproject.toml       # Project metadata
├── Dockerfile           # Docker build file
└── docker-compose.yaml  # Docker Compose config
```

## Currency Ticker Normalization

The exporter normalizes currency tickers to BestChange format:

| Input | Normalized |
|-------|------------|
| `USDT-TRC20`, `usdt_trc20` | `USDTTRC20` |
| `Sberbank`, `SBER` | `SBERRUB` |
| `Tinkoff`, `TINK` | `TCSBRUB` |
| `Bitcoin`, `btc` | `BTC` |
| `QIWI` | `QWRUB` |

## Troubleshooting

### No data fetched

1. Check exchanger IDs are correct
2. Verify network connectivity to exnode.ru
3. Check logs for error messages: `python main.py -v`

### XML validation errors

1. Ensure all required fields have valid values
2. Check for special characters in currency names
3. Enable debug logging to see raw data

### High memory usage

1. Reduce number of exchangers
2. Increase update interval
3. Use `--once` mode with cron instead of daemon mode

## License

MIT License - see LICENSE file for details.
