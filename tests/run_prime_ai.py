"""Run PRIME AI regressions without opening the bot's actual storage backends.

Usage: PYTHONPATH=. python tests/run_prime_ai.py
"""

import os
import tempfile
import unittest

# This must happen before any PRIME persistence module is imported.
os.environ["PRIME_AI_DISABLE_DURABLE_STORE"] = "1"

import database


def main() -> int:
    original = database.DB_NAME
    with tempfile.TemporaryDirectory(prefix="prime-ai-regressions-") as directory:
        database.DB_NAME = os.path.join(directory, "isolated.db")
        try:
            suite = unittest.defaultTestLoader.loadTestsFromNames([
                "tests.test_prime_ai",
                "tests.test_prime_ai_intelligence",
                "tests.test_prime_ai_internals",
            ])
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            return 0 if result.wasSuccessful() else 1
        finally:
            database.DB_NAME = original


if __name__ == "__main__":
    raise SystemExit(main())
