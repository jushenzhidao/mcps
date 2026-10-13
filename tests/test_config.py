"""配置层测试（FR-F1/F2/F3）。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jieapi_mcp.config import (
    CapabilityMap,
    ConfigError,
    ConfigManager,
    Settings,
    load_capability_map,
)


def _write(tmp_path: Path, data) -> Path:
    p = tmp_path / "capabilities.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def test_load_list_form(tmp_path):
    path = _write(tmp_path, {"models": {"flux-3-image": ["image"]}})
    cap = load_capability_map(path)
    assert cap.supports("flux-3-image", "image")
    assert not cap.supports("flux-3-image", "video")
    assert cap.models_for("image") == ["flux-3-image"]


def test_load_mapping_form(tmp_path):
    path = _write(tmp_path, {"models": {"m1": {"capabilities": ["image", "image_to_image"]}}})
    cap = load_capability_map(path)
    assert cap.supports("m1", "image_to_image")


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_capability_map(tmp_path / "nope.yaml")


def test_missing_models_section(tmp_path):
    path = _write(tmp_path, {"foo": 1})
    with pytest.raises(ConfigError):
        load_capability_map(path)


def test_empty_models(tmp_path):
    path = _write(tmp_path, {"models": {}})
    with pytest.raises(ConfigError):
        load_capability_map(path)


def test_model_without_capabilities(tmp_path):
    path = _write(tmp_path, {"models": {"m": []}})
    with pytest.raises(ConfigError):
        load_capability_map(path)


def test_bad_yaml(tmp_path):
    p = tmp_path / "capabilities.yaml"
    p.write_text("models: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_capability_map(p)


def test_settings_redacts_key():
    s = Settings(api_key="sk-secret", capabilities_file=Path("/tmp/x"))
    assert "sk-secret" not in str(s.redacted())
    assert s.redacted()["api_key"] == "***"


def test_reload_keeps_old_on_failure(tmp_path, monkeypatch):
    path = _write(tmp_path, {"models": {"a": ["image"]}})
    monkeypatch.setenv("JIEAPI_KEY", "k")
    monkeypatch.setenv("CAPABILITIES_FILE", str(path))
    settings = Settings(api_key="k", capabilities_file=path)
    mgr = ConfigManager(settings, load_capability_map(path))
    # 破坏文件后 reload 应抛错，且旧配置保持
    path.write_text("models: [broken", encoding="utf-8")
    with pytest.raises(ConfigError):
        mgr.reload()
    assert mgr.capability_map.supports("a", "image")


def test_reload_success(tmp_path, monkeypatch):
    path = _write(tmp_path, {"models": {"a": ["image"]}})
    monkeypatch.setenv("JIEAPI_KEY", "k")
    monkeypatch.setenv("CAPABILITIES_FILE", str(path))
    settings = Settings(api_key="k", capabilities_file=path)
    mgr = ConfigManager(settings, load_capability_map(path))
    _write(tmp_path, {"models": {"a": ["image"], "b": ["video"]}})
    info = mgr.reload()
    assert info["models"] == 2
    assert mgr.capability_map.supports("b", "video")
