#!/usr/bin/env python3
"""Rimuove da un video una filigrana che si sposta tra gli angoli.

Esempi:

    # rilevamento e ricostruzione classica, senza GPU
    python dewatermark.py -i video.mp4 -o out/pulito.mp4

    # solo analisi: maschere, matte e video di controllo
    python dewatermark.py -i video.mp4 --detect-only --debug-dir out/debug

    # qualita' massima con GPU (vedi README per l'installazione)
    python dewatermark.py -i video.mp4 -o out/pulito.mp4 --backend propainter

Progetto a scopo didattico: mostra come si stima la matte di una filigrana
semitrasparente e come si ricostruisce l'area coperta. Usalo sui tuoi video e
tieni presente che togliere una filigrana cancella anche l'indicazione di
provenienza di un contenuto generato da AI.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from wmremove import detect as detect_module
from wmremove import video_io
from wmremove.backends import BACKENDS, Options, get_backend, resolve_propainter_dir
from wmremove.detect import CORNERS


def parse_box(value: str) -> tuple[int, int, int, int]:
    parts = [int(p) for p in value.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("il rettangolo va scritto come x,y,larghezza,altezza")
    return tuple(parts)  # type: ignore[return-value]


def parse_schedule(value: str) -> list[tuple[int, int, str]]:
    """Interpreta ``0-47:tl,48-100:tr`` come elenco di intervalli."""
    schedule: list[tuple[int, int, str]] = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        span, _, corner = chunk.partition(":")
        corner = corner.strip().lower()
        if corner not in CORNERS:
            raise argparse.ArgumentTypeError(
                f"angolo '{corner}' non valido (usa {', '.join(CORNERS)})"
            )
        first, _, last = span.partition("-")
        schedule.append((int(first), int(last or first), corner))
    if not schedule:
        raise argparse.ArgumentTypeError("nessun intervallo riconosciuto")
    return schedule


def parse_frac(value: str) -> tuple[float, float]:
    parts = [float(p) for p in value.replace(" ", "").split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("la frazione va scritta come larghezza,altezza")
    return parts[0], parts[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )

    io_group = parser.add_argument_group("ingresso e uscita")
    io_group.add_argument("-i", "--input", type=Path, required=True, help="video mp4 di partenza")
    io_group.add_argument(
        "-o", "--output", type=Path, default=Path("out/pulito.mp4"), help="video ripulito"
    )
    io_group.add_argument("--crf", type=int, default=16, help="qualita' H.264, piu' basso = migliore")
    io_group.add_argument("--max-frames", type=int, default=None, help="elabora solo i primi N frame")
    io_group.add_argument("--compare", action="store_true", help="salva anche un video prima/dopo affiancati")
    io_group.add_argument("--quiet", action="store_true", help="meno messaggi")

    detect_group = parser.add_argument_group("rilevamento")
    detect_group.add_argument("--detect-only", action="store_true", help="analizza soltanto, non ricostruisce")
    detect_group.add_argument("--debug-dir", type=Path, default=None, help="cartella per matte, grafici e video di controllo")
    detect_group.add_argument("--roi-frac", type=parse_frac, default=(0.5, 0.28), help="dimensione delle zone d'angolo esplorate")
    detect_group.add_argument("--alpha-threshold", type=float, default=0.06, help="opacita' minima considerata filigrana")
    detect_group.add_argument("--mad-k", type=float, default=6.0, help="severita' della soglia sulle transizioni")
    detect_group.add_argument("--min-purity", type=float, default=0.88, help="quanto un salto deve essere a senso unico per valere come transizione")
    detect_group.add_argument("--temporal-dilation", type=int, default=1, help="frame di margine attorno agli intervalli")
    detect_group.add_argument("--manual-box", type=parse_box, default=None, help="rettangolo fisso x,y,w,h invece del rilevamento")
    detect_group.add_argument("--force-corners", type=parse_schedule, default=None, help="pianificazione imposta, es. 0-47:tl,48-100:tr")

    mask_group = parser.add_argument_group("maschera")
    mask_group.add_argument("--mask-threshold", type=float, default=0.05)
    mask_group.add_argument("--mask-dilation", type=int, default=6)
    mask_group.add_argument("--feather", type=int, default=8, help="ampiezza della sfumatura ai bordi")

    backend_group = parser.add_argument_group("ricostruzione")
    backend_group.add_argument("--backend", choices=BACKENDS, default="classic")
    backend_group.add_argument("--device", default="auto", choices=("auto", "cuda", "mps", "cpu"))
    backend_group.add_argument("--dry-run", action="store_true", help="mostra il comando del backend esterno senza eseguirlo")
    backend_group.add_argument("--work-dir", type=Path, default=Path("out/lavoro"))
    backend_group.add_argument("--keep-work", action="store_true", help="non cancella i file temporanei")

    classic_group = parser.add_argument_group("backend classic")
    classic_group.add_argument("--classic-mode", choices=("unblend", "inpaint"), default="unblend")
    classic_group.add_argument("--unblend-max", type=float, default=0.75, help="oltre questa opacita' si passa all'inpainting")
    classic_group.add_argument("--unblend-denoise", type=float, default=0.25, help="lisciatura del rumore amplificato, 0 disattiva")
    classic_group.add_argument("--inpaint-radius", type=int, default=5)
    classic_group.add_argument("--inpaint-method", choices=("telea", "ns"), default="telea")

    pp_group = parser.add_argument_group("backend propainter")
    pp_group.add_argument(
        "--propainter-dir",
        type=Path,
        default=None,
        help="cartella di ProPainter (default: gpu/ProPainter oppure third_party/ProPainter)",
    )
    pp_group.add_argument("--propainter-python", default=None, help="interprete con PyTorch, se diverso da quello corrente")
    pp_group.add_argument("--propainter-mask-dilation", type=int, default=4)
    pp_group.add_argument("--subvideo-length", type=int, default=32, help="frame per blocco: piu' basso = meno VRAM")
    pp_group.add_argument("--neighbor-length", type=int, default=10)
    pp_group.add_argument("--ref-stride", type=int, default=10)
    pp_group.add_argument("--raft-iter", type=int, default=20)
    pp_group.add_argument("--resize-ratio", type=float, default=1.0)
    pp_group.add_argument("--no-fp16", dest="fp16", action="store_false", help="usa fp32 (piu' VRAM)")
    pp_group.set_defaults(fp16=True)

    lama_group = parser.add_argument_group("backend lama")
    lama_group.add_argument("--lama-model", default="lama")

    return parser


def apply_schedule(detection, schedule: list[tuple[int, int, str]], n_frames: int) -> None:
    """Sostituisce gli intervalli rilevati con quelli imposti dall'utente."""
    for corner in CORNERS:
        detection.presence[corner] = np.zeros(n_frames, bool)
    for first, last, corner in schedule:
        last = min(last, n_frames - 1)
        detection.presence[corner][first : last + 1] = True


