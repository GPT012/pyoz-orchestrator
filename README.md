# pyoz-orchestrator

Python orchestrator for OpenZeppelin Monitor's blockchain monitoring components.

## Overview

A lightweight Python wrapper that orchestrates the Rust-based OpenZeppelin Monitor, enabling flexible blockchain monitoring with database-driven or file-based configurations.

## Features

- **Database Integration**: Load monitor configurations from PostgreSQL
- **Multi-Network Support**: Monitor EVM and Stellar networks simultaneously  
- **Flexible Configuration**: Switch between database and file-based configs
- **Real-time Monitoring**: Track block processing with live statistics
- **Multi-tenant Support**: Isolate configurations by tenant ID

## Installation

```bash
pip install psycopg2-binary
```

## Usage

### Database Mode

```bash
python blockwatcher_runner.py --use-database
```

### File Mode

```bash
python blockwatcher_runner.py --networks ethereum_mainnet stellar_mainnet
```

### Custom Database

```bash
python blockwatcher_runner.py --use-database \
  --db-url postgres://user:pass@host:port/db \
  --tenant-id your-tenant-id
```

## Configuration

### Database Schema

- `networks`: Network RPC configurations
- `monitors`: Monitoring rules and conditions
- `triggers`: Notification configurations
- `email_triggers`: Email notification details
- `webhook_triggers`: Webhook notification details

### Arguments

- `--use-database`: Enable database configuration mode
- `--networks`: Specific networks to monitor
- `--db-url`: PostgreSQL connection string
- `--tenant-id`: Tenant identifier for multi-tenancy
- `--store-blocks`: Persist block data to disk
- `--verbose`: Enable detailed logging
- `--data-dir`: Directory for block data storage
- `--config-dir`: Directory for configuration files

## Architecture

See [blockwatcher_architecture.md](blockwatcher_architecture.md) for detailed architecture documentation.

## Development

### Database Exploration

```bash
python explore_db.py
```

## License

See parent OpenZeppelin Monitor project for licensing information.
