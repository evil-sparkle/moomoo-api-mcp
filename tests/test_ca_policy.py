"""Public certificate fixtures exercise parent-root exclusion and fail-closed pins."""

from pathlib import Path

import pytest

from deploy.trust.harden_ca import (
    BLOCKED_ROOTS,
    assert_safe,
    disable_roots,
    fingerprints,
)

FIXTURE = Path(__file__).parent / "fixtures/ca-parent-roots.pem"


def certificates():
    return [
        part[part.index("-----BEGIN CERTIFICATE-----") :]
        + "-----END CERTIFICATE-----\n"
        for part in FIXTURE.read_text().split("-----END CERTIFICATE-----")
        if "-----BEGIN CERTIFICATE-----" in part
    ]


def test_both_cross_sign_parents_are_rejected_but_unrelated_root_survives():
    roots = certificates()
    good = []
    for root in roots:
        if fingerprints(root).intersection(BLOCKED_ROOTS.values()):
            with pytest.raises(ValueError, match="still trusted"):
                assert_safe(root)
        else:
            assert_safe(root)
            good.append(root)
    assert len(good) == 1
    with pytest.raises(ValueError, match="still trusted"):
        assert_safe(FIXTURE.read_text())


def test_system_configuration_disables_exact_pinned_roots_idempotently(tmp_path):
    roots = {next(iter(fingerprints(pem))): pem for pem in certificates()}
    directory = tmp_path / "usr/share/ca-certificates"
    for name, fingerprint in BLOCKED_ROOTS.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(roots[fingerprint])
    config = tmp_path / "etc/ca-certificates.conf"
    config.parent.mkdir()
    config.write_text(
        "# retain comment\nmozilla/ISRG_Root_X1.crt\n" + "\n".join(BLOCKED_ROOTS) + "\n"
    )
    disable_roots(tmp_path)
    first = config.read_text()
    disable_roots(tmp_path)
    assert config.read_text() == first
    assert "mozilla/ISRG_Root_X1.crt\n" in first
    assert all("!" + name in first.splitlines() for name in BLOCKED_ROOTS)


def test_unexpected_root_under_pinned_name_aborts_before_editing(tmp_path):
    name = next(iter(BLOCKED_ROOTS))
    path = tmp_path / "usr/share/ca-certificates" / name
    path.parent.mkdir(parents=True)
    good = next(
        pem
        for pem in certificates()
        if not fingerprints(pem).intersection(BLOCKED_ROOTS.values())
    )
    path.write_text(good)
    with pytest.raises(ValueError, match="fingerprint"):
        disable_roots(tmp_path)


def test_empty_bundle_cannot_pass_validation():
    with pytest.raises(ValueError, match="no certificates"):
        assert_safe("# no trust roots\n")


def test_managed_pip_and_ensurepip_seed_receive_safe_bundle(tmp_path):
    import csv
    import io
    from zipfile import ZipFile

    from deploy.trust.harden_ca import refresh_pip

    prefix = tmp_path / "opt/uv-python/cpython-fixture/lib/python3.12"
    loose = prefix / "site-packages/pip/_vendor/certifi/cacert.pem"
    loose.parent.mkdir(parents=True)
    loose.write_text(FIXTURE.read_text())
    seed = prefix / "ensurepip/_bundled/pip-fixture.whl"
    seed.parent.mkdir(parents=True)
    with ZipFile(seed, "w") as wheel:
        wheel.writestr("pip/_vendor/certifi/cacert.pem", FIXTURE.read_text())
        wheel.writestr("pip/public_code.py", "unchanged code")
        wheel.writestr(
            "pip-fixture.dist-info/RECORD",
            "pip/_vendor/certifi/cacert.pem,old,1\npip/public_code.py,old-code,14\n",
        )
    safe = next(
        pem
        for pem in certificates()
        if not fingerprints(pem).intersection(BLOCKED_ROOTS.values())
    )
    refresh_pip(tmp_path, safe)
    assert loose.read_text() == safe
    first = seed.read_bytes()
    refresh_pip(tmp_path, safe)
    assert seed.read_bytes() == first
    with ZipFile(seed) as wheel:
        assert wheel.read("pip/_vendor/certifi/cacert.pem") == safe.encode()
        assert wheel.read("pip/public_code.py") == b"unchanged code"
        rows = list(
            csv.reader(io.StringIO(wheel.read("pip-fixture.dist-info/RECORD").decode()))
        )
        assert rows[0][1].startswith("sha256=")
        assert rows[0][2] == str(len(safe.encode()))
        assert rows[1] == ["pip/public_code.py", "old-code", "14"]
