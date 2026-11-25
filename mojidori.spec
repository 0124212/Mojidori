# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller bundle for Mojidori (Windows EXE).
Built automatically by .github/workflows/build-exe.yml — never build by hand.
Single-folder bundle: everything the app needs sits next to the EXE,
so queue/ and processed/ live beside it, fully portable.
"""
from PyInstaller.utils.hooks import collect_all

whisper_datas, whisper_binaries, whisper_hidden = collect_all('whisper')
fw_datas, fw_binaries, fw_hidden = collect_all('faster_whisper')
ct2_datas, ct2_binaries, ct2_hidden = collect_all('ctranslate2')
yt_datas, yt_binaries, yt_hidden = collect_all('yt_dlp')        # extractors load lazily
ejs_datas, ejs_binaries, ejs_hidden = collect_all('yt_dlp_ejs')  # YouTube JS solver

a = Analysis(
    ['start.py'],
    pathex=[],
    binaries=whisper_binaries + fw_binaries + ct2_binaries + yt_binaries + ejs_binaries,
    datas=whisper_datas + fw_datas + ct2_datas + yt_datas + ejs_datas + [
        ('templates', 'templates'),
        ('prompt_cheat_sheet.md', '.'),
        ('prompt_condensed_notes.md', '.'),
        ('prompt_descriptive_summary.md', '.'),
    ],
    hiddenimports=whisper_hidden + fw_hidden + ct2_hidden + yt_hidden + ejs_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'IPython', 'notebook', 'nbformat',
        'pytest', 'sphinx',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Mojidori',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX trips antivirus heuristics; skip it
    console=True,  # keep the console: it shows the URL + live status
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Mojidori',
)
