#!/usr/bin/env python3
"""Explore database schema for OpenZeppelin Monitor"""

import psycopg2
import json
from psycopg2.extras import RealDictCursor


def explore_database():
    """Connect to database and explore schema"""

    # Connection string provided by user
    conn_string = "postgres://ozuser:ozpassword@localhost:5433/oz_monitor"

    try:
        conn = psycopg2.connect(conn_string)
        print("✓ Connected to oz_monitor database")
    except Exception as e:
        print(f"❌ Could not connect to database: {e}")
        return

    cur = conn.cursor(cursor_factory=RealDictCursor)

    # List all tables in current database
    print("\n📋 Tables in oz_monitor database:")
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)
    tables = cur.fetchall()

    if not tables:
        print("  No tables found in public schema")
    else:
        for table in tables:
            table_name = table['table_name']
            print(f"\n  📊 Table: {table_name}")

            # Get column information
            cur.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name = %s AND table_schema = 'public'
                ORDER BY ordinal_position;
            """, (table_name,))
            columns = cur.fetchall()

            for col in columns:
                nullable = "NULL" if col['is_nullable'] == 'YES' else "NOT NULL"
                default = f" DEFAULT {col['column_default']}" if col['column_default'] else ""
                print(
                    f"    - {col['column_name']}: {col['data_type']} {nullable}{default}")

            # Get row count and sample data
            try:
                cur.execute(f"SELECT COUNT(*) as count FROM {table_name};")
                count = cur.fetchone()
                if count and 'count' in count:
                    print(f"    Row count: {count['count']}")

                    if count['count'] > 0:
                        cur.execute(f"SELECT * FROM {table_name} LIMIT 1;")
                        sample = cur.fetchone()
                        if sample:
                            print(
                                f"    Sample data: {json.dumps(dict(sample), default=str, indent=6)[:500]}...")
            except Exception as e:
                print(f"    Could not fetch data: {e}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    explore_database()
