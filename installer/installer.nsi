; Installer di Video Watermark Remover per Windows 11 (64 bit).
;
; Si compila con makensis (anche da macOS o Linux) dopo aver preparato la
; cartella build/ con lo script compila.sh:
;
;     ./installer/compila.sh
;
; L'installazione e' per il singolo utente, sotto %LOCALAPPDATA%\Programs: cosi'
; non serve l'elevazione a amministratore e la disinstallazione e' pulita.

Unicode true

!include "MUI2.nsh"
!include "FileFunc.nsh"

!define NOME        "Video Watermark Remover"
!define VERSIONE    "1.0.0"
!define EDITORE     "Progetto didattico"
!define CHIAVE      "VideoWatermarkRemover"

; Cartella con i file gia' pronti da impacchettare. Si puo' spostare altrove
; con -DBUILD=... , utile per compilare da un disco veloce.
!ifndef BUILD
  !define BUILD "build"
!endif

!ifndef USCITA
  !define USCITA "../VideoWatermarkRemover-Setup.exe"
!endif

Name "${NOME}"
OutFile "${USCITA}"
InstallDir "$LOCALAPPDATA\Programs\${CHIAVE}"
InstallDirRegKey HKCU "Software\${CHIAVE}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "1.0.0.0"
VIAddVersionKey /LANG=1040 "ProductName"     "${NOME}"
VIAddVersionKey /LANG=1040 "FileDescription" "Installer di ${NOME}"
VIAddVersionKey /LANG=1040 "FileVersion"     "${VERSIONE}"
VIAddVersionKey /LANG=1040 "ProductVersion"  "${VERSIONE}"
VIAddVersionKey /LANG=1040 "CompanyName"     "${EDITORE}"
VIAddVersionKey /LANG=1040 "LegalCopyright"  "Uso didattico"

!define MUI_ICON   "icona.ico"
!define MUI_UNICON "icona.ico"
!define MUI_ABORTWARNING

!define MUI_WELCOMEPAGE_TITLE "Installazione di ${NOME}"
!define MUI_WELCOMEPAGE_TEXT  "Questo programma toglie dai video le filigrane semitrasparenti, comprese quelle che si spostano da un angolo all'altro.$\r$\n$\r$\nVerranno installati il programma, la sua interfaccia e un ambiente Python 3.12 completo e indipendente: non serve avere Python sul computer, e nulla di gia' installato viene toccato.$\r$\n$\r$\nServono circa 800 MB di spazio su disco.$\r$\n$\r$\nIl supporto per la scheda video AMD si aggiunge dopo, dal menu Start, quando serve."

!define MUI_FINISHPAGE_RUN            "$INSTDIR\python\pythonw.exe"
; Le virgolette vanno protette: MUI monta la riga dentro un'altra stringa.
!define MUI_FINISHPAGE_RUN_PARAMETERS '-E -X utf8 $\"$INSTDIR\app\interfaccia.py$\"'
!define MUI_FINISHPAGE_RUN_TEXT       "Avvia ${NOME}"
!define MUI_FINISHPAGE_LINK           "Leggi la documentazione del progetto"
!define MUI_FINISHPAGE_LINK_LOCATION  "$INSTDIR\app\README.md"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "Italian"

; ---------------------------------------------------------------- sezioni

Section "Programma e ambiente Python" SEZIONE_BASE
  SectionIn RO

  SetOutPath "$INSTDIR"
  File "icona.ico"
  File "riga-di-comando.cmd"
  File "supporto-gpu.cmd"

  DetailPrint "Copia dell'ambiente Python..."
  File /r "${BUILD}/python"

  DetailPrint "Copia del programma..."
  File /r "${BUILD}/app"

  ; Menu Start
  CreateDirectory "$SMPROGRAMS\${NOME}"
  CreateShortcut "$SMPROGRAMS\${NOME}\${NOME}.lnk" \
    "$INSTDIR\python\pythonw.exe" '-E -X utf8 "$INSTDIR\app\interfaccia.py"' \
    "$INSTDIR\icona.ico" 0 SW_SHOWNORMAL "" "Toglie la filigrana dai video"
  CreateShortcut "$SMPROGRAMS\${NOME}\Riga di comando.lnk" \
    "$INSTDIR\riga-di-comando.cmd" "" "$INSTDIR\icona.ico" 0
  CreateShortcut "$SMPROGRAMS\${NOME}\Aggiungi il supporto GPU AMD.lnk" \
    "$INSTDIR\supporto-gpu.cmd" "" "$INSTDIR\icona.ico" 0
  CreateShortcut "$SMPROGRAMS\${NOME}\Disinstalla.lnk" "$INSTDIR\uninstall.exe"

  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "Software\${CHIAVE}" "InstallDir" "$INSTDIR"

  !define DISINSTALLA "Software\Microsoft\Windows\CurrentVersion\Uninstall\${CHIAVE}"
  WriteRegStr HKCU "${DISINSTALLA}" "DisplayName"     "${NOME}"
  WriteRegStr HKCU "${DISINSTALLA}" "DisplayVersion"  "${VERSIONE}"
  WriteRegStr HKCU "${DISINSTALLA}" "Publisher"       "${EDITORE}"
  WriteRegStr HKCU "${DISINSTALLA}" "DisplayIcon"     "$INSTDIR\icona.ico"
  WriteRegStr HKCU "${DISINSTALLA}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${DISINSTALLA}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${DISINSTALLA}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD HKCU "${DISINSTALLA}" "NoModify" 1
  WriteRegDWORD HKCU "${DISINSTALLA}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${DISINSTALLA}" "EstimatedSize" "$0"
SectionEnd

Section "Collegamento sul desktop" SEZIONE_DESKTOP
  CreateShortcut "$DESKTOP\${NOME}.lnk" \
    "$INSTDIR\python\pythonw.exe" '-E -X utf8 "$INSTDIR\app\interfaccia.py"' \
    "$INSTDIR\icona.ico" 0 SW_SHOWNORMAL "" \
    "Trascina qui un video per ripulirlo"
SectionEnd

LangString DESC_BASE    ${LANG_ITALIAN} \
  "Il programma, l'interfaccia grafica e un Python 3.12 completo e separato dal resto del sistema."
LangString DESC_DESKTOP ${LANG_ITALIAN} \
  "Mette l'icona sul desktop. Ci puoi anche trascinare sopra un video per aprirlo gia' pronto."

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEZIONE_BASE}    $(DESC_BASE)
  !insertmacro MUI_DESCRIPTION_TEXT ${SEZIONE_DESKTOP} $(DESC_DESKTOP)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; ----------------------------------------------------------- disinstalla

Section "Uninstall"
  ; Rete di sicurezza: cancella solo se dentro c'e' davvero il programma.
  IfFileExists "$INSTDIR\app\dewatermark.py" 0 non_e_qui

  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\gpu"
  Delete "$INSTDIR\icona.ico"
  Delete "$INSTDIR\riga-di-comando.cmd"
  Delete "$INSTDIR\supporto-gpu.cmd"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"

  Delete "$DESKTOP\${NOME}.lnk"
  RMDir /r "$SMPROGRAMS\${NOME}"

  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${CHIAVE}"
  DeleteRegKey HKCU "Software\${CHIAVE}"
  Goto fine

non_e_qui:
  MessageBox MB_ICONSTOP \
    "In $INSTDIR non c'e' ${NOME}: non tocco nulla."

fine:
SectionEnd
