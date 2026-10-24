"""Install checksum-verified Kokoro voice assets on the existing persistent disk.

Run during a controlled deploy, not in the customer request handler.
Never enable TRAFFICLIFT_HOSTED_VIDEO before FFmpeg/FFprobe, dependencies,
access protection, and end-to-end render verification are complete.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
from urllib.request import urlopen

ASSETS = {
    "kokoro-v1.0.int8.onnx": "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def install(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in ASSETS.items():
        target = directory / name
        if target.is_file() and digest(target) == expected:
            continue
        temporary = directory / (name + ".partial")
        try:
            with urlopen(BASE + name, timeout=120) as response, temporary.open("wb") as output:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    output.write(chunk)
            if digest(temporary) != expected:
                raise RuntimeError(name + ": SHA256 verification failed")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    install(Path(os.getenv("TRAFFICLIFT_HOSTED_MODEL_DIR", "/var/data/models")))
