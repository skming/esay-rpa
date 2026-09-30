import base64
import hashlib
import json

from app.services.extension_config_service import ExtensionConfigService


def test_trusted_extension_id_comes_from_packaged_manifest(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    public_key = b"extension-public-key"
    manifest_path.write_text(json.dumps({"key": base64.b64encode(public_key).decode("ascii")}), encoding="utf-8")
    service = ExtensionConfigService(app_data_dir=str(tmp_path), extension_manifest_path=manifest_path)
    digest = hashlib.sha256(public_key).digest()[:16]
    expected_id = "".join(chr(ord("a") + nibble) for byte in digest for nibble in (byte >> 4, byte & 15))

    assert service.trusted_extension_id() == expected_id
    service.save({"enabled": False, "key": base64.b64encode(b"other-key").decode("ascii")})
    assert service.trusted_extension_id() == expected_id


def test_missing_or_invalid_manifest_fails_closed(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    service = ExtensionConfigService(extension_manifest_path=manifest_path)
    assert service.trusted_extension_id() is None
    manifest_path.write_text('{"key":"not base64!"}', encoding="utf-8")
    assert service.trusted_extension_id() is None
    manifest_path.write_text("{}", encoding="utf-8")
    assert service.trusted_extension_id() is None


def test_manifest_lookup_uses_fallback_only_when_primary_is_missing(tmp_path) -> None:
    first = tmp_path / "packaged" / "manifest.json"
    fallback = tmp_path / "dev" / "manifest.json"
    fallback.parent.mkdir()
    fallback.write_text('{"key":"AQID"}', encoding="utf-8")
    service = ExtensionConfigService(extension_manifest_path=first)
    service._manifest_paths.append(fallback)
    assert service.trusted_extension_id() is not None

    first.parent.mkdir()
    first.write_text("{}", encoding="utf-8")
    assert service.trusted_extension_id() is None
