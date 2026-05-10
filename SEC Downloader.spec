# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['f:\\MyRepository\\FinancialAdvisor\\app.py'],
    pathex=[],
    binaries=[],
    datas=[('f:\\MyRepository\\FinancialAdvisor\\scrape_transcripts.py', '.'), ('C:\\Users\\Achilles\\AppData\\Local\\Programs\\Python\\Python311\\Lib\\site-packages\\datamule\\mapping_dicts\\xml_mapping_jsons', 'datamule\\mapping_dicts\\xml_mapping_jsons')],
    hiddenimports=['datamule', 'markdown_cleanup'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SEC Downloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
