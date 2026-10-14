"""Read-only CS16 viewer; no GNU Radio or radio hardware dependencies."""
import argparse
import json
import math
from pathlib import Path
import sys
import threading
import time

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg


class CS16File:
    def __init__(self, path):
        self.path = Path(path).resolve()
        size = self.path.stat().st_size
        if size == 0 or size % 4:
            raise ValueError('CS16 必須非空，且檔案大小必須為 4 bytes 的整數倍（I16 + Q16）。')
        self.samples = size // 4
        self.metadata = {}
        self.warning = ''
        sidecar = Path(str(self.path) + '.json')
        if sidecar.is_file():
            try:
                data = json.loads(sidecar.read_text(encoding='utf-8-sig'))
                if not isinstance(data, dict):
                    raise ValueError('metadata must be an object')
                rate = float(data['sample_rate'])
                if not math.isfinite(rate) or not 1 <= rate <= 100_000_000:
                    raise ValueError('invalid sample_rate')
                self.metadata = data
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.warning = f'無法採用 JSON 取樣率：{exc}；請手動設定 Fs。'

    def read(self, start, count):
        start = max(0, min(int(start), self.samples))
        count = max(0, min(int(count), self.samples - start))
        with self.path.open('rb') as handle:
            handle.seek(start * 4)
            raw = handle.read(count * 4)
        if len(raw) != count * 4:
            raise OSError('檔案長度已改變，請停止錄製後重新開啟。')
        iq = np.frombuffer(raw, dtype='<i2').reshape(-1, 2)
        return (iq[:, 0].astype(np.float32) + 1j * iq[:, 1].astype(np.float32)) / 32767.0


