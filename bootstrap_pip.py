"""Run the bundled pip wheel without any prior pip installation."""
from pathlib import Path
import runpy
import sys

wheel = Path(__file__).resolve().parent / 'packages' / 'wheels' / 'pip-25.0.1-py3-none-any.whl'
if not wheel.is_file():
    raise SystemExit(f'Missing offline pip wheel: {wheel}')
sys.path.insert(0, str(wheel))
runpy.run_module('pip', run_name='__main__')
