"""Offline-only tests; recordings, if present, are opened read-only."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PyQt5 import QtGui, QtWidgets
from iqcs16_spectrum import CS16File, Viewer, spectrogram


def save_cs16(path, iq):
    raw = np.empty((len(iq), 2), dtype='<i2')
    raw[:, 0] = np.round(np.real(iq) * 32767).astype(np.int16)
    raw[:, 1] = np.round(np.imag(iq) * 32767).astype(np.int16)
    raw.tofile(path)


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        font = Path('C:/Windows/Fonts/msjh.ttc')
        if font.is_file():
            QtGui.QFontDatabase.addApplicationFont(str(font))
        cls.app.setFont(QtGui.QFont('Microsoft JhengHei', 10))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.cs16'

    def tearDown(self):
        self.temp.cleanup()

    def wait_viewer(self, viewer):
        deadline = time.monotonic() + 15
        while viewer.loading and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(viewer.loading, 'background FFT timeout')
        self.assertIsNotNone(viewer.last_result, viewer.info.text())

    def test_iq_endianness_and_bounds(self):
        self.path.write_bytes(bytes.fromhex('FF7F0080004000C0'))
        source = CS16File(self.path)
        np.testing.assert_allclose(source.read(0, 20), [1 - 32768j / 32767, (16384 - 16384j) / 32767])
        self.assertEqual(len(source.read(2, 10)), 0)
        self.assertEqual(source.samples, 2)

    def test_invalid_empty_and_unaligned_files(self):
        for data in (b'', b'123', b'12345'):
            self.path.write_bytes(data)
            with self.assertRaises(ValueError):
                CS16File(self.path)

    def test_metadata_and_malformed_fallback(self):
        self.path.write_bytes(b'\0' * 40)
        sidecar = Path(str(self.path) + '.json')
        sidecar.write_text(json.dumps({'sample_rate': 96000}), encoding='utf-8-sig')
        self.assertEqual(CS16File(self.path).metadata['sample_rate'], 96000)
        sidecar.write_text('{broken', encoding='utf-8')
        self.assertTrue(CS16File(self.path).warning)

    def test_tone_peak_and_absolute_time(self):
        fs, nfft = 100000, 2048
        t = np.arange(250000) / fs
        save_cs16(self.path, 0.25 * np.exp(2j * np.pi * (80 * fs / nfft) * t))
        source = CS16File(self.path)
        values, left, width, dt = spectrogram(source, fs, nfft, 1.0, 1.0)
        self.assertEqual(values.shape, (2048, 50))
        self.assertEqual(int(np.argmax(values[:, 0])), 1024 + 80)
        self.assertAlmostEqual(float(values[1104, 0]), 20 * np.log10(.25), places=2)
        self.assertAlmostEqual(left + dt / 2, 1 + nfft / (2 * fs))
        self.assertAlmostEqual(width, 1)

    def test_tail_cancel_and_memory_limit(self):
        save_cs16(self.path, np.ones(2100, dtype=np.complex64) * .25)
        source = CS16File(self.path)
        result = spectrogram(source, 100000, 2048, 0, 30)
        self.assertEqual(result[0].shape, (2048, 2))
        self.assertTrue(np.isfinite(result[0]).all())
        cancelled = threading.Event()
        cancelled.set()
        self.assertIsNone(spectrogram(source, 100000, 2048, 0, 30, cancelled))
        with self.assertRaises(ValueError):
            spectrogram(source, 1, 16384, 0, 2100)

    def test_gui_play_seek_and_preview(self):
        fs = 100000
        t = np.arange(3 * fs) / fs
        rng = np.random.default_rng(42)
        iq = .0002 * (rng.normal(size=len(t)) + 1j * rng.normal(size=len(t)))
        iq += .1 * np.exp(2j * np.pi * 4000 * t) * (((t > .5) & (t < .77)) | ((t > 1.5) & (t < 1.77)))
        save_cs16(self.path, iq)
        Path(str(self.path) + '.json').write_text(json.dumps({'sample_rate': fs, 'center_frequency_hz': 435400000}), encoding='utf-8')
        viewer = Viewer()
        try:
            viewer.span.setValue(1)
            viewer.show()
            viewer.open_file(self.path)
            self.wait_viewer(viewer)
            self.assertEqual(viewer.fs, fs)
            viewer.seek(1.5)
            self.wait_viewer(viewer)
            self.assertEqual(viewer.page_start, 1)
            self.assertAlmostEqual(viewer.position, 1.5)
            viewer.toggle_play()
            viewer.last_tick -= .1
            viewer.tick()
            self.assertGreater(viewer.position, 1.59)
            viewer.pause()
            self.assertFalse(viewer.playing)
            viewer.seek(3)
            self.wait_viewer(viewer)
            self.assertEqual(viewer.page_start, 2)
            viewer.toggle_play()
            self.wait_viewer(viewer)
            self.assertLess(viewer.position, 1)
            viewer.pause()
            viewer.span.setValue(3)
            viewer.apply_settings()
            self.wait_viewer(viewer)
            viewer.file_label.setText('SYNTHETIC TEST PREVIEW — 100000 samples/s, 270 ms bursts (not recorded RF)')
            self.app.processEvents()
            self.assertTrue(viewer.grab().save(str(Path(__file__).with_name('IQ_spectrum_preview.png'))))
            # Rapid changes must not allow a stale worker to overwrite the final page.
            viewer.span.setValue(1)
            viewer.apply_settings()
            viewer.seek(1.2)
            viewer.seek(2.2)
            self.wait_viewer(viewer)
            self.assertGreaterEqual(viewer.last_result[1], 2)
        finally:
            viewer.close()

    def test_real_recording_read_only(self):
        directory = Path(__file__).resolve().parent.parent / 'IQ_Recordings'
        candidates = sorted(directory.glob('*.cs16'))
        if not candidates:
            self.skipTest('No local recording')
        source = CS16File(candidates[-1])
        viewer = Viewer()
        try:
            viewer.open_file(source.path)
            self.wait_viewer(viewer)
            self.assertTrue(np.isfinite(viewer.last_result[0]).all())
            self.assertEqual(viewer.fs, float(source.metadata.get('sample_rate', 100000)))
            print(f'READ ONLY: {source.path.name}: Fs={viewer.fs:g}, samples={source.samples}, duration={viewer.duration:.5f}s')
            viewer.seek(viewer.duration)
            self.wait_viewer(viewer)
        finally:
            viewer.close()


if __name__ == '__main__':
    unittest.main()