def spectrogram(source, fs, nfft, start_seconds, span_seconds, cancelled=None):
    """Globally aligned FFTs; include a zero-padded final window, bounded RAM."""
    if not math.isfinite(fs) or fs <= 0 or nfft < 32 or span_seconds <= 0:
        raise ValueError('Invalid FFT / sample-rate / duration')
    hop = max(1, min(nfft, round(fs * 0.02)))
    first = max(0, int(start_seconds * fs) // hop)
    end_sample = min(source.samples, math.ceil((start_seconds + span_seconds) * fs))
    stop = math.ceil(end_sample / hop)
    columns = max(0, stop - first)
    if columns > 12000 or columns * nfft > 16_000_000:
        raise ValueError('顯示資料量過大，請縮短「視窗秒數」或調小 FFT。')
    if not columns:
        raise ValueError('此區段沒有 IQ 資料。')
    window = np.hanning(nfft).astype(np.float32)
    norm = float(window.sum()) ** 2
    result = np.empty((nfft, columns), dtype=np.float32)
    for offset in range(0, columns, 64):
        if cancelled is not None and cancelled.is_set():
            return None
        batch = min(64, columns - offset)
        sample_start = (first + offset) * hop
        needed = (batch - 1) * hop + nfft
        iq = source.read(sample_start, needed)
        if len(iq) < needed:
            iq = np.pad(iq, (0, needed - len(iq)))
        frames = np.lib.stride_tricks.sliding_window_view(iq, nfft)[::hop][:batch]
        values = np.fft.fftshift(np.fft.fft(frames * window, axis=1), axes=1)
        result[:, offset:offset + batch] = (
            10 * np.log10(np.maximum(np.abs(values) ** 2 / norm, 1e-14))).T
    # Pixels are centered at the FFT window center and exact fftshift bin frequency.
    left = (first * hop + nfft / 2 - hop / 2) / fs
    return result, left, columns * hop / fs, hop / fs


class ResultSignals(QtCore.QObject):
    ready = QtCore.pyqtSignal(int, object)
    failed = QtCore.pyqtSignal(int, str)


class FFTJob(QtCore.QRunnable):
    def __init__(self, generation, args, cancelled):
        super().__init__()
        self.generation, self.args, self.cancelled = generation, args, cancelled
        self.signals = ResultSignals()

    def run(self):
        try:
            result = spectrogram(*self.args, cancelled=self.cancelled)
            if result is not None and not self.cancelled.is_set():
                self.signals.ready.emit(self.generation, result)
        except Exception as exc:
            if not self.cancelled.is_set():
                self.signals.failed.emit(self.generation, str(exc))


class Viewer(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('KOYO CS16 IQ Spectrum — 離線檢視（不控制 USRP / VSG）')
        self.resize(1250, 790)
        self.source = None
        self.fs = 100000.0
        self.position = 0.0
        self.page = None
        self.generation = 0
        self.cancelled = threading.Event()
        self.pool = QtCore.QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.playing = False
        self.loading = False
        self.last_result = None
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        self.file_label = QtWidgets.QLabel('請開啟 CS16：Little-endian、signed int16、I/Q 交錯、無檔頭')
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)
        settings = QtWidgets.QHBoxLayout()
        layout.addLayout(settings)
        open_button = QtWidgets.QPushButton('開啟 .cs16')
        open_button.clicked.connect(self.choose_file)
        settings.addWidget(open_button)
        settings.addWidget(QtWidgets.QLabel('Fs (samples/s)'))
        self.rate = QtWidgets.QDoubleSpinBox()
        self.rate.setRange(1, 100_000_000)
        self.rate.setDecimals(3)
        self.rate.setValue(self.fs)
        self.rate.setMaximumWidth(150)
        settings.addWidget(self.rate)
        settings.addWidget(QtWidgets.QLabel('FFT'))
        self.fft = QtWidgets.QComboBox()
        self.fft.addItems(['512', '1024', '2048', '4096', '8192', '16384'])
        self.fft.setCurrentText('2048')
        settings.addWidget(self.fft)
        settings.addWidget(QtWidgets.QLabel('視窗秒數'))
        self.span = QtWidgets.QDoubleSpinBox()
        self.span.setRange(0.1, 120)
        self.span.setValue(30)
        settings.addWidget(self.span)
        apply_button = QtWidgets.QPushButton('套用設定')
        apply_button.clicked.connect(self.apply_settings)
        settings.addWidget(apply_button)
        reset_button = QtWidgets.QPushButton('還原縮放')
        reset_button.clicked.connect(self.reset_zoom)
        settings.addWidget(reset_button)
        self.info = QtWidgets.QLabel('尚未載入檔案')
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        controls = QtWidgets.QHBoxLayout()
        layout.addLayout(controls)
        self.play_button = QtWidgets.QPushButton('播放')
        self.play_button.clicked.connect(self.toggle_play)
        controls.addWidget(self.play_button)
        previous = QtWidgets.QPushButton('上一頁')
        previous.clicked.connect(lambda: self.seek(self.page_start - self.page_span))
        controls.addWidget(previous)
        following = QtWidgets.QPushButton('下一頁')
        following.clicked.connect(lambda: self.seek(self.page_start + self.page_span))
        controls.addWidget(following)
        controls.addWidget(QtWidgets.QLabel('時間 (s)'))
        self.seek_time = QtWidgets.QDoubleSpinBox()
        self.seek_time.setDecimals(3)
        self.seek_time.setRange(0, 0)
        controls.addWidget(self.seek_time)
        jump = QtWidgets.QPushButton('跳轉')
        jump.clicked.connect(lambda: self.seek(self.seek_time.value()))
        controls.addWidget(jump)
        controls.addWidget(QtWidgets.QLabel('速度'))
        self.speed = QtWidgets.QComboBox()
        self.speed.addItems(['0.25', '0.5', '1', '2', '5', '10'])
        self.speed.setCurrentText('1')
        controls.addWidget(self.speed)
        self.position_label = QtWidgets.QLabel('0.000 / 0.000 s')
        controls.addWidget(self.position_label)
        controls.addStretch()
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, 100000)
        self.slider.sliderPressed.connect(self.pause)
        self.slider.sliderReleased.connect(lambda: self.seek(self.duration * self.slider.value() / 100000))
        layout.addWidget(self.slider)
        self.canvas = pg.GraphicsLayoutWidget()
        layout.addWidget(self.canvas, 1)
        self.plot = self.canvas.addPlot()
        self.plot.setLabel('bottom', 'Time from file start', units='s')
        self.plot.setLabel('left', 'Frequency offset', units='kHz')
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.image = pg.ImageItem(axisOrder='row-major')
        self.plot.addItem(self.image)
        self.hist = pg.HistogramLUTItem()
        self.hist.setImageItem(self.image)
        self.canvas.addItem(self.hist)
        colors = np.array([[0, 0, 20], [0, 30, 100], [0, 150, 180],
                           [80, 220, 100], [255, 240, 30]], dtype=np.ubyte)
        self.hist.gradient.setColorMap(pg.ColorMap(np.linspace(0, 1, len(colors)), colors))
        self.hist.setLevels(-100, -30)
        self.cursor = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen('#ffffff', width=1))
        self.plot.addItem(self.cursor)
        self.canvas.scene().sigMouseClicked.connect(self.clicked_plot)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self.page_start, self.page_span = 0.0, 30.0
        self.active_fft = 2048
        self.statusBar().showMessage('滑鼠滾輪縮放；拖曳平移；雙擊瀑布圖跳轉時間；右側調整色階。')

    @property
    def duration(self):
        return self.source.samples / self.fs if self.source else 0.0

    def choose_file(self):
        program_directory = (Path(sys.executable).resolve().parent
                             if getattr(sys, 'frozen', False)
                             else Path(__file__).resolve().parent)
        default_directory = program_directory.parent / 'IQ_Recordings'
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '開啟 CS16', str(self.source.path.parent if self.source else default_directory),
            'CS16 IQ (*.cs16);;All files (*)')
        if path:
            self.open_file(path)

    def open_file(self, path, sample_rate=None):
        self.pause()
        try:
            source = CS16File(path)
            fs = float(sample_rate if sample_rate is not None else source.metadata.get('sample_rate', 100000))
            if not math.isfinite(fs) or not 1 <= fs <= 100_000_000:
                raise ValueError('Fs 必須介於 1 與 100,000,000 samples/s。')
        except (OSError, ValueError, TypeError) as exc:
            QtWidgets.QMessageBox.warning(self, '無法開啟', str(exc))
            return
        self.source = source
        self.fs = fs
        self.rate.setValue(fs)
        self.position = 0
        origin = '命令列 Fs' if sample_rate is not None else ('JSON Fs' if source.metadata else '預設 Fs=100000，請確認')
        if source.metadata.get('finalized') is False:
            origin += ' | JSON 顯示尚未正常結束錄製：請確認錄製已停止；目前只讀開檔時長度'
        self.file_label.setText(f'{source.path}\n{origin}' + (f' | {source.warning}' if source.warning else ''))
        self.apply_settings()

    def apply_settings(self):
        if not self.source:
            return
        self.pause()
        self.fs = self.rate.value()
        self.active_fft = int(self.fft.currentText())
        self.page_span = self.span.value()
        self.seek_time.setMaximum(self.duration)
        self.page = None
        self.seek(min(self.position, self.duration), force=True)

    def pause(self):
        self.playing = False
        self.timer.stop()
        self.play_button.setText('播放')

    def toggle_play(self):
        if not self.source:
            return
        if self.playing:
            self.pause()
        else:
            if self.position >= self.duration:
                self.seek(0)
            self.playing = True
            self.last_tick = time.monotonic()
            self.play_button.setText('暫停')
            self.timer.start()

    def tick(self):
        now = time.monotonic()
        elapsed = now - self.last_tick
        self.last_tick = now
        if self.loading:
            return  # Pause the playback clock while a page is being computed.
        self.seek(self.position + elapsed * float(self.speed.currentText()))
        if self.position >= self.duration:
            self.pause()

    def seek(self, seconds, force=False):
        if not self.source:
            return
        self.position = max(0.0, min(float(seconds), self.duration))
        self.last_tick = time.monotonic()
        self.cursor.setValue(self.position)
        self.position_label.setText(f'{self.position:.3f} / {self.duration:.3f} s')
        if not self.seek_time.hasFocus():
            self.seek_time.setValue(self.position)
        self.slider.setValue(round(self.position / self.duration * 100000))
        # At EOF keep the last non-empty page.
        index = int(min(self.position, max(0, self.duration - 1 / self.fs)) // self.page_span)
        if force or index != self.page:
            self.page = index
            self.page_start = index * self.page_span
            self.request_page()

    def request_page(self):
        self.cancelled.set()
        self.pool.clear()
        self.cancelled = threading.Event()
        self.generation += 1
        self.loading = True
        self.last_result = None
        self.image.clear()
        self.info.setText('正在讀取目前時間區段並計算 FFT…')
        job = FFTJob(self.generation, (self.source, self.fs, self.active_fft,
                                     self.page_start, self.page_span), self.cancelled)
        job.signals.ready.connect(self.show_result)
        job.signals.failed.connect(self.show_error)
        self.pool.start(job)

    def show_result(self, generation, result):
        if generation != self.generation:
            return
        self.loading = False
        self.last_tick = time.monotonic()
        self.last_result = result
        values, left, width, dt = result
        self.image.setImage(values, autoLevels=False, autoHistogramRange=False)
        bin_khz = self.fs / self.active_fft / 1000
        self.image.setRect(QtCore.QRectF(left, -self.fs / 2000 - bin_khz / 2, width, self.fs / 1000))
        self.reset_zoom()
        self.info.setText(
            f'Fs={self.fs:g} samples/s | FFT={self.active_fft} | '
            f'FFT 視窗={self.active_fft / self.fs * 1000:.2f} ms | 時間步距={dt * 1000:.2f} ms | '
            f'頻率間隔={self.fs / self.active_fft:.2f} Hz | 色階：相對 dBFS/bin（非 dBm）')
        fc = self.source.metadata.get('center_frequency_hz')
        self.plot.setTitle(f'CS16 | RF center metadata: {fc} Hz' if fc is not None else 'CS16 | Frequency offset from capture center')

    def show_error(self, generation, message):
        if generation != self.generation:
            return
        self.loading = False
        self.pause()
        self.info.setText(f'無法顯示：{message}')

    def reset_zoom(self):
        if not self.source:
            return
        end = min(self.page_start + self.page_span, self.duration)
        self.plot.setXRange(self.page_start, end, padding=0)
        self.plot.setYRange(-self.fs / 2000, self.fs / 2000, padding=0)
        self.plot.getAxis('bottom').setTickSpacing(major=1 if self.page_span <= 30 else 10, minor=0.1)

    def clicked_plot(self, event):
        if event.double() and event.button() == QtCore.Qt.LeftButton and self.plot.sceneBoundingRect().contains(event.scenePos()):
            self.pause()
            self.seek(self.plot.vb.mapSceneToView(event.scenePos()).x())

    def closeEvent(self, event):
        self.pause()
        self.cancelled.set()
        self.pool.clear()
        self.pool.waitForDone()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description='Offline CS16 IQ waterfall viewer')
    parser.add_argument('file', nargs='?')
    parser.add_argument('--sample-rate', type=float)
    args = parser.parse_args()
    app = QtWidgets.QApplication([])
    for font in ('msjh.ttc', 'segoeui.ttf'):
        path = Path('C:/Windows/Fonts') / font
        if path.is_file():
            QtGui.QFontDatabase.addApplicationFont(str(path))
    app.setFont(QtGui.QFont('Microsoft JhengHei', 10))
    viewer = Viewer()
    viewer.show()
    if args.file:
        viewer.open_file(args.file, args.sample_rate)
    return app.exec_()


if __name__ == '__main__':
    raise SystemExit(main())
