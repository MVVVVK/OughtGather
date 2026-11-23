"""Retry a previously encrypted EPUB without fetching news or loading dedup state."""
import argparse
import sys
from src.delivery.service import retry_encrypted

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--encrypted', required=True, help='Authenticated encrypted delivery backup')
args = parser.parse_args()
sys.exit(0 if retry_encrypted(args.encrypted) else 1)
