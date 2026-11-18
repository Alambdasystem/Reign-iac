"""Prepare the pinned, offline-capable Android runtime and model."""
import hashlib
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "generated-assets"
MODEL_URL = ("https://huggingface.co/bartowski/SmolLM2-360M-Instruct-GGUF/resolve/"
             "7be6f65f1db715fe5dc5a4634c0d459b4eed42ec/SmolLM2-360M-Instruct-Q4_K_M.gguf")
MODEL_SHA = "2fa3f013dcdd7b99f9b237717fa0b12d75bbb89984cc1274be1471a465bac9c2"
RUNTIME_URL = "https://registry.npmjs.org/@wllama/wllama/-/wllama-2.3.7.tgz"
RUNTIME_SHA = "297109c76215c7457c230527fe8f930aa578897156c06df2da6cf7e393ee0233"


def verified_download(url, sha, destination):
    if destination.is_file():
        with destination.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == sha:
                return
    with tempfile.NamedTemporaryFile(dir=OUT, delete=False) as temp:
        path = Path(temp.name)
    try:
        with urllib.request.urlopen(url, timeout=120) as source, path.open("wb") as output:
            shutil.copyfileobj(source, output)
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != sha:
                raise ValueError(f"Checksum mismatch for {destination.name}")
        path.replace(destination)
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    verified_download(MODEL_URL, MODEL_SHA, OUT / "model.gguf")
    with tempfile.TemporaryDirectory(dir=OUT) as tmp:
        archive = Path(tmp) / "runtime.tgz"
        verified_download(RUNTIME_URL, RUNTIME_SHA, archive)
        with tarfile.open(archive) as src:
            for name, target in [("esm/index.min.js", "wllama.js"),
                                 ("esm/single-thread/wllama.wasm", "wllama.wasm"),
                                 ("LICENCE", "runtime-license.txt")]:
                with src.extractfile("package/" + name) as source, (OUT / target).open("wb") as output:
                    shutil.copyfileobj(source, output)
