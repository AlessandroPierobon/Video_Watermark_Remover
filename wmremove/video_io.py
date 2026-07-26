"""Lettura e scrittura di video.

L'unica dipendenza esterna e' il binario ffmpeg incluso nel pacchetto
``imageio-ffmpeg``: non serve installare ffmpeg nel sistema.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import cv2
import imageio_ffmpeg
import numpy as np


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    n_frames: int
    has_audio: bool

    def __str__(self) -> str:  # pragma: no cover - solo diagnostica
        audio = "con audio" if self.has_audio else "senza audio"
        return (
            f"{self.width}x{self.height}, {self.fps:.3f} fps, "
            f"{self.n_frames} frame, {audio}"
        )


def ffmpeg_exe() -> str:
    """Percorso del binario ffmpeg incluso in imageio-ffmpeg."""
    return imageio_ffmpeg.get_ffmpeg_exe()


def _ffmpeg_stream_report(path: Path) -> str:
    """Output diagnostico di ffmpeg per un file (arriva su stderr)."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    return proc.stderr


def probe(path: Path) -> VideoInfo:
    """Legge risoluzione, frame rate, numero di frame e presenza di audio."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"video non trovato: {path}")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"impossibile aprire il video: {path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    report = _ffmpeg_stream_report(path)
    has_audio = bool(re.search(r"Stream #.*: Audio:", report))

    if fps <= 0:
        match = re.search(r"([\d.]+) fps", report)
        fps = float(match.group(1)) if match else 25.0

    return VideoInfo(width, height, fps, n_frames, has_audio)


def read_frames(path: Path, max_frames: int | None = None) -> np.ndarray:
    """Decodifica il video in un array ``(N, H, W, 3)`` uint8 in ordine BGR."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"impossibile aprire il video: {path}")

    frames: list[np.ndarray] = []
    while max_frames is None or len(frames) < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()

    if not frames:
        raise RuntimeError(f"nessun frame decodificato da {path}")
    return np.stack(frames)


class VideoWriter:
    """Scrittura H.264 a qualita' costante, un frame alla volta.

    I frame vengono passati a ffmpeg via pipe man mano che arrivano, quindi il
    video di uscita non viene mai tenuto tutto in memoria. I frame attesi sono
    in BGR, ``size`` e' ``(larghezza, altezza)``.
    """

    def __init__(
        self,
        path: Path,
        fps: float,
        size: tuple[int, int],
        crf: int = 16,
        preset: str = "slow",
        audio_from: Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._writer = imageio_ffmpeg.write_frames(
            str(self.path),
            size=size,
            fps=fps,
            codec="libx264",
            quality=None,
            macro_block_size=1,
            pix_fmt_in="bgr24",
            pix_fmt_out="yuv420p",
            output_params=["-crf", str(crf), "-preset", preset],
            audio_path=str(audio_from) if audio_from else None,
            audio_codec="aac" if audio_from else None,
        )
        self._writer.send(None)

    def write(self, frame: np.ndarray) -> None:
        self._writer.send(np.ascontiguousarray(frame))

    def close(self) -> None:
        self._writer.close()

    def __enter__(self) -> "VideoWriter":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def write_video(
    frames: Iterable[np.ndarray],
    path: Path,
    fps: float,
    size: tuple[int, int],
    crf: int = 16,
    preset: str = "slow",
    audio_from: Path | None = None,
) -> Path:
    """Scrive un video intero a partire da un iterabile di frame."""
    with VideoWriter(path, fps, size, crf, preset, audio_from) as writer:
        for frame in frames:
            writer.write(frame)
    return Path(path)


def dump_frames(frames: np.ndarray, directory: Path, prefix: str = "") -> Path:
    """Salva i frame come PNG numerati, formato atteso dai backend esterni."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(frames):
        cv2.imwrite(str(directory / f"{prefix}{i:05d}.png"), frame)
    return directory


def load_frames_dir(directory: Path) -> np.ndarray:
    """Rilegge una cartella di PNG numerati come array di frame."""
    files = sorted(Path(directory).glob("*.png"))
    if not files:
        raise RuntimeError(f"nessun PNG in {directory}")
    return np.stack([cv2.imread(str(f), cv2.IMREAD_COLOR) for f in files])


def iter_frames_dir(directory: Path) -> Iterator[np.ndarray]:
    """Come :func:`load_frames_dir` ma senza caricare tutto in memoria."""
    for file in sorted(Path(directory).glob("*.png")):
        yield cv2.imread(str(file), cv2.IMREAD_COLOR)
