"""
config.py — Load customer YAML configs and resolve env vars
"""

import os
import glob
import yaml
from pathlib import Path
from dotenv import load_dotenv
from src.logger import get_logger

load_dotenv()
log = get_logger()


def load_customer_config(yaml_path: str) -> dict:
    """
    Load a customer YAML file and inject values from .env.
    Raises ValueError if any required env var is missing.
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    customer = config["customer_name"]

    # Resolve portal URL
    url_key = config["portal_url_env"]
    config["portal_url"] = _require_env(url_key, customer)

    # Resolve credentials
    config["username"] = _require_env(config["auth"]["username_env"], customer)
    config["password"] = _require_env(config["auth"]["password_env"], customer)

    # Resolve sheet ID
    config["sheet_id"] = _require_env(
        config["google_sheet"]["sheet_id_env"], customer
    )

    log.debug(f"Loaded config for {customer} ({config['portal_url']})")
    return config


def load_all_customers(customers_dir: str = "customers") -> list[dict]:
    """Load all YAML files from the customers/ directory."""
    yaml_files = glob.glob(os.path.join(customers_dir, "*.yaml"))

    if not yaml_files:
        log.warning(f"No customer YAML files found in {customers_dir}/")
        return []

    configs = []
    for f in sorted(yaml_files):
        try:
            cfg = load_customer_config(f)
            configs.append(cfg)
            log.info(f"✅ Loaded customer: {cfg['customer_name']}")
        except ValueError as e:
            log.error(f"❌ Skipping {f}: {e}")

    return configs


def _require_env(key: str, customer: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise ValueError(
            f"Missing env var '{key}' required for customer '{customer}'. "
            f"Add it to your .env file."
        )
    return val
