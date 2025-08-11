#!/usr/bin/env python3
"""
BlockWatcher Runner - Thin orchestrator for OpenZeppelin Monitor's blockwatcher components

This script runs the Rust-based OpenZeppelin Monitor in blockwatcher-only mode by:
1. Creating minimal monitor configs (no filtering)
2. Creating empty trigger configs (no notifications)
3. Running the Rust binary with temporary configs
4. Monitoring block processing status
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional
import threading
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor


class BlockWatcherRunner:
    """Manages the OpenZeppelin Monitor process in blockwatcher-only mode"""

    def __init__(self, networks: Optional[List[str]] = None,
                 data_dir: str = "data",
                 config_dir: str = "config",
                 verbose: bool = False,
                 store_blocks: bool = False,
                 db_url: Optional[str] = None,
                 use_database: bool = False,
                 tenant_id: Optional[str] = None):
        self.networks = networks or []
        self.data_dir = Path(data_dir)
        self.config_dir = Path(config_dir)
        self.verbose = verbose
        self.store_blocks = store_blocks
        self.db_url = db_url or "postgres://ozuser:ozpassword@localhost:5433/oz_monitor"
        self.use_database = use_database
        self.tenant_id = tenant_id or "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"  # Default tenant
        self.process: Optional[subprocess.Popen] = None
        self.temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.stats: Dict[str, Dict] = {}

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def load_network_configs(self) -> Dict[str, dict]:
        """Load network configurations from database or files"""
        if self.use_database:
            return self.load_network_configs_from_db()
        else:
            return self.load_network_configs_from_files()

    def load_network_configs_from_db(self) -> Dict[str, dict]:
        """Load network configurations from database"""
        networks = {}

        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor(cursor_factory=RealDictCursor)

            # Query networks from database
            query = """
                SELECT * FROM networks
                WHERE tenant_id = %s
                AND active = true
                AND deleted_at IS NULL
            """

            if self.networks:
                # Filter by specific network slugs
                placeholders = ','.join(['%s'] * len(self.networks))
                query += f" AND slug IN ({placeholders})"
                cur.execute(query, [self.tenant_id] + self.networks)
            else:
                cur.execute(query, (self.tenant_id,))

            rows = cur.fetchall()

            for row in rows:
                # Convert database row to config format
                config = {
                    'name': row['name'],
                    'slug': row['slug'],
                    'network_type': row['network_type'],
                    'chain_id': row['chain_id'],
                    'network_passphrase': row['network_passphrase'],
                    'rpc_urls': row['rpc_urls'],
                    'block_time_ms': row['block_time_ms'],
                    'confirmation_blocks': row['confirmation_blocks'],
                    'cron_schedule': row['cron_schedule'],
                    'max_past_blocks': row['max_past_blocks'],
                    'store_blocks': row['store_blocks'] or self.store_blocks
                }

                # Format RPC URLs according to the expected structure
                # Expected format: [{"type_": "rpc", "url": {"type": "plain", "value": "..."}, "weight": 100}]
                if config['rpc_urls']:
                    formatted_urls = []
                    for rpc in config['rpc_urls']:
                        if isinstance(rpc, dict) and 'url' in rpc:
                            url_data = rpc['url']
                            if isinstance(url_data, dict) and 'value' in url_data:
                                # Already has nested structure, extract value
                                formatted_urls.append({
                                    "type_": "rpc",
                                    "url": {
                                        "type": "plain",
                                        "value": url_data['value']
                                    },
                                    "weight": rpc.get('weight', 100)
                                })
                            else:
                                # Plain URL string in url field
                                formatted_urls.append({
                                    "type_": "rpc",
                                    "url": {
                                        "type": "plain",
                                        "value": url_data
                                    },
                                    "weight": rpc.get('weight', 100)
                                })
                        else:
                            # Plain string URL
                            formatted_urls.append({
                                "type_": "rpc",
                                "url": {
                                    "type": "plain",
                                    "value": rpc
                                },
                                "weight": 100
                            })
                    config['rpc_urls'] = formatted_urls

                networks[row['slug']] = config
                if self.verbose:
                    print(f"✓ Loaded network from DB: {row['slug']}")

            cur.close()
            conn.close()

        except Exception as e:
            print(f"❌ Failed to load networks from database: {e}")
            sys.exit(1)

        if not networks:
            if self.networks:
                print(
                    f"❌ No matching networks found in database for: {self.networks}")
            else:
                print("❌ No network configurations found in database")
            sys.exit(1)

        return networks

    def load_network_configs_from_files(self) -> Dict[str, dict]:
        """Load network configurations from config/networks/"""
        networks = {}
        network_dir = self.config_dir / "networks"

        if not network_dir.exists():
            print(f"❌ Network config directory not found: {network_dir}")
            sys.exit(1)

        for config_file in network_dir.glob("*.json"):
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    slug = config.get('slug', config_file.stem)

                    # Filter by requested networks if specified
                    if self.networks and slug not in self.networks:
                        continue

                    networks[slug] = config
                    if self.verbose:
                        print(f"✓ Loaded network: {slug}")
            except Exception as e:
                print(f"⚠️  Failed to load {config_file}: {e}")

        if not networks:
            if self.networks:
                print(f"❌ No matching networks found for: {self.networks}")
            else:
                print("❌ No network configurations found")
            sys.exit(1)

        return networks

    def load_monitor_and_trigger_configs_from_db(self) -> tuple[List[dict], List[dict]]:
        """Load monitor and trigger configurations from database"""
        monitors = []
        triggers_map: Dict[str, Optional[dict]] = {}

        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor(cursor_factory=RealDictCursor)

            # Load active monitors
            cur.execute("""
                SELECT * FROM monitors
                WHERE tenant_id = %s
                AND active = true
                AND paused = false
                AND deleted_at IS NULL
            """, (self.tenant_id,))

            monitor_rows = cur.fetchall()

            for row in monitor_rows:
                monitor = {
                    'name': row['slug'],  # Use slug as name for compatibility
                    'paused': row['paused'],
                    'networks': row['networks'],
                    'addresses': row['addresses'],
                    'match_conditions': {
                        'functions': row['match_functions'],
                        'events': row['match_events'],
                        'transactions': row['match_transactions']
                    },
                    'trigger_conditions': row['trigger_conditions'],
                    'triggers': row['triggers']
                }
                monitors.append(monitor)

                # Collect trigger references (can be slugs or IDs)
                for trigger_ref in row['triggers']:
                    if isinstance(trigger_ref, dict) and 'id' in trigger_ref:
                        # It's a dict with an ID
                        trigger_id = trigger_ref['id']
                        if trigger_id not in triggers_map:
                            triggers_map[trigger_id] = None
                    elif isinstance(trigger_ref, str):
                        # It's a slug string - we'll look it up
                        if trigger_ref not in triggers_map:
                            triggers_map[trigger_ref] = None

            # Load referenced triggers
            if triggers_map:
                trigger_refs = list(triggers_map.keys())
                placeholders = ','.join(['%s'] * len(trigger_refs))

                # Get trigger metadata (by slug or ID)
                cur.execute(f"""
                    SELECT * FROM triggers
                    WHERE (slug IN ({placeholders}) OR id::text IN ({placeholders}))
                    AND tenant_id = %s
                    AND active = true
                    AND deleted_at IS NULL
                """, trigger_refs + trigger_refs + [self.tenant_id])

                trigger_rows = cur.fetchall()

                for row in trigger_rows:
                    trigger_id = str(row['id'])
                    trigger_slug = row['slug']
                    trigger_type = row['trigger_type']

                    trigger_config = {
                        'id': trigger_id,
                        'name': row['name'],
                        'slug': trigger_slug,
                        'type': trigger_type
                    }

                    # Load type-specific configuration in the expected format
                    if trigger_type == 'email':
                        cur.execute("""
                            SELECT * FROM email_triggers WHERE trigger_id = %s
                        """, (trigger_id,))
                        email_config = cur.fetchone()
                        if email_config:
                            # Format according to expected structure
                            trigger_config = {
                                trigger_slug: {
                                    'name': row['name'],
                                    'trigger_type': 'email',
                                    'config': {
                                        'host': email_config['host'],
                                        'port': email_config['port'],
                                        'username': {
                                            'type': 'plain',
                                            'value': email_config['username_value']
                                        },
                                        'password': {
                                            'type': 'plain',
                                            'value': email_config['password_value']
                                        },
                                        'sender': email_config['sender'],
                                        'recipients': email_config['recipients'],
                                        'message': {
                                            'title': email_config['message_title'],
                                            'body': email_config['message_body']
                                        }
                                    }
                                }
                            }

                    elif trigger_type == 'webhook':
                        cur.execute("""
                            SELECT * FROM webhook_triggers WHERE trigger_id = %s
                        """, (trigger_id,))
                        webhook_config = cur.fetchone()
                        if webhook_config:
                            # Format according to expected structure
                            trigger_config = {
                                trigger_slug: {
                                    'name': row['name'],
                                    'trigger_type': 'webhook',
                                    'config': {
                                        'url': {
                                            'type': 'plain',
                                            'value': webhook_config['url_value']
                                        },
                                        'method': webhook_config['method'],
                                        'headers': webhook_config['headers'] or {},
                                        'secret': {
                                            'type': 'plain',
                                            'value': webhook_config['secret_value']
                                        } if webhook_config['secret_value'] else None,
                                        'message': {
                                            'title': webhook_config['message_title'],
                                            'body': webhook_config['message_body']
                                        }
                                    }
                                }
                            }

                    # Store by both ID and slug for lookup
                    triggers_map[trigger_id] = trigger_config
                    triggers_map[trigger_slug] = trigger_config

            cur.close()
            conn.close()

            # Convert triggers_map to list
            triggers = [t for t in triggers_map.values() if t is not None]

            if self.verbose:
                print(
                    f"✓ Loaded {len(monitors)} monitors and {len(triggers)} triggers from database")

            return monitors, triggers

        except Exception as e:
            print(f"❌ Failed to load monitors/triggers from database: {e}")
            return [], []

    def create_minimal_configs(self, networks: Dict[str, dict]) -> Path:
        """Create minimal monitor and trigger configs in config directory or temp"""
        if self.use_database:
            # Use the main config directory directly
            config_path = self.config_dir

            # Create directory structure if needed
            (config_path / "monitors").mkdir(parents=True, exist_ok=True)
            (config_path / "triggers").mkdir(parents=True, exist_ok=True)
            (config_path / "networks").mkdir(parents=True, exist_ok=True)

            # Store existing file names to preserve non-DB configs
            existing_files = {}
            for subdir in ['monitors', 'triggers', 'networks']:
                existing_files[subdir] = set(f.name for f in (
                    config_path / subdir).glob('*.json'))

            if self.verbose:
                print(f"📁 Using config directory: {config_path}")
                print("📝 Writing database configs to main config directory")
        else:
            # Use temporary directory for file-based configs
            self.temp_dir = tempfile.TemporaryDirectory(prefix="blockwatcher_")
            config_path = Path(self.temp_dir.name)

            # Create directory structure
            (config_path / "monitors").mkdir(parents=True)
            (config_path / "triggers").mkdir(parents=True)
            (config_path / "networks").mkdir(parents=True)

        # Copy network configs (update store_blocks if needed)
        for slug, config in networks.items():
            config_copy = config.copy()
            if self.store_blocks:
                config_copy['store_blocks'] = True

            with open(config_path / "networks" / f"{slug}.json", 'w') as f:
                json.dump(config_copy, f, indent=2)

        if self.use_database:
            # Load monitors and triggers from database
            monitors, triggers = self.load_monitor_and_trigger_configs_from_db()

            # Write monitor configs
            if monitors:
                for i, monitor in enumerate(monitors):
                    filename = f"{monitor.get('name', f'monitor_{i}')}.json"
                    with open(config_path / "monitors" / filename, 'w') as f:
                        json.dump(monitor, f, indent=2)
            else:
                # Create minimal monitor if no monitors found
                print(
                    "⚠️  No active monitors found in database, creating minimal config")
                self._create_minimal_monitor_configs(config_path, networks)

            # Write trigger configs (each trigger file contains multiple trigger definitions)
            if triggers:
                # Group triggers into a single file (like the example configs)
                all_triggers = {}
                for trigger in triggers:
                    # Each trigger is already a dict with the slug as key
                    if isinstance(trigger, dict):
                        all_triggers.update(trigger)

                # Write all triggers to a single file
                with open(config_path / "triggers" / "database_triggers.json", 'w') as f:
                    json.dump(all_triggers, f, indent=2)
            else:
                # Create empty trigger config
                with open(config_path / "triggers" / "empty.json", 'w') as f:
                    json.dump({}, f)
        else:
            # Use minimal configs for file-based operation
            self._create_minimal_monitor_configs(config_path, networks)

            # Create empty trigger config
            with open(config_path / "triggers" / "empty.json", 'w') as f:
                json.dump({}, f)

        if self.verbose:
            print(f"✓ Created configs in: {config_path}")

        return config_path

    def _create_minimal_monitor_configs(self, temp_path: Path, networks: Dict[str, dict]):
        """Create minimal monitor configs for blockwatcher-only mode"""
        # Create minimal monitor config - one for each network type
        evm_networks = [slug for slug, cfg in networks.items()
                        if cfg.get('network_type') == 'EVM']
        stellar_networks = [slug for slug, cfg in networks.items()
                            if cfg.get('network_type') == 'Stellar']

        if evm_networks:
            evm_monitor = {
                "name": "blockwatcher_evm",
                "paused": False,
                "networks": evm_networks,
                "addresses": [{
                    "address": "0x0000000000000000000000000000000000000000"
                }],
                "match_conditions": {
                    "functions": [],
                    "events": [],
                    "transactions": [{
                        "status": "Success",
                        "expression": None
                    }]
                },
                "trigger_conditions": [],
                "triggers": []
            }

            with open(temp_path / "monitors" / "blockwatcher_evm.json", 'w') as f:
                json.dump(evm_monitor, f, indent=2)

        if stellar_networks:
            stellar_monitor = {
                "name": "blockwatcher_stellar",
                "paused": False,
                "networks": stellar_networks,
                "addresses": [{
                    "address": "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
                }],
                "match_conditions": {
                    "functions": [],
                    "events": [],
                    "transactions": [{
                        "status": "Success",
                        "expression": None
                    }]
                },
                "trigger_conditions": [],
                "triggers": []
            }

            with open(temp_path / "monitors" / "blockwatcher_stellar.json", 'w') as f:
                json.dump(stellar_monitor, f, indent=2)

    def build_command(self, config_path: Path) -> tuple[List[str], Dict[str, str]]:
        """Build the command to run the Rust binary"""
        binary_path = Path("./target/release/openzeppelin-monitor")

        # Check if release binary exists, fall back to debug
        if not binary_path.exists():
            binary_path = Path("./target/debug/openzeppelin-monitor")

        if not binary_path.exists():
            binary_path = Path("./openzeppelin-monitor")

        if not binary_path.exists():
            print("❌ OpenZeppelin Monitor binary not found!")
            print("  Please build the project first: cargo build --release")
            sys.exit(1)

        cmd = [str(binary_path)]

        # Set environment variables for config and data paths
        env = os.environ.copy()

        # CONFIG_DIR should point to where networks/, monitors/, triggers/ subdirs are
        env['CONFIG_DIR'] = str(config_path)
        env['LOG_DATA_DIR'] = str(self.data_dir)

        if self.verbose:
            env['RUST_LOG'] = 'info'
            print(f"🔍 CONFIG_DIR set to: {config_path}")
            print("🔍 Checking config files:")
            for subdir in ['networks', 'monitors', 'triggers']:
                path = config_path / subdir
                if path.exists():
                    files = list(path.glob('*.json'))
                    print(f"   {subdir}: {len(files)} files")
                    for f in files[:3]:  # Show first 3 files
                        print(f"     - {f.name}")
        else:
            env['RUST_LOG'] = 'warn'

        return cmd, env

    def monitor_blocks(self):
        """Monitor block processing status from data files"""
        print("\n📊 Block Processing Status")
        print("=" * 60)

        while self.running:
            try:
                # Read last block files
                for block_file in self.data_dir.glob("*_last_block.txt"):
                    network = block_file.stem.replace("_last_block", "")

                    try:
                        with open(block_file, 'r') as f:
                            block_num = int(f.read().strip())

                        # Update stats
                        if network not in self.stats:
                            self.stats[network] = {
                                'first_block': block_num,
                                'last_block': block_num,
                                'blocks_processed': 0,
                                'last_update': datetime.now()
                            }
                        else:
                            old_block = self.stats[network]['last_block']
                            if block_num > old_block:
                                self.stats[network]['last_block'] = block_num
                                self.stats[network]['blocks_processed'] = \
                                    block_num - \
                                    self.stats[network]['first_block']
                                self.stats[network]['last_update'] = datetime.now()

                                # Print update
                                print(f"\r[{datetime.now().strftime('%H:%M:%S')}] "
                                      f"{network}: Block #{block_num} "
                                      f"(+{block_num - old_block}) "
                                      f"Total: {self.stats[network]['blocks_processed']}",
                                      end="", flush=True)

                    except Exception as e:
                        if self.verbose:
                            print(f"\n⚠️  Error reading {block_file}: {e}")

                # Check for missed blocks
                for missed_file in self.data_dir.glob("*_missed_blocks.txt"):
                    network = missed_file.stem.replace("_missed_blocks", "")
                    try:
                        with open(missed_file, 'r') as f:
                            missed = f.read().strip().split('\n')
                            if missed and missed[0]:
                                print(
                                    f"\n⚠️  {network} has {len(missed)} missed blocks!")
                    except Exception:
                        pass

                time.sleep(1)

            except KeyboardInterrupt:
                break
            except Exception as e:
                if self.verbose:
                    print(f"\n❌ Monitor error: {e}")
                time.sleep(1)

    def run(self):
        """Run the blockwatcher"""
        print("🚀 OpenZeppelin Monitor BlockWatcher Runner")
        print("=" * 60)

        if self.use_database:
            print(
                f"📚 Using database configurations from: {self.db_url.split('@')[1] if '@' in self.db_url else 'database'}")
            print(f"👤 Tenant ID: {self.tenant_id}")
        else:
            print(f"📁 Using file-based configurations from: {self.config_dir}")

        # Load network configs
        networks = self.load_network_configs()
        print(
            f"📡 Monitoring {len(networks)} network(s): {', '.join(networks.keys())}")

        # Create minimal configs
        config_path = self.create_minimal_configs(networks)

        # Build command
        cmd, env = self.build_command(config_path)

        print(f"🔧 Running: {' '.join(cmd)}")
        print(f"📂 Data directory: {self.data_dir}")
        print(f"💾 Store blocks: {self.store_blocks}")
        print("-" * 60)

        # Start the process
        try:
            self.running = True

            # Start monitoring thread
            self.monitor_thread = threading.Thread(target=self.monitor_blocks)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()

            # Run the Rust binary
            self.process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE if not self.verbose else None,
                stderr=subprocess.PIPE if not self.verbose else None,
                text=True,
                bufsize=1
            )

            # Handle output if not verbose
            if not self.verbose and self.process.stdout:
                for line in iter(self.process.stdout.readline, ''):
                    if line and ('ERROR' in line or 'WARN' in line):
                        print(f"  {line.strip()}")

            # Wait for process
            self.process.wait()

        except KeyboardInterrupt:
            print("\n\n⏹️  Shutting down...")
            self.shutdown()
        except Exception as e:
            print(f"\n❌ Error: {e}")
            self.shutdown()
            sys.exit(1)
        finally:
            self.cleanup()

    def shutdown(self):
        """Gracefully shutdown the process"""
        self.running = False

        if self.process:
            # Send SIGTERM for graceful shutdown
            self.process.terminate()

            # Wait up to 10 seconds for graceful shutdown
            try:
                self.process.wait(timeout=10)
                print("✓ Process terminated gracefully")
            except subprocess.TimeoutExpired:
                # Force kill if not responding
                self.process.kill()
                print("⚠️  Process killed forcefully")

        # Print final statistics
        if self.stats:
            print("\n📈 Final Statistics:")
            print("-" * 40)
            for network, stats in self.stats.items():
                print(f"{network}:")
                print(f"  • Blocks processed: {stats['blocks_processed']}")
                print(f"  • Last block: {stats['last_block']}")
                print(
                    f"  • Last update: {stats['last_update'].strftime('%H:%M:%S')}")

    def cleanup(self):
        """Clean up temporary files"""
        if self.temp_dir:
            self.temp_dir.cleanup()
            if self.verbose:
                print("✓ Cleaned up temporary configs")
        elif self.use_database and self.verbose:
            print(f"ℹ️  Database configs written to: {self.config_dir}")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Run OpenZeppelin Monitor in blockwatcher-only mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run using file-based configs (default)
  %(prog)s

  # Run using database configs
  %(prog)s --use-database

  # Run for specific networks from database
  %(prog)s --use-database --networks ethereum_mainnet stellar_mainnet

  # Run with custom database URL and tenant
  %(prog)s --use-database --db-url postgres://user:pass@host:port/db --tenant-id your-tenant-id

  # Run with block storage enabled
  %(prog)s --store-blocks

  # Run with verbose logging
  %(prog)s --verbose
        """
    )

    parser.add_argument(
        '--networks',
        nargs='+',
        help='Specific networks to monitor (default: all)'
    )

    parser.add_argument(
        '--data-dir',
        default='data',
        help='Directory for block data (default: data)'
    )

    parser.add_argument(
        '--config-dir',
        default='config',
        help='Directory containing network configs (default: config)'
    )

    parser.add_argument(
        '--store-blocks',
        action='store_true',
        help='Store block data to disk (default: false)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    parser.add_argument(
        '--use-database',
        action='store_true',
        help='Load configurations from database instead of files'
    )

    parser.add_argument(
        '--db-url',
        default='postgres://ozuser:ozpassword@localhost:5433/oz_monitor',
        help='Database connection URL (default: postgres://ozuser:ozpassword@localhost:5433/oz_monitor)'
    )

    parser.add_argument(
        '--tenant-id',
        default='a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11',
        help='Tenant ID to use when loading from database'
    )

    args = parser.parse_args()

    # Run the blockwatcher
    runner = BlockWatcherRunner(
        networks=args.networks,
        data_dir=args.data_dir,
        config_dir=args.config_dir,
        verbose=args.verbose,
        store_blocks=args.store_blocks,
        use_database=args.use_database,
        db_url=args.db_url,
        tenant_id=args.tenant_id
    )

    runner.run()


if __name__ == '__main__':
    main()
