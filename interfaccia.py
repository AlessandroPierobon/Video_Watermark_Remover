#!/usr/bin/env python3
"""Finestra grafica per la rimozione della filigrana.

Non fa il lavoro in proprio: prepara gli argomenti e avvia ``dewatermark.py``
come processo separato, mostrandone l'avanzamento. Cosi' l'interfaccia resta
reattiva, il calcolo si puo' interrompere e la logica resta tutta nel programma
da riga di comando.

Si avvia da sola con:  pythonw interfaccia.py [video.mp4]
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Video Watermark Remover"
ROOT_DIR = Path(__file__).resolve().parent
PROGRESS_RE = re.compile(r"(\d+)/(\d+) frame")

MOTORI = [
    ("classic", "Classico - nessuna GPU, ideale per filigrane semitrasparenti"),
    ("propainter", "ProPainter - GPU, ricostruisce dai fotogrammi vicini"),
    ("lama", "LaMa - rete neurale, un fotogramma alla volta"),
]


def interprete() -> str:
    """Interprete da usare per il processo di lavoro.

    Se l'interfaccia gira sotto ``pythonw.exe`` (senza console) si preferisce
    comunque ``python.exe`` per il figlio, che gestisce meglio le pipe.
    """
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return str(console)
    return str(exe)


def cartella_gpu() -> Path:
    """Cartella ProPainter usata dall'installer GPU, con fallback CLI."""
    from wmremove.backends import resolve_propainter_dir

    return resolve_propainter_dir(ROOT_DIR / "gpu" / "ProPainter")


def torch_installato() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def gpu_torch_disponibile() -> bool:
    if not torch_installato():
        return False
    import torch

    return bool(torch.cuda.is_available())