def save_comparison_stills(frames, cleaned_by_index, detection, debug_dir: Path) -> list[Path]:
    """Salva un affiancamento prima/dopo al centro di ogni intervallo."""
    debug_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for corner, first, last in detection.intervals():
        middle = (first + last) // 2
        if middle not in cleaned_by_index:
            continue
        before, after = frames[middle], cleaned_by_index[middle]
        separator = np.full((before.shape[0], 6, 3), 255, np.uint8)
        pair = np.hstack([before, separator, after])
        cv2.putText(pair, "prima", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.putText(
            pair, "dopo", (before.shape[1] + 18, 34),
            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 0), 2, cv2.LINE_AA,
        )
        path = debug_dir / f"confronto_{corner}_{middle:04d}.png"
        cv2.imwrite(str(path), pair)
        saved.append(path)
    return saved


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = (lambda *_: None) if args.quiet else print

    info = video_io.probe(args.input)
    log(f"video: {info}")

    started = time.time()
    frames = video_io.read_frames(args.input, args.max_frames)
    n_frames, height, width = frames.shape[:3]
    log(f"decodificati {n_frames} frame in {time.time() - started:.1f}s")

    if args.manual_box:
        log(f"rilevamento saltato: uso il rettangolo fisso {args.manual_box}")
        detection = detect_module.manual_detection((height, width), args.manual_box, n_frames)
    else:
        started = time.time()
        detection = detect_module.detect(
            frames,
            roi_frac=args.roi_frac,
            alpha_threshold=args.alpha_threshold,
            mad_k=args.mad_k,
            min_purity=args.min_purity,
            temporal_dilation=args.temporal_dilation,
            log=log,
        )
        log(f"rilevamento completato in {time.time() - started:.1f}s")

    if args.force_corners:
        log("pianificazione degli angoli imposta da riga di comando")
        apply_schedule(detection, args.force_corners, n_frames)

    if not any(detection.presence[c].any() for c in CORNERS):
        print(
            "nessuna filigrana rilevata.\n"
            "Il metodo ha bisogno di vedere almeno una comparsa o una scomparsa: se hai\n"
            "usato --max-frames prova ad allargare il tratto analizzato. Altrimenti abbassa\n"
            "--mad-k (per esempio 3.5), oppure passa --manual-box x,y,larghezza,altezza\n"
            "se la filigrana resta sempre ferma nello stesso punto.",
            file=sys.stderr,
        )
        return 1

    if not any(detection.alpha[c].any() for c in CORNERS):
        print(
            "gli intervalli sono noti ma la forma della filigrana non e' stata stimata.\n"
            "Serve almeno una transizione nel tratto analizzato, oppure usa --manual-box.",
            file=sys.stderr,
        )
        return 1

    log("\n" + detection.summary(info.fps) + "\n")

    debug_dir = args.debug_dir
    if args.detect_only and debug_dir is None:
        debug_dir = args.output.parent / "debug"
    if debug_dir is not None:
        log(f"scrivo i file di controllo in {debug_dir}")
        detect_module.save_debug(
            detection, frames, debug_dir, info.fps, args.mask_threshold, args.mask_dilation
        )

    if args.detect_only:
        log("modalita' --detect-only: nessun video ricostruito")
        return 0

    propainter_dir = (
        Path(args.propainter_dir)
        if args.propainter_dir is not None
        else resolve_propainter_dir()
    )

    options = Options(
        mask_threshold=args.mask_threshold,
        mask_dilation=args.mask_dilation,
        feather=args.feather,
        classic_mode=args.classic_mode,
        unblend_max=args.unblend_max,
        unblend_denoise=args.unblend_denoise,
        inpaint_radius=args.inpaint_radius,
        inpaint_method=args.inpaint_method,
        work_dir=args.work_dir,
        keep_work=args.keep_work,
        device=args.device,
        dry_run=args.dry_run,
        propainter_dir=propainter_dir,
        propainter_python=args.propainter_python,
        propainter_mask_dilation=args.propainter_mask_dilation,
        fp16=args.fp16,
        subvideo_length=args.subvideo_length,
        neighbor_length=args.neighbor_length,
        ref_stride=args.ref_stride,
        raft_iter=args.raft_iter,
        resize_ratio=args.resize_ratio,
        lama_model=args.lama_model,
    )

    backend = get_backend(args.backend)
    started = time.time()

    audio_source = args.input if info.has_audio else None
    writer: video_io.VideoWriter | None = None
    comparison: video_io.VideoWriter | None = None
    comparison_path = args.output.with_name(args.output.stem + "_confronto.mp4")

    stills: dict[int, np.ndarray] = {}
    wanted = {(first + last) // 2 for _, first, last in detection.intervals()}

    # In un terminale l'avanzamento si riscrive sulla stessa riga; se invece
    # l'uscita e' rediretta (per esempio verso l'interfaccia grafica) ogni
    # aggiornamento va su una riga sua, altrimenti non si vedrebbe nulla.
    interactive = sys.stdout.isatty()
    step = 25 if interactive else 10
    written = 0

    try:
        for t, cleaned in enumerate(backend(frames, detection, options, log)):
            if writer is None:
                writer = video_io.VideoWriter(
                    args.output, info.fps, (width, height),
                    crf=args.crf, audio_from=audio_source,
                )
                if args.compare:
                    comparison = video_io.VideoWriter(
                        comparison_path, info.fps, (width * 2, height), crf=args.crf
                    )
            writer.write(cleaned)
            if comparison is not None:
                comparison.write(np.hstack([frames[t], cleaned]))
            if t in wanted:
                stills[t] = cleaned.copy()
            written += 1
            if not args.quiet and (written % step == 0 or written == n_frames):
                print(
                    f"  {written}/{n_frames} frame",
                    end="\r" if interactive else "\n",
                    flush=True,
                )
    finally:
        if writer is not None:
            writer.close()
        if comparison is not None:
            comparison.close()

    if written == 0:
        print("nessun frame ricostruito: file di uscita non creato.", file=sys.stderr)
        return 1

    log(f"\nscritto {args.output} in {time.time() - started:.1f}s")
    if comparison is not None:
        log(f"scritto {comparison.path}")
    if debug_dir is not None:
        for path in save_comparison_stills(frames, stills, detection, Path(debug_dir)):
            log(f"scritto {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
