from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def read_sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()

