#!/usr/bin/env bash
# Costruisce VideoWatermarkRemover-Setup.exe, l'installer per Windows.
#
# Funziona anche da macOS o Linux: serve makensis (brew install makensis,
# oppure apt install nsis) e uv per scaricare le librerie Python nella
# variante Windows.
#
#   ./installer/compila.sh                    # tutto dentro installer/build
#   BUILD=~/wmr-build ./installer/compila.sh  # cartella di lavoro altrove
#
# La cartella di lavoro viene riusata tra una compilazione e l'altra: il
# runtime Python si riscarica solo se manca.

set -euo pipefail

QUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROGETTO="$(dirname "$QUI")"
BUILD="${BUILD:-$QUI/build}"
USCITA="${USCITA:-$PROGETTO/VideoWatermarkRemover-Setup.exe}"

VERSIONE_PYTHON="3.12.13"
RILASCIO_PYTHON="20260718"
ARCHIVIO="cpython-${VERSIONE_PYTHON}+${RILASCIO_PYTHON}-x86_64-pc-windows-msvc-install_only.tar.gz"
URL_PYTHON="https://github.com/astral-sh/python-build-standalone/releases/download/${RILASCIO_PYTHON}/${ARCHIVIO}"

if [ -z "${PYTHON_LOCALE:-}" ]; then
  if [ -x "$PROGETTO/.venv/bin/python" ]; then
    PYTHON_LOCALE="$PROGETTO/.venv/bin/python"
  else
    PYTHON_LOCALE="python3"
  fi
fi

echo "progetto:      $PROGETTO"
echo "lavoro in:     $BUILD"
echo "risultato:     $USCITA"
mkdir -p "$BUILD"

# ------------------------------------------------- 1. runtime Python Windows
if [ ! -f "$BUILD/python/python.exe" ]; then
  echo
  echo "== Scarico Python ${VERSIONE_PYTHON} per Windows =="
  [ -f "$BUILD/$ARCHIVIO" ] || curl -# -L -o "$BUILD/$ARCHIVIO" "$URL_PYTHON"
  rm -rf "$BUILD/python"
  tar xzf "$BUILD/$ARCHIVIO" -C "$BUILD"

  echo "== Tolgo cio' che non serve a far girare il programma =="
  cd "$BUILD/python"
  find . -name "*.pdb" -delete
  rm -rf include libs Lib/idlelib Lib/turtledemo Lib/ensurepip Lib/test
  find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
  cd "$BUILD"

  echo "== Aggiungo le librerie nella variante Windows =="
  rm -rf "$BUILD/sp"
  UV_LINK_MODE=copy uv pip install \
    --python-platform x86_64-pc-windows-msvc --python-version 3.12 \
    --target "$BUILD/sp" \
    "opencv-python-headless>=4.8,<5" numpy imageio-ffmpeg tqdm
  rm -rf "$BUILD/sp/bin" "$BUILD/sp/images"
  cp -R "$BUILD/sp/." "$BUILD/python/Lib/site-packages/"
  rm -rf "$BUILD/sp"
else
  echo "runtime Python gia' pronto (cancella $BUILD/python per rifarlo)"
fi

# ------------------------------------------------------------ 2. programma
echo
echo "== Preparo i file del programma =="
[ -f "$QUI/icona.ico" ] || "$PYTHON_LOCALE" "$QUI/crea_icona.py" "$QUI/icona.ico"

rm -rf "$BUILD/app"
mkdir -p "$BUILD/app"
cp "$PROGETTO/dewatermark.py" "$PROGETTO/interfaccia.py" "$PROGETTO/README.md" "$BUILD/app/"
cp "$QUI/icona.ico" "$BUILD/app/"
cp -R "$PROGETTO/wmremove" "$PROGETTO/scripts" "$BUILD/app/"
find "$BUILD/app" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$BUILD/app" -name "._*" -delete 2>/dev/null || true

# ---------------------------------------------------------- 3. compilazione
echo
echo "== Compilo l'installer =="
cd "$QUI"
makensis -V2 -DBUILD="$BUILD" -DUSCITA="$USCITA" installer.nsi

echo
ls -lh "$USCITA"
echo "fatto."
