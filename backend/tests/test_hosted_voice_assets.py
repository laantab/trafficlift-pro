"""Asset installer never accepts corrupt files or redownloads validated files."""
import hashlib
import io
import pytest
from scripts import install_hosted_voice_assets as assets


def test_installer_checksums_and_reuses_cache(monkeypatch, tmp_path):
    data=b"verified model bytes"
    monkeypatch.setattr(assets,"ASSETS",{"model.onnx": hashlib.sha256(data).hexdigest()})
    calls=[]
    def fake_open(url, timeout=120):
        calls.append(url)
        return io.BytesIO(data)
    monkeypatch.setattr(assets,"urlopen",fake_open)
    assets.install(tmp_path)
    assets.install(tmp_path)
    assert (tmp_path/"model.onnx").read_bytes()==data
    assert len(calls)==1


def test_installer_rejects_mismatched_asset(monkeypatch, tmp_path):
    monkeypatch.setattr(assets,"ASSETS",{"model.onnx":"0"*64})
    monkeypatch.setattr(assets,"urlopen",lambda *a,**kw: io.BytesIO(b"invalid"))
    with pytest.raises(RuntimeError,match="SHA256"):
        assets.install(tmp_path)
    assert not (tmp_path/"model.onnx").exists()
    assert not (tmp_path/"model.onnx.partial").exists()
