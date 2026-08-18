"""Tests for app.planner.fqdn_intake — vendor FQDN allowlist row/xlsx parsing."""

import io

import openpyxl

from app.planner.fqdn_intake import parse_fqdn_rows, parse_fqdn_xlsx


def test_parse_fqdn_rows_basic():
    rows = [
        {
            "Hostname/Domain": "api.vendor.com", "Ports": "443, 5223",
            "Protocol": "TCP", "Vendor": "Vendor Co", "Category": "API",
            "Required?": "Yes", "Purpose/Notes": "Core API",
        },
        {
            "Hostname/Domain": "*.push.apple.com", "Ports": "5223",
            "Protocol": "TCP",
        },
    ]
    req = parse_fqdn_rows(rows, src_ip="10.1.1.1", ticket_id="CHG1", firewalls=["FW-A:OT-ADOM"])

    assert req.vendor == "Vendor Co"
    assert req.category == "API"
    assert req.src_ip == "10.1.1.1"
    assert req.firewalls == ["FW-A:OT-ADOM"]
    assert len(req.entries) == 2
    assert req.entries[0].fqdn == "api.vendor.com"
    assert req.entries[0].ports == [443, 5223]
    assert req.entries[0].required is True
    assert req.entries[1].is_wildcard is True
    assert not req.warnings


def test_parse_fqdn_rows_flags_illegal_characters_and_bad_ports():
    rows = [
        {"Hostname/Domain": 'evil"fqdn', "Ports": "443"},
        {"Hostname/Domain": "ok.vendor.com", "Ports": "not-a-port"},
    ]
    req = parse_fqdn_rows(rows)
    assert not req.entries
    assert any("illegal characters" in w for w in req.warnings)
    assert any("no valid ports" in w for w in req.warnings)


def test_parse_fqdn_rows_missing_src_ip_flagged():
    req = parse_fqdn_rows([{"Hostname/Domain": "x.vendor.com", "Ports": "443"}])
    assert "src_ip" in req.missing_fields


def test_parse_fqdn_xlsx_roundtrip():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Hostname/Domain", "Ports", "Protocol", "Vendor", "Category"])
    ws.append(["api.vendor.com", "443", "TCP", "Vendor Co", "API"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    req = parse_fqdn_xlsx(buf, src_ip="10.1.1.1", ticket_id="CHG1", firewalls=["FW-A:OT-ADOM"])
    assert req.vendor == "Vendor Co"
    assert req.entries[0].fqdn == "api.vendor.com"
