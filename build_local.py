"""Build using wheel paths, preserving Unicode Windows directory names."""
from pathlib import Path
import sys

from PyInstaller.utils.hooks import qt
from PyInstaller.__main__ import run


def main():
    # PyQt5's embedded Qt5 qt.conf stores its prefix as Latin-1. A Chinese
    # install path can therefore be returned as question marks by QLibraryInfo.
    # Use the actual wheel directory for collection with pinned PyInstaller.
    info = qt.pyqt5_library_info
    if not info.version:
        raise RuntimeError('The bundled PyQt5 could not be loaded')
    prefix = info.package_location / 'Qt5'
    if not (prefix / 'plugins').is_dir() or not (prefix / 'bin').is_dir():
        raise RuntimeError('Incomplete bundled Qt5 wheel')
    reported_prefix = info.location['PrefixPath'].replace('\\', '/')
    actual_prefix = prefix.as_posix()
    info.location = {key: value.replace('\\', '/').replace(reported_prefix, actual_prefix)
                     for key, value in info.location.items()}
    info.qt_inside_package = True
    info.qt_lib_dir = prefix / 'bin'
    run(sys.argv[1:])


if __name__ == '__main__':
    main()
