import json
import logging

logger = logging.getLogger("repopilot.service")


def event(name: str, **fields):
    # Callers pass identifiers, counts, enums only; never payloads, exceptions or URLs.
    logger.info(json.dumps({"event": name, **{k: str(v) if v is not None else None for k, v in fields.items()}}, sort_keys=True))


def configure():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
