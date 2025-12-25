#!/usr/bin/env python3
"""
Exnode Rate Exporter - Main entry point.

A production-ready rate exporter for exnode.ru exchangers.
Exports exchange rates in BestChange-compatible XML format.

Usage:
    python main.py                  # Run as daemon
    python main.py --once           # Run once and exit
    python main.py --sample         # Generate sample XML
    python main.py -c config.yaml   # Use custom config file

For more information:
    python main.py --help
"""

import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.service import main

if __name__ == '__main__':
    sys.exit(main())
