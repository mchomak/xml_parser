# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Windows .exe build
# =============================================
#
# Usage:
#   pip install pyinstaller
#   pyinstaller exnode_exporter.spec
#
# Output: dist/exnode-exporter.exe

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('config.yaml', '.'),
        ('src/*.py', 'src'),
    ],
    hiddenimports=[
        'aiohttp',
        'yaml',
        'asyncio',
        'xml.etree.ElementTree',
        'xml.dom.minidom',
        'json',
        'logging',
        'signal',
        'tempfile',
        'pathlib',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='exnode-exporter',
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
    icon=None,  # Add icon path if desired: 'icon.ico'
)
