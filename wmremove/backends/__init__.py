"""Backend di ricostruzione dell'area coperta dalla filigrana.

Ogni backend espone ``run(frames, detection, options, log)`` e restituisce un
iteratore di frame gia' ricomposti sull'originale.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Protocol

import numpy as np

BACKENDS = ("classic", "propainter", "lama")


@dataclass
class Options:
    """Parametri condivisi da tutti i backend."""

    # Maschera
    mask_threshold: float = 0.05
    mask_dilation: int = 6
    feather: int = 8

    # Backend classic
    classic_mode: str = "unblend"
    unblend_max: float = 0.75
    unblend_denoise: float = 0.25
    inpaint_radius: int = 5
    inpaint_method: str = "telea"

    # Backend esterni
    work_dir: Path = Path("out/lavoro")
    keep_work: bool = False
    device: str = "auto"
    dry_run: bool = False

    # ProPainter
    propainter_dir: Path = Path("gpu/ProPainter")
    propainter_python: str | None = None
    propainter_mask_dilation: int = 4
    fp16: bool = True
    subvideo_length: int = 32
    neighbor_length: int = 10
    ref_stride: int = 10
    raft_iter: int = 20
    resize_ratio: float = 1.0

    # LaMa (modello TorchScript Big-LaMa)
    lama_model: str = "lama"


def resolve_device(requested: str) -> str:
    """Traduce ``auto`` nel dispositivo migliore disponibile.

    Con le build ROCm di PyTorch le GPU AMD si presentano come ``cuda``: e'
    voluto, non e' un errore.
    """
    if requested != "auto":
        return requested
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def resolve_propainter_dir(requested: Path | None = None) -> Path:
    """Trova la cartella di ProPainter tra le posizioni usate da GUI e CLI."""
    root = Path(__file__).resolve().parents[2]
    candidates: list[Path] = []
    if requested is not None:
        candidates.append(Path(requested))
    candidates.extend(
        (
            root / "gpu" / "ProPainter",
            root / "third_party" / "ProPainter",
            Path("gpu/ProPainter"),
            Path("third_party/ProPainter"),
        )
    )
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if (candidate / "inference_propainter.py").exists():
            return candidate
    return Path(requested) if requested is not None else root / "gpu" / "ProPainter"


def propainter_ready(requested: Path | None = None) -> bool:
    return (resolve_propainter_dir(requested) / "inference_propainter.py").exists()


def torch_ready() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


class Backend(Protocol):  # pragma: no cover - solo tipizzazione
    def __call__(
        self, frames: np.ndarray, detection, options: Options, log: Callable[[str], None]
    ) -> Iterator[np.ndarray]: ...


def get_backend(name: str) -> Backend:
    if name == "classic":
        from .classic import run
    elif name == "propainter":
        from .propainter import run
    elif name == "lama":
        from .lama import run
    else:
        raise ValueError(f"backend sconosciuto: {name} (scegli tra {', '.join(BACKENDS)})")
    return run
