import os
import sys
import re
import logging
import multiprocessing
import requests

from PyQt6.QtCore import QThread, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QProgressBar,
    QTextEdit,
    QWidget,
)

from legendary.models.manifest import Manifest
from legendary.models.json_manifest import JSONManifest
from legendary.downloader.mp.manager import DLManager
from legendary.models.downloading import UIUpdate

class WorkInfo:
    def __init__(self, base_url="", manifest="", download_location=""):
        self.base_url = base_url
        self.manifest = manifest
        self.download_location = download_location

class UpdateProgress:
    def __init__(self, callback):
        self.callback = callback

    def put(self, item, timeout=None):
        self.callback(item)

class DownloadThread(QThread):
    progress_signal = pyqtSignal(float, float, float, float)
    url_regex = re.compile("^https?://(?:www\\.)?[-a-zA-Z0-9@:%._\\+~#=]{1,256}\\.[a-zA-Z0-9()]{1,6}\\b(?:[-a-zA-Z0-9()@:%_\\+.~#?&=/]*)$")

    def __init__(self, url, work_info: WorkInfo):
        super().__init__()
        self.url = url
        self.work_info = work_info
        self.progress_queue = UpdateProgress(self.update_progress)
        self.manager = DLManager(
            os.path.join(self.work_info.download_location),
            self.work_info.base_url,
            os.path.join(self.work_info.download_location, ".cache"),
            self.progress_queue,
            resume_file=os.path.join(self.work_info.download_location, ".resumedata"),
        )

    def run(self):
        if self.url_regex.match(self.work_info.manifest):
            logging.info("Downloading manifest from URL...")
            try:
                resp = requests.get(self.work_info.manifest, stream=True)
                data = resp.content
            except requests.RequestException as e:
                logging.error(f"Error downloading manifest: {e}")
                return
        else:
            try:
                with open(self.work_info.manifest, "rb") as f:
                    data = f.read()
            except FileNotFoundError:
                logging.error("Manifest file not found.")
                return

        try:
            manifest = Manifest.read_all(data)
        except Exception:
            try:
                manifest = JSONManifest.read_all(data)
            except Exception as e:
                logging.error(f"Error parsing manifest: {e}")
                return

        self.manager.run_analysis(manifest, None, processing_optimization=False)

        try:
            self.manager.run()
        except SystemExit:
            pass
        finally:
            self.finished.emit()

    def update_progress(self, progress: UIUpdate):
        if progress:
            mbs = progress.download_speed / 1024 / 1024
            self.progress_signal.emit(
                progress.progress,
                mbs,
                progress.read_speed / 1024 / 1024,
                progress.write_speed / 1024 / 1024,
            )

    def kill(self):
        import signal
        os.kill(self.manager._parent_pid, signal.SIGTERM)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        self.url_label = QLabel("Base URL:")
        self.url_edit = QLineEdit("https://epicgames-download1.akamaized.net/Builds/Fortnite/CloudDir/")
        self.manifest_picker_button = QPushButton("Browse")
        self.manifest_location_label = QLabel("Manifest Location/URL:")
        self.manifest_location_edit = QLineEdit()
        self.download_location_label = QLabel("Download Location:")
        self.download_location_edit = QLineEdit()
        self.download_location_button = QPushButton("Browse")
        self.download_button = QPushButton("Download")
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_label = QLabel()
        self.speed_label = QLabel()
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.download_thread = None

        self.manifest_picker_button.clicked.connect(self.select_manifest)
        self.download_location_button.clicked.connect(self.browse_download_location)
        self.download_button.clicked.connect(self.download_file)

        input_layout = QVBoxLayout()
        input_layout.addWidget(self.url_label)
        input_layout.addWidget(self.url_edit)

        input_layout.addWidget(self.manifest_location_label)
        manifest_layout = QHBoxLayout()
        manifest_layout.addWidget(self.manifest_location_edit)
        manifest_layout.addWidget(self.manifest_picker_button)
        input_layout.addLayout(manifest_layout)

        input_layout.addWidget(self.download_location_label)
        download_layout = QHBoxLayout()
        download_layout.addWidget(self.download_location_edit)
        download_layout.addWidget(self.download_location_button)
        input_layout.addLayout(download_layout)

        input_layout.addWidget(self.download_button)

        progress_layout = QVBoxLayout()
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.speed_label)
    
        console_layout = QVBoxLayout()
        console_layout.addWidget(QLabel("Console Output:"))
        console_layout.addWidget(self.console)

        main_layout = QVBoxLayout()
        main_layout.addLayout(input_layout)
        main_layout.addLayout(progress_layout)
        main_layout.addLayout(console_layout)

        central_widget = QWidget()
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        self.setup_logging()
        self.setWindowTitle("Epic Manifest Downloader")

        self.resize(500, 250)

        self.show()


    def select_manifest(self):
        manifest_path, _ = QFileDialog.getOpenFileName(
            self, "Select Manifest", filter="Manifest File (*.manifest)"
        )
        self.manifest_location_edit.setText(manifest_path)
        logging.getLogger().info(f"Selected manifest: {manifest_path}")


    def browse_download_location(self):
        download_dir = QFileDialog.getExistingDirectory(
            self, "Choose Download Directory"
        )
        self.download_location_edit.setText(download_dir)

    def download_file(self):
        url = self.url_edit.text()
        manifest_path = self.manifest_location_edit.text()
        dest_dir = self.download_location_edit.text()

        work_info = WorkInfo(url, manifest_path, dest_dir)

        self.download_thread = DownloadThread(url, work_info)
        self.download_thread.progress_signal.connect(self.update_progress)
        self.download_thread.finished.connect(self.download_finished)
        self.download_thread.start()
        self.download_button.setEnabled(False)

    def update_progress(self, progress_percent, speed, read_speed, write_speed):
        self.progress_bar.setValue(int(progress_percent))
        self.speed_label.setText(f"Download {speed:.2f} MB/s")
        self.progress_label.setText(
            f"R/W {read_speed:.2f} MB/s, {write_speed:.2f} MB/s"
        )

    def download_finished(self):
        self.download_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Download Finished")
        self.speed_label.setText("")
        self.download_thread.manager.running = False
        self.download_thread.kill()

    def write_to_console(self, text: str):
        text = text[:-1] if text.endswith("\n") else text
        self.console.append(text)

    def setup_logging(self):
        stream = LoggerStream()
        stream.newText.connect(self.write_to_console)

        logging.basicConfig(level=logging.INFO)
        dlm = logging.getLogger("DLM")
        dlm.setLevel(logging.INFO)

        logging.getLogger().addHandler(logging.StreamHandler(stream))

    def closeEvent(self, event):
        if self.download_thread:
            self.download_thread.manager.running = False
            self.download_thread.kill()
        super().closeEvent(event)

class LoggerStream(QObject):
    newText = pyqtSignal(str)

    def write(self, text):
        self.newText.emit(str(text))

    def flush(self):
        pass

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = QApplication([])
    window = MainWindow()
    sys.exit(app.exec())
