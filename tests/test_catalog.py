import pytest

from alert_triage.catalog import CatalogError, load_catalog

VALID = """
checkout-api:
  owner: payments-team
  escalation: payments-oncall-lead
  customer_facing: true
  environments: [prod, staging]
  runbook: https://runbooks.example.com/checkout-api
"""


def write(tmp_path, text):
    path = tmp_path / "services.yaml"
    path.write_text(text)
    return path


def test_valid_catalog_loads(tmp_path):
    services = load_catalog(write(tmp_path, VALID))
    assert services["checkout-api"].environments == ("prod", "staging")


def test_missing_file_is_a_catalog_error(tmp_path):
    with pytest.raises(CatalogError, match="cannot read"):
        load_catalog(tmp_path / "nope.yaml")


def test_missing_field_is_named(tmp_path):
    with pytest.raises(CatalogError, match="owner"):
        load_catalog(write(tmp_path, VALID.replace("  owner: payments-team\n", "")))


def test_customer_facing_must_be_boolean(tmp_path):
    with pytest.raises(CatalogError, match="customer_facing"):
        load_catalog(write(tmp_path, VALID.replace("true", '"yes"')))


@pytest.mark.parametrize("text", ["", "just a string", "checkout-api: [1, 2]"])
def test_wrong_shape_is_rejected(tmp_path, text):
    with pytest.raises(CatalogError):
        load_catalog(write(tmp_path, text))
