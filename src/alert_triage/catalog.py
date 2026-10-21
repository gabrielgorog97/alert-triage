from pathlib import Path

import yaml

from alert_triage.models import Service

REQUIRED_FIELDS = ("owner", "escalation", "customer_facing", "environments", "runbook")


class CatalogError(Exception):
    pass


def load_catalog(path: Path) -> dict[str, Service]:
    """Load the service catalog. A broken catalog is a configuration error, so it fails loudly
    instead of letting every alert fall into manual review."""
    try:
        data = yaml.safe_load(Path(path).read_text())
    except OSError as exc:
        raise CatalogError(f"cannot read catalog {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CatalogError(f"catalog {path} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict) or not data:
        raise CatalogError(f"catalog {path} must map service names to their metadata")

    services = {}
    for name, meta in data.items():
        if not isinstance(meta, dict):
            raise CatalogError(f"service '{name}': metadata must be a mapping")
        missing = [f for f in REQUIRED_FIELDS if f not in meta]
        if missing:
            raise CatalogError(f"service '{name}': missing {missing}")
        if not isinstance(meta["customer_facing"], bool):
            raise CatalogError(f"service '{name}': 'customer_facing' must be true or false")
        envs = meta["environments"]
        if not isinstance(envs, list) or not envs or not all(isinstance(e, str) for e in envs):
            raise CatalogError(f"service '{name}': 'environments' must be a non-empty list")
        services[name] = Service(
            name=name,
            owner=str(meta["owner"]),
            escalation=str(meta["escalation"]),
            customer_facing=meta["customer_facing"],
            environments=tuple(envs),
            runbook=str(meta["runbook"]),
        )
    return services