class Applicazione(ttk.Frame):
    def __init__(self, master: tk.Tk, video_iniziale: str | None = None) -> None:
        super().__init__(master, padding=14)
        self.master: tk.Tk = master
        self.processo: subprocess.Popen | None = None
        self.coda: queue.Queue[str | None] = queue.Queue()
        self.ultimo_output: Path | None = None

        self.var_input = tk.StringVar()
        self.var_output = tk.StringVar()
        self.var_motore = tk.StringVar(value=MOTORI[0][1])
        self.var_confronto = tk.BooleanVar(value=True)
        self.var_diagnostica = tk.BooleanVar(value=False)
        self.var_solo_analisi = tk.BooleanVar(value=False)
        self.var_stato = tk.StringVar(value="Pronto")

        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)

        self._costruisci()
        self.var_input.trace_add("write", lambda *_: self._proponi_uscita())
        if video_iniziale:
            self.var_input.set(video_iniziale)

    # ------------------------------------------------------------------ UI

    def _costruisci(self) -> None:
        riga = 0

        gruppo_file = ttk.LabelFrame(self, text="Video da ripulire", padding=10)
        gruppo_file.grid(row=riga, column=0, sticky="ew")
        gruppo_file.columnconfigure(0, weight=1)
        ttk.Entry(gruppo_file, textvariable=self.var_input).grid(row=0, column=0, sticky="ew")
        ttk.Button(gruppo_file, text="Sfoglia...", command=self._scegli_input).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.etichetta_trascina = ttk.Label(
            gruppo_file,
            text="Puoi anche trascinare qui il file, o sull'icona del programma.",
            foreground="#666666",
        )
        self.etichetta_trascina.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        riga += 1

        gruppo_uscita = ttk.LabelFrame(self, text="Salva il risultato come", padding=10)
        gruppo_uscita.grid(row=riga, column=0, sticky="ew", pady=(10, 0))
        gruppo_uscita.columnconfigure(0, weight=1)
        ttk.Entry(gruppo_uscita, textvariable=self.var_output).grid(row=0, column=0, sticky="ew")
        ttk.Button(gruppo_uscita, text="Sfoglia...", command=self._scegli_output).grid(
            row=0, column=1, padx=(8, 0)
        )
        riga += 1

        gruppo_opzioni = ttk.LabelFrame(self, text="Opzioni", padding=10)
        gruppo_opzioni.grid(row=riga, column=0, sticky="ew", pady=(10, 0))
        gruppo_opzioni.columnconfigure(1, weight=1)
        ttk.Label(gruppo_opzioni, text="Motore").grid(row=0, column=0, sticky="w")
        self.scelta_motore = ttk.Combobox(
            gruppo_opzioni,
            textvariable=self.var_motore,
            values=[descrizione for _, descrizione in MOTORI],
            state="readonly",
        )
        self.scelta_motore.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        ttk.Checkbutton(
            gruppo_opzioni, text="Crea anche il video di confronto prima/dopo",
            variable=self.var_confronto,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            gruppo_opzioni, text="Salva i file di diagnostica (maschere, matte, grafici)",
            variable=self.var_diagnostica,
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            gruppo_opzioni, text="Solo analisi: individua la filigrana senza ricostruire il video",
            variable=self.var_solo_analisi, command=self._aggiorna_stato_controlli,
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        riga += 1

        barra = ttk.Frame(self)
        barra.grid(row=riga, column=0, sticky="ew", pady=(12, 0))
        barra.columnconfigure(2, weight=1)
        self.bottone_avvia = ttk.Button(barra, text="Avvia", command=self._avvia)
        self.bottone_avvia.grid(row=0, column=0)
        self.bottone_ferma = ttk.Button(
            barra, text="Interrompi", command=self._interrompi, state="disabled"
        )
        self.bottone_ferma.grid(row=0, column=1, padx=(8, 0))
        self.avanzamento = ttk.Progressbar(barra, mode="determinate", maximum=100)
        self.avanzamento.grid(row=0, column=2, sticky="ew", padx=(12, 0))
        riga += 1

        ttk.Label(self, textvariable=self.var_stato).grid(
            row=riga, column=0, sticky="w", pady=(8, 4)
        )
        riga += 1

        cornice_log = ttk.Frame(self)
        cornice_log.grid(row=riga, column=0, sticky="nsew")
        cornice_log.columnconfigure(0, weight=1)
        cornice_log.rowconfigure(0, weight=1)
        self.log = tk.Text(
            cornice_log, height=12, wrap="none", state="disabled",
            background="#111418", foreground="#d6dae0", insertbackground="#d6dae0",
            font=("Consolas" if sys.platform == "win32" else "Menlo", 10), relief="flat",
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        scorrimento = ttk.Scrollbar(cornice_log, orient="vertical", command=self.log.yview)
        scorrimento.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scorrimento.set)
        riga += 1

        piede = ttk.Frame(self)
        piede.grid(row=riga, column=0, sticky="ew", pady=(10, 0))
        self.bottone_cartella = ttk.Button(
            piede, text="Apri la cartella dei risultati",
            command=self._apri_cartella, state="disabled",
        )
        self.bottone_cartella.grid(row=0, column=0)

    # ------------------------------------------------------- interazione

    def _scegli_input(self) -> None:
        percorso = filedialog.askopenfilename(
            title="Scegli il video",
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"), ("Tutti i file", "*.*")],
        )
        if percorso:
            self.var_input.set(percorso)

    def _scegli_output(self) -> None:
        percorso = filedialog.asksaveasfilename(
            title="Salva il video ripulito", defaultextension=".mp4",
            filetypes=[("Video MP4", "*.mp4")],
        )
        if percorso:
            self.var_output.set(percorso)

    def _proponi_uscita(self) -> None:
        entrata = self.var_input.get().strip()
        if not entrata:
            return
        origine = Path(entrata)
        proposta = origine.with_name(f"{origine.stem}_pulito.mp4")
        self.var_output.set(str(proposta))

    def _aggiorna_stato_controlli(self) -> None:
        stato = "disabled" if self.var_solo_analisi.get() else "normal"
        for widget in (self.bottone_cartella,):
            widget.configure(state=stato if self.ultimo_output else "disabled")

    def _apri_cartella(self) -> None:
        if not self.ultimo_output:
            return
        cartella = self.ultimo_output.parent
        if sys.platform == "win32":
            os.startfile(cartella)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(cartella)])
        else:
            subprocess.Popen(["xdg-open", str(cartella)])

    def _scrivi(self, testo: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", testo.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -------------------------------------------------------- esecuzione

    def _motore_scelto(self) -> str:
        descrizione = self.var_motore.get()
        for chiave, testo in MOTORI:
            if testo == descrizione:
                return chiave
        return "classic"

    def _comando(self) -> list[str] | None:
        entrata = self.var_input.get().strip()
        if not entrata:
            messagebox.showwarning(APP_NAME, "Scegli prima un video da ripulire.")
            return None
        if not Path(entrata).exists():
            messagebox.showerror(APP_NAME, f"Il file non esiste:\n{entrata}")
            return None

        motore = self._motore_scelto()
        if motore == "propainter":
            script_pp = cartella_gpu() / "inference_propainter.py"
            if not script_pp.exists():
                messagebox.showerror(
                    APP_NAME,
                    "Il motore ProPainter non e' ancora installato.\n\n"
                    "Apri dal menu Start la voce \"Aggiungi il supporto GPU\" "
                    "oppure esegui:\n"
                    "  python scripts\\installa_gpu.py\n\n"
                    "Servono Python 3.12, i driver AMD recenti e alcuni "
                    "gigabyte di download.",
                )
                return None
            if not gpu_torch_disponibile():
                messagebox.showerror(
                    APP_NAME,
                    "ProPainter richiede una GPU con PyTorch CUDA/ROCm.\n\n"
                    "Qui PyTorch non vede alcuna GPU (oppure non e' "
                    "installato). Su Windows con scheda AMD usa "
                    "\"Aggiungi il supporto GPU\" con Python 3.12.",
                )
                return None
        if motore == "lama" and not torch_installato():
            messagebox.showerror(
                APP_NAME,
                "Il motore LaMa richiede PyTorch (non usa iopaint).\n\n"
                "Dal menu Start: \"Aggiungi il supporto LaMa\",\n"
                "oppure, se hai gia' il supporto GPU AMD, riapri il programma.\n\n"
                "I pesi della rete (~200 MB) si scaricano al primo utilizzo.",
            )
            return None

        uscita = Path(self.var_output.get().strip() or "risultato.mp4")
        # -E ignora le variabili PYTHON* del sistema, che potrebbero puntare a
        # un'altra installazione; -X utf8 tiene i messaggi in UTF-8 anche
        # quando l'uscita e' una pipe.
        comando = [
            interprete(), "-E", "-X", "utf8", str(ROOT_DIR / "dewatermark.py"),
            "--input", entrata,
            "--output", str(uscita),
            "--backend", motore,
        ]
        if self.var_solo_analisi.get():
            comando.append("--detect-only")
        else:
            if self.var_confronto.get():
                comando.append("--compare")
        if self.var_diagnostica.get() or self.var_solo_analisi.get():
            comando += ["--debug-dir", str(uscita.parent / "diagnostica")]
        if motore == "propainter":
            # ProPainter scrive molti file temporanei: meglio accanto
            # al risultato che dentro la cartella di installazione.
            comando += [
                "--work-dir", str(uscita.parent / "lavoro-temporaneo"),
                "--propainter-dir", str(cartella_gpu()),
            ]
        self.ultimo_output = uscita
        return comando

    def _avvia(self) -> None:
        comando = self._comando()
        if comando is None:
            return

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.avanzamento.configure(value=0)
        self.bottone_avvia.configure(state="disabled")
        self.bottone_ferma.configure(state="normal")
        self.bottone_cartella.configure(state="disabled")
        self.var_stato.set("Elaborazione in corso...")
        self._scrivi("> " + " ".join(comando) + "\n")

        opzioni: dict = {}
        if sys.platform == "win32":
            opzioni["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self.processo = subprocess.Popen(
                comando,
                cwd=str(ROOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **opzioni,
            )
        except OSError as errore:
            self._scrivi(f"impossibile avviare il programma: {errore}")
            self._concludi(-1)
            return

        threading.Thread(target=self._leggi_output, daemon=True).start()
        self.after(100, self._svuota_coda)

    def _leggi_output(self) -> None:
        assert self.processo and self.processo.stdout
        for riga in self.processo.stdout:
            self.coda.put(riga)
        self.processo.wait()
        self.coda.put(None)

    def _svuota_coda(self) -> None:
        try:
            while True:
                riga = self.coda.get_nowait()
                if riga is None:
                    self._concludi(self.processo.returncode if self.processo else -1)
                    return
                self._scrivi(riga)
                trovato = PROGRESS_RE.search(riga)
                if trovato:
                    fatti, totali = int(trovato.group(1)), int(trovato.group(2))
                    if totali:
                        self.avanzamento.configure(value=100.0 * fatti / totali)
        except queue.Empty:
            pass
        self.after(100, self._svuota_coda)

    def _interrompi(self) -> None:
        if self.processo and self.processo.poll() is None:
            self.processo.terminate()
            self.var_stato.set("Interrotto")

    def _concludi(self, codice: int) -> None:
        self.bottone_avvia.configure(state="normal")
        self.bottone_ferma.configure(state="disabled")
        self.processo = None
        if codice == 0:
            self.avanzamento.configure(value=100)
            self.var_stato.set("Completato")
            self.bottone_cartella.configure(state="normal")
        else:
            self.var_stato.set(f"Terminato con errore (codice {codice})")

    # ------------------------------------------- trascinamento su Windows

    def abilita_trascinamento(self) -> None:
        """Accetta i file trascinati dentro la finestra (solo Windows).

        Windows non offre nulla di pronto in Tk: bisogna dire alla finestra di
        accettare i file e intercettare il messaggio WM_DROPFILES. Se qualcosa
        va storto si prosegue senza: restano il pulsante Sfoglia e il
        trascinamento sull'icona del programma.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            WM_DROPFILES = 0x0233
            GWLP_WNDPROC = -4
            GA_ROOT = 2

            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32

            hwnd = user32.GetAncestor(wintypes.HWND(self.master.winfo_id()), GA_ROOT)
            if not hwnd:
                return

            prototipo = ctypes.WINFUNCTYPE(
                ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                ctypes.c_void_p, ctypes.c_void_p,
            )
            user32.CallWindowProcW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

            precedente = ctypes.c_void_p()

            def procedura(finestra, messaggio, wparam, lparam):
                if messaggio == WM_DROPFILES:
                    buffer = ctypes.create_unicode_buffer(1024)
                    shell32.DragQueryFileW(
                        ctypes.c_void_p(wparam), 0, buffer, ctypes.sizeof(buffer)
                    )
                    shell32.DragFinish(ctypes.c_void_p(wparam))
                    if buffer.value:
                        self.master.after(0, self.var_input.set, buffer.value)
                    return 0
                return user32.CallWindowProcW(
                    precedente, finestra, messaggio, wparam, lparam
                )

            callback = prototipo(procedura)
            self._riferimenti = (callback, precedente)  # va tenuto vivo
            precedente.value = user32.SetWindowLongPtrW(
                hwnd, GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p)
            )
            shell32.DragAcceptFiles(hwnd, True)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    argomenti = list(sys.argv[1:] if argv is None else argv)
    video = argomenti[0] if argomenti else None

    radice = tk.Tk()
    radice.title(APP_NAME)
    radice.minsize(720, 620)
    icona = ROOT_DIR / "icona.ico"
    if icona.exists() and sys.platform == "win32":
        try:
            radice.iconbitmap(str(icona))
        except tk.TclError:
            pass
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass

    applicazione = Applicazione(radice, video)
    applicazione.abilita_trascinamento()
    radice.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
