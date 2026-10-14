"""Windowed executable entry point, with an offline packaging verification mode."""
import sys


def run():
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        import io
        import json
        import os
        from pathlib import Path
        import unittest

        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        import test_iqcs16_spectrum

        output = io.StringIO()
        suite = unittest.defaultTestLoader.loadTestsFromModule(test_iqcs16_spectrum)
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
        Path(sys.argv[2]).write_text(json.dumps({
            'success': result.wasSuccessful(),
            'tests_run': result.testsRun,
            'skipped': len(result.skipped),
            'details': output.getvalue(),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0 if result.wasSuccessful() else 1

    from iqcs16_spectrum import main
    return main()


if __name__ == '__main__':
    raise SystemExit(run())
