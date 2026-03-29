from app.services.ingest import _build_field_validation_report


def test_build_field_validation_report_marks_all_expected_fields():
    report = _build_field_validation_report(
        {
            "tax_id": "XAXX010101000",
            "legal_name": "EMPRESA DEMO SA DE CV",
            "trade_name": "",
            "tax_regime": "General de Ley Personas Morales",
            "postal_code": "01234",
            "curp": "",
            "cif_id": "123456789",
            "status_padron": "ACTIVO",
            "start_operations_date": "01/01/2020",
            "last_status_change_date": "02/01/2024",
            "street_type": "CALLE",
            "street_name": "REFORMA",
            "ext_number": "123",
            "int_number": "",
            "neighborhood": "CENTRO",
            "locality": "CIUDAD DE MEXICO",
            "municipality": "CUAUHTEMOC",
            "state": "CIUDAD DE MEXICO",
            "between_street": "JUAREZ",
            "and_street": "MORELOS",
            "qr_url": "https://sat.gob.mx/app/qr/faces/pages/mobile/validadorqr.jsf",
        }
    )

    assert len(report) == 21
    assert next(item for item in report if item["field"] == "tax_id")["status"] == "si"
    assert next(item for item in report if item["field"] == "trade_name")["status"] == "no"


def test_build_field_validation_report_marks_ai_corrections_as_not_ok():
    report = _build_field_validation_report(
        {
            "tax_id": "XAXX010101000",
            "legal_name": "EMPRESA DEM0 SA DE CV",
            "trade_name": "",
            "tax_regime": "General",
            "postal_code": "01234",
            "curp": "ABCD001122HMNXYZ09",
            "cif_id": "123456",
            "status_padron": "ACTIVO",
            "start_operations_date": "01/01/2020",
            "last_status_change_date": "01/01/2024",
            "street_type": "CALLE",
            "street_name": "REFORMA",
            "ext_number": "1",
            "int_number": "2",
            "neighborhood": "CENTRO",
            "locality": "CDMX",
            "municipality": "CUAUHTEMOC",
            "state": "CDMX",
            "between_street": "JUAREZ",
            "and_street": "MORELOS",
            "qr_url": "https://sat.gob.mx/app/qr/faces/pages/mobile/validadorqr.jsf",
        },
        ai_field_corrections=[
            {
                "field": "legal_name",
                "current_value": "EMPRESA DEM0 SA DE CV",
                "suggested_value": "EMPRESA DEMO SA DE CV",
                "reason": "OCR",
                "confidence": 0.97,
            }
        ],
    )

    legal_name = next(item for item in report if item["field"] == "legal_name")
    assert legal_name["status"] == "no"
    assert legal_name["suggested_value"] == "EMPRESA DEMO SA DE CV"
    assert legal_name["reason"] == "OCR"