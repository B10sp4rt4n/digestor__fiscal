import pytest

from app.api.routers.receptor_validate import _validate_uso_cfdi, get_catalogos


@pytest.mark.parametrize("code,person,regime,valid", [
    ("G03", "moral", "601", True), ("I04", "moral", "601", True),
    ("D01", "moral", "601", False), ("D01", "fisica", "607", True),
    ("D10", "fisica", "615", True), ("D01", "fisica", "626", False),
    ("G03", "fisica", "605", False), ("G03", "fisica", "616", False),
    ("G02", "fisica", "616", True), ("G03", "fisica", "610", False),
    ("CP01", "moral", "601", True), ("CN01", "fisica", "605", True),
    ("CN01", "moral", "601", False), ("P01", "moral", "601", False),
])
def test_use_person_and_regime_boundaries(code, person, regime, valid):
    assert _validate_uso_cfdi(code, person, regime)[0] is valid


def test_public_catalog_has_complete_explicit_regime_lists():
    usages = get_catalogos()["usos_cfdi"]
    assert len(usages) == 24
    assert "P01" not in usages
    assert all(row["regimenes_compatibles"] for row in usages.values())
    assert usages["I02"]["descripcion"] == "Mobiliario y equipo de oficina por inversiones"
    assert usages["G01"]["descripcion"] == "Adquisición de mercancías"
