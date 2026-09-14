# -*- mode: python ; coding: utf-8 -*-
import os
import ssl

block_cipher = None
certificate_datas = []
certificate_candidates = [
    ssl.get_default_verify_paths().cafile,
    '/etc/ssl/cert.pem',
    '/private/etc/ssl/cert.pem',
    '/etc/ssl/certs/ca-certificates.crt',
]
certificate_file = next(
    (path for path in certificate_candidates if path and os.path.isfile(path)),
    None,
)
if certificate_file:
    certificate_datas.append((certificate_file, 'certificates'))

our_datas = [
    ('styles/main.qss', 'styles'),
    ('styles/logs.qss', 'styles'),
    ('styles/theme.py', 'styles'),
    ('config_matcher.yaml', '.'),
    ('icon.icns', '.'),
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=certificate_datas + our_datas,
    hiddenimports=[
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'parser', 'ozon_parser', 'ozon_marker', 'supply_detector',
        'indexer', 'matcher', 'manual_overrides',
        'core.paths', 'core.logger', 'core.store_manager', 'core.pdf_cache',
        'core.cancellation', 'core.json_storage',
        'core.yandex_disk', 'core.storage_backend',
        'gui.main_window', 'gui.settings_window',
        'gui.dialogs.new_store_dialog', 'gui.dialogs.log_window',
        'gui.dialogs.parse_progress_dialog',
        'gui.widgets.supply_tree', 'gui.widgets.override_delegate',
        'pdfplumber', 'fitz', 'pymupdf', 'yaml', 'ssl',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'tkinterdnd2', 'matplotlib', 'numpy', 'scipy', 'pandas',
        'IPython', 'jupyter', 'notebook', 'pytest', 'setuptools', 'pip',
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
    [],
    exclude_binaries=True,
    name='RexArticles',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RexArticles',
)

app = BUNDLE(
    coll,
    name='RexArticles.app',
    icon='icon.icns',
    bundle_identifier='ru.rexarticles.app',
    info_plist={
        'CFBundleDisplayName': 'RexArticles',
        'NSHighResolutionCapable': True,
    },
)
