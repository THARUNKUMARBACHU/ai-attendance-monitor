"""Build step for the Vercel deployment (pyproject.toml: [tool.vercel.scripts] build). Vercel runs it after
installing the Python dependencies and before packaging the function. It:

1. builds the UI into frontend/dist, which the app serves at /ui;
2. downloads the three embedding models into var/models, adds the placeholder files fastembed needs to load
   them without the network, and checks that they do load offline;
3. downloads a Tesseract build for Amazon Linux 2023 into vendor/tesseract, at a pinned version checked
   against its SHA-256.

Each step can be skipped, for example to try the others locally:
    uv run python scripts/vercel_build.py --skip-ui --skip-tesseract
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from attendance_ai.core.config import Settings  # noqa: E402

MODEL_DIR = ROOT / "var" / "models"
TESSERACT_DIR = ROOT / "vendor" / "tesseract"
# Tesseract 5.5.2 with Leptonica 1.87, built for AWS Lambda on Amazon Linux 2023 (the runtime Vercel's
# Python functions use) by github.com/bweigel/aws-lambda-tesseract-layer, Apache-2.0.
TESSERACT_URL = (
    "https://github.com/bweigel/aws-lambda-tesseract-layer/releases/download/v5.4.0/tesseract-al2023-x86.zip"
)
TESSERACT_SHA256 = "f6312eed51baea7514a32a79036908b782dde7a558d4f5817a0a206221797868"
UNUSED_LANGUAGE_DATA = ("deu.traineddata",)

OFFLINE_CHECK = """
from pathlib import Path
from attendance_ai.stores.embeddings import Embedder
embedder = Embedder({dense!r}, {sparse!r}, {rerank!r}, Path({cache!r}))
embedder.embed_query("Why was Vikram Reddy on leave?")
if embedder.can_rerank:
    embedder.rerank("why leave", ["Sick leave", "Present"])
print("models load offline")
"""


def build_ui() -> None:
    frontend = ROOT / "frontend"
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm is needed to build the UI.")
    # --include=dev: the build tools are dev dependencies, which npm skips when NODE_ENV=production.
    # Fixed commands and arguments, no shell.
    subprocess.run([npm, "ci", "--include=dev", "--no-audit", "--no-fund"], cwd=frontend, check=True)  # noqa: S603
    subprocess.run([npm, "run", "build"], cwd=frontend, check=True)  # noqa: S603
    shutil.rmtree(frontend / "node_modules")  # build-only; keeps the function bundle small


def fetch_models() -> None:
    os.environ.pop("HF_HUB_OFFLINE", None)
    from fastembed import SparseTextEmbedding, TextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    from fastembed.sparse.bm25 import supported_languages

    fields = Settings.model_fields
    dense, sparse = fields["embedding_model"].default, fields["sparse_model"].default
    rerank = fields["rerank_model"].default
    cache = str(MODEL_DIR)
    TextEmbedding(model_name=dense, cache_dir=cache)
    SparseTextEmbedding(model_name=sparse, cache_dir=cache)
    if rerank:
        TextCrossEncoder(model_name=rerank, cache_dir=cache)

    # fastembed loads a cached model only when the model's file and every additional file exist. BM25
    # has no model file (it names a placeholder, "mock.file"), and fastembed 0.8.1 also expects a
    # stopword list (tamil.txt) that the model repository does not contain. Empty placeholders let the
    # cache be used offline; only the English stopwords are used.
    for snapshot in MODEL_DIR.glob(f"models--{sparse.replace('/', '--')}/snapshots/*"):
        for name in ["mock.file", *(f"{language}.txt" for language in supported_languages)]:
            (snapshot / name).touch(exist_ok=True)
    inline_cache_links(MODEL_DIR)

    env = {**os.environ, "HF_HUB_OFFLINE": "1", "PYTHONPATH": str(ROOT / "src")}
    script = OFFLINE_CHECK.format(dense=dense, sparse=sparse, rerank=rerank, cache=cache)
    subprocess.run([sys.executable, "-c", script], env=env, check=True)  # noqa: S603 (our own script)


def inline_cache_links(cache: Path) -> None:
    """The Hugging Face cache keeps each file once under blobs/ and links to it from snapshots/. The
    function bundle follows the links, so every model would ship two or three times. Replace each link
    with the file itself and drop blobs/: a snapshot that holds real files loads the same way."""
    for link in [path for path in cache.rglob("*") if path.is_symlink()]:
        target = link.resolve()
        link.unlink()
        shutil.copy2(target, link)
    for folder in [*cache.glob("models--*/blobs"), *cache.glob(".locks")]:
        shutil.rmtree(folder)


def fetch_tesseract() -> None:
    response = httpx.get(TESSERACT_URL, follow_redirects=True, timeout=120)
    response.raise_for_status()
    digest = hashlib.sha256(response.content).hexdigest()
    if digest != TESSERACT_SHA256:
        raise SystemExit(f"The Tesseract download does not match its pinned checksum ({digest}).")
    shutil.rmtree(TESSERACT_DIR, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(TESSERACT_DIR)
    for name in UNUSED_LANGUAGE_DATA:
        (TESSERACT_DIR / "tesseract" / "share" / "tessdata" / name).unlink(missing_ok=True)
    (TESSERACT_DIR / "bin" / "tesseract").chmod(0o755)


def size_mb(path: Path) -> float:
    """Real files only: a link would otherwise count its target a second time."""
    files = (item for item in path.rglob("*") if item.is_file() and not item.is_symlink())
    return sum(item.stat().st_size for item in files) / 1_048_576


def main() -> int:
    parser = argparse.ArgumentParser(description="Vercel build step.")
    parser.add_argument("--skip-ui", action="store_true")
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument("--skip-tesseract", action="store_true")
    args = parser.parse_args()
    if not args.skip_ui:
        build_ui()
    if not args.skip_models:
        fetch_models()
    if not args.skip_tesseract:
        fetch_tesseract()
    outputs = (
        ("UI", ROOT / "frontend" / "dist"),
        ("models", MODEL_DIR),
        ("tesseract", TESSERACT_DIR),
        ("python packages", Path(sys.prefix)),
    )
    for label, path in outputs:
        if path.exists():
            print(f"{label}: {size_mb(path):.1f} MB in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
