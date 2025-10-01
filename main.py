import os
import sys
import logging
import subprocess
import json
import re


from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QTextEdit,
                             QFileDialog, QProgressBar, QGroupBox, QCheckBox,
                             QMessageBox, QListWidget, QSplitter, QTableWidget,
                             QTableWidgetItem, QHeaderView, QAbstractItemView,
                             QGridLayout, QComboBox)
from PyQt5.QtCore import Qt, pyqtSignal, QThread
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, USLT, TIT2, TPE1, TALB, TCON, TDRC, APIC
import eyed3
from eyed3.id3 import Tag

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AudioConverter(QThread):
    progress_updated = pyqtSignal(int)
    file_processed = pyqtSignal(str)
    finished = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, input_folder, output_folder, ffmpeg_path, ffprobe_path, embed_lrc=False, selected_files=None,
                 overwrite_lrc=False):
        super().__init__()
        self.input_folder = input_folder
        self.output_folder = output_folder
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.embed_lrc = embed_lrc
        self.selected_files = selected_files or []
        self.overwrite_lrc = overwrite_lrc
        self.files_to_process = []

    def find_audio_files(self):
        """Поиск аудиофайлов в указанной папке"""
        audio_extensions = ['.mp3', '.wav', '.flac', '.ogg', '.m4a', '.wma', '.aac']
        audio_files = []

        for root, _, files in os.walk(self.input_folder):
            for file in files:
                if any(file.lower().endswith(ext) for ext in audio_extensions):
                    audio_files.append(os.path.join(root, file))

        return audio_files

    def find_lrc_file(self, audio_path):
        """Поиск соответствующего LRC файла"""
        audio_name = os.path.splitext(audio_path)[0]
        lrc_path = audio_name + '.lrc'

        if os.path.exists(lrc_path):
            return lrc_path

        # Проверяем другие возможные расположения
        possible_lrc_names = [
            os.path.join(os.path.dirname(audio_path), os.path.basename(audio_name) + '.lrc'),
            os.path.join(self.input_folder, os.path.basename(audio_name) + '.lrc')
        ]

        for lrc_path in possible_lrc_names:
            if os.path.exists(lrc_path):
                return lrc_path

        return None

    def read_lrc_file(self, lrc_path):
        """Чтение LRC файла"""
        try:
            with open(lrc_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Ошибка чтения LRC файла {lrc_path}: {e}")
            return None

    def has_embedded_lrc(self, audio_path):
        """Проверяет, есть ли встроенный LRC в файле"""
        try:
            audio = MP3(audio_path, ID3=ID3)
            if audio.tags:
                for tag in audio.tags.values():
                    if isinstance(tag, USLT):
                        # Проверяем, содержит ли текст временные метки LRC
                        lrc_text = tag.text
                        # Простая проверка на наличие временных меток в формате [mm:ss.xx]
                        if re.search(r'\[\d{1,2}:\d{2}\.\d{2}\]', lrc_text) or re.search(r'\[\d{1,2}:\d{2}\]',
                                                                                         lrc_text):
                            return True
            return False
        except Exception as e:
            logger.error(f"Ошибка проверки встроенного LRC {audio_path}: {e}")
            return False

    def get_embedded_lrc(self, audio_path):
        """Получает встроенный LRC текст из файла"""
        try:
            audio = MP3(audio_path, ID3=ID3)
            if audio.tags:
                for tag in audio.tags.values():
                    if isinstance(tag, USLT):
                        return tag.text
            return None
        except Exception as e:
            logger.error(f"Ошибка получения встроенного LRC {audio_path}: {e}")
            return None

    def get_metadata(self, audio_path):
        """Получение метаданных из аудиофайла"""
        try:
            # Сначала пробуем через mutagen
            try:
                audio = MP3(audio_path, ID3=ID3)
                title = None
                artist = None

                if audio.tags:
                    title_tag = audio.tags.get('TIT2')
                    artist_tag = audio.tags.get('TPE1')

                    if title_tag:
                        title = str(title_tag)
                    if artist_tag:
                        artist = str(artist_tag)
            except:
                audio = None

            # Если не получилось через mutagen, пробуем через eyed3
            if not title or not artist:
                try:
                    audiofile = eyed3.load(audio_path)
                    if audiofile and audiofile.tag:
                        if not title and audiofile.tag.title:
                            title = audiofile.tag.title
                        if not artist and audiofile.tag.artist:
                            artist = audiofile.tag.artist
                except:
                    pass

            # Если все еще нет метаданных, используем имя файла
            if not title:
                title = os.path.splitext(os.path.basename(audio_path))[0]
            if not artist:
                artist = "Неизвестный исполнитель"

            return title, artist
        except Exception as e:
            logger.error(f"Ошибка чтения метаданных {audio_path}: {e}")
            return os.path.splitext(os.path.basename(audio_path))[0], "Неизвестный исполнитель"

    def convert_to_mp3(self, input_path, output_path):
        """Конвертация аудиофайла в MP3 с помощью ffmpeg"""
        try:
            cmd = [
                self.ffmpeg_path,
                '-i', input_path,
                '-codec:a', 'libmp3lame',
                '-qscale:a', '2',
                '-map_metadata', '0',
                '-id3v2_version', '3',
                '-map', '0',
                output_path,
                '-y'
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            if result.returncode == 0:
                return True
            else:
                logger.error(f"Ошибка конвертации {input_path}: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"Ошибка вызова ffmpeg для {input_path}: {e}")
            return False

    def copy_cover_art(self, source_path, target_path):
        """Копирование обложки из исходного файла в целевой"""
        try:
            # Читаем обложку из исходного файла
            source_audio = MP3(source_path, ID3=ID3)
            if source_audio.tags:
                for tag in source_audio.tags.values():
                    if isinstance(tag, APIC):
                        # Сохраняем обложку в целевой файл
                        target_audio = MP3(target_path, ID3=ID3)
                        if not target_audio.tags:
                            target_audio.add_tags()
                        target_audio.tags.add(tag)
                        target_audio.save()
                        return True
            return False
        except Exception as e:
            logger.error(f"Ошибка копирования обложки: {e}")
            return False

    def embed_lrc_into_mp3(self, mp3_path, lrc_text):
        """Встраивание LRC текста в MP3 файл"""
        try:
            audio = MP3(mp3_path, ID3=ID3)

            if not audio.tags:
                audio.add_tags()

            audio.tags.delall("USLT")
            audio.tags.add(USLT(encoding=3, lang='eng', desc='', text=lrc_text))

            audio.save()
            return True

        except Exception as e:
            logger.error(f"Ошибка встраивания LRC: {e}")
            return False

    def process_file(self, audio_path, lrc_path=None, lrc_text=None):
        """Обработка одного аудиофайла"""
        try:
            relative_path = os.path.relpath(audio_path, self.input_folder)
            output_path = os.path.join(self.output_folder,
                                       os.path.splitext(relative_path)[0] + '.mp3')

            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            if not self.convert_to_mp3(audio_path, output_path):
                return False

            # Копируем обложку
            self.copy_cover_art(audio_path, output_path)

            if self.embed_lrc and lrc_text:
                # Проверяем, есть ли уже встроенный LRC
                has_embedded = self.has_embedded_lrc(output_path)

                if has_embedded and not self.overwrite_lrc:
                    logger.info(f"LRC уже встроен в {os.path.basename(audio_path)}, пропускаем")
                else:
                    if self.embed_lrc_into_mp3(output_path, lrc_text):
                        logger.info(f"LRC встроен в {os.path.basename(audio_path)}")
                    else:
                        logger.error(f"Ошибка встраивания LRC в {os.path.basename(audio_path)}")

            return True

        except Exception as e:
            logger.error(f"Ошибка обработки файла {audio_path}: {e}")
            return False

    def run(self):
        """Основной метод выполнения конвертации"""
        try:
            all_files = self.find_audio_files()

            # Фильтруем файлы по выбранным
            if self.selected_files:
                self.files_to_process = [f for f in all_files if f in self.selected_files]
            else:
                self.files_to_process = all_files

            total_files = len(self.files_to_process)

            if total_files == 0:
                self.error_occurred.emit("Аудиофайлы не найдены!")
                return

            for i, audio_path in enumerate(self.files_to_process):
                lrc_path = self.find_lrc_file(audio_path)
                lrc_text = None

                if lrc_path:
                    lrc_text = self.read_lrc_file(lrc_path)

                success = self.process_file(audio_path, lrc_path, lrc_text)

                if success:
                    if lrc_text and self.embed_lrc:
                        self.file_processed.emit(f"✓ Обработан: {os.path.basename(audio_path)} (LRC встроен)")
                    else:
                        self.file_processed.emit(f"✓ Обработан: {os.path.basename(audio_path)}")
                else:
                    self.file_processed.emit(f"✗ Ошибка: {os.path.basename(audio_path)}")

                progress = int((i + 1) / total_files * 100)
                self.progress_updated.emit(progress)

            self.finished.emit()

        except Exception as e:
            self.error_occurred.emit(f"Критическая ошибка: {str(e)}")


class CheckBoxWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.checkbox = QCheckBox()
        layout = QHBoxLayout()
        layout.addWidget(self.checkbox)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

    def isChecked(self):
        return self.checkbox.isChecked()

    def setChecked(self, checked):
        self.checkbox.setChecked(checked)


class AudioConverterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ffmpeg_path = None
        self.ffprobe_path = None
        self.initUI()
        self.converter = None
        self.input_folder = None
        self.output_folder = None
        self.audio_files = []
        self.lrc_files = {}
        self.metadata_cache = {}
        self.checkbox_widgets = []
        self.lrc_status_cache = {}  # Кэш статуса LRC для каждого файла
        self.overwrite_lrc = False  # Глобальная настройка перезаписи LRC

    def initUI(self):
        self.setWindowTitle("AMLL Converter - by quwwerix (on tt)")
        self.setGeometry(100, 100, 1400, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)

        # Создаем grid layout для папок и настроек
        grid_layout = QGridLayout()

        # Папки
        folder_group = QGroupBox("Папки")
        folder_layout = QVBoxLayout()

        input_layout = QHBoxLayout()
        self.input_btn = QPushButton("Входная папка")
        self.input_btn.clicked.connect(self.select_input_folder)
        self.input_label = QLabel("Не выбрана")
        self.input_label.setWordWrap(True)
        input_layout.addWidget(self.input_btn)
        input_layout.addWidget(self.input_label)

        output_layout = QHBoxLayout()
        self.output_btn = QPushButton("Выходная папка")
        self.output_btn.clicked.connect(self.select_output_folder)
        self.output_label = QLabel("Не выбрана")
        self.output_label.setWordWrap(True)
        output_layout.addWidget(self.output_btn)
        output_layout.addWidget(self.output_label)

        folder_layout.addLayout(input_layout)
        folder_layout.addLayout(output_layout)
        folder_group.setLayout(folder_layout)

        # Настройки
        settings_group = QGroupBox("Настройки")
        settings_layout = QVBoxLayout()

        self.embed_lrc_check = QCheckBox("Встраивать LRC тексты")
        self.embed_lrc_check.setChecked(True)

        # Фильтр файлов
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Фильтр:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["Все файлы", "Только с LRC файлами", "Только с встроенным LRC", "Без LRC"])
        self.filter_combo.currentTextChanged.connect(self.apply_filter)
        filter_layout.addWidget(self.filter_combo)
        settings_layout.addLayout(filter_layout)

        self.select_all_btn = QPushButton("Выбрать все")
        self.select_all_btn.clicked.connect(self.toggle_select_all)
        self.select_all_btn.setEnabled(False)

        self.scan_btn = QPushButton("Сканировать файлы")
        self.scan_btn.clicked.connect(self.scan_files)
        self.scan_btn.setEnabled(False)

        settings_layout.addWidget(self.embed_lrc_check)
        settings_layout.addWidget(self.select_all_btn)
        settings_layout.addWidget(self.scan_btn)
        settings_group.setLayout(settings_layout)

        # Добавляем группы в grid
        grid_layout.addWidget(folder_group, 0, 0)
        grid_layout.addWidget(settings_group, 0, 1)
        grid_layout.setColumnStretch(0, 2)
        grid_layout.setColumnStretch(1, 1)

        # Разделитель для двух колонок
        splitter = QSplitter(Qt.Horizontal)

        self.audio_table = QTableWidget()
        self.audio_table.setColumnCount(5)
        self.audio_table.setHorizontalHeaderLabels(["", "Файл", "Трек - Исполнитель", "Размер", "Статус LRC"])
        self.audio_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.audio_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.audio_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.audio_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.audio_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.audio_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.audio_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.audio_table.setMinimumHeight(400)

        self.lrc_preview = QTextEdit()
        self.lrc_preview.setReadOnly(True)
        self.lrc_preview.setPlaceholderText("Выберите аудиофайл для просмотра LRC")
        self.lrc_preview.setMinimumHeight(400)

        splitter.addWidget(self.audio_table)
        splitter.addWidget(self.lrc_preview)
        splitter.setSizes([800, 400])

        # Кнопка запуска
        self.start_btn = QPushButton("Начать конвертацию")
        self.start_btn.clicked.connect(self.start_conversion)
        self.start_btn.setEnabled(False)

        # Прогресс бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        # Лог
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)

        # Добавляем все в основной layout
        main_layout.addLayout(grid_layout)
        main_layout.addWidget(splitter)
        main_layout.addWidget(self.start_btn)
        main_layout.addWidget(self.progress_bar)
        main_layout.addWidget(QLabel("Лог:"))
        main_layout.addWidget(self.log_text)

        self.audio_table.itemSelectionChanged.connect(self.on_audio_selected)

        # Автопоиск ffmpeg в папке с проектом
        self.auto_find_ffmpeg()

    def auto_find_ffmpeg(self):
        """Автопоиск ffmpeg в папке с проектом"""
        project_dir = os.path.dirname(os.path.abspath(__file__))

        # Проверяем разные возможные имена файлов
        possible_names = [
            ('ffmpeg.exe', 'ffprobe.exe'),
            ('ffmpeg', 'ffprobe'),
        ]

        for ffmpeg_name, ffprobe_name in possible_names:
            ffmpeg_path = os.path.join(project_dir, ffmpeg_name)
            ffprobe_path = os.path.join(project_dir, ffprobe_name)

            if os.path.exists(ffmpeg_path) and os.path.exists(ffprobe_path):
                self.ffmpeg_path = ffmpeg_path
                self.ffprobe_path = ffprobe_path
                self.log_message("✓ FFmpeg найден в папке с проектом!")
                self.scan_btn.setEnabled(True)
                return

        self.log_message(
            "❌ FFmpeg не найден в папке с проектом. Убедитесь, что ffmpeg и ffprobe находятся в той же папке, что и программа.")

    def select_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите входную папку")
        if folder:
            self.input_folder = folder
            self.input_label.setText(f"{os.path.basename(folder)}")
            self.scan_btn.setEnabled(bool(self.ffmpeg_path))

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите выходную папку")
        if folder:
            self.output_folder = folder
            self.output_label.setText(f"{os.path.basename(folder)}")

    def check_lrc_status(self, audio_path):
        """Проверяет статус LRC для файла"""
        converter = AudioConverter("", "", self.ffmpeg_path, self.ffprobe_path)

        # Проверяем встроенный LRC
        has_embedded = converter.has_embedded_lrc(audio_path)

        # Ищем внешний LRC файл
        lrc_path = converter.find_lrc_file(audio_path)
        has_external = lrc_path is not None and os.path.exists(lrc_path)

        # Определяем статус
        if has_embedded and has_external:
            return "LRC встроен + файл найден", lrc_path
        elif has_embedded:
            return "LRC встроен", None
        elif has_external:
            return "Файл LRC найден", lrc_path
        else:
            return "LRC отсутствует", None

    def scan_files(self):
        """Сканирование файлов в выбранной папке"""
        if not self.input_folder:
            QMessageBox.warning(self, "Ошибка", "Сначала выберите входную папку!")
            return

        if not self.ffmpeg_path or not self.ffprobe_path:
            QMessageBox.warning(self, "Ошибка", "FFmpeg не найден!")
            return

        self.audio_files = []
        self.lrc_files = {}
        self.metadata_cache = {}
        self.lrc_status_cache = {}
        self.checkbox_widgets = []

        # Ищем аудиофайлы
        audio_extensions = ['.mp3', '.wav', '.flac', '.ogg', '.m4a', '.wma', '.aac']
        for root, _, files in os.walk(self.input_folder):
            for file in files:
                if any(file.lower().endswith(ext) for ext in audio_extensions):
                    audio_path = os.path.join(root, file)
                    self.audio_files.append(audio_path)

                    # Получаем метаданные
                    converter = AudioConverter("", "", self.ffmpeg_path, self.ffprobe_path)
                    title, artist = converter.get_metadata(audio_path)
                    self.metadata_cache[audio_path] = (title, artist)

                    # Проверяем статус LRC
                    lrc_status, lrc_path = self.check_lrc_status(audio_path)
                    self.lrc_status_cache[audio_path] = lrc_status
                    self.lrc_files[audio_path] = lrc_path

        # Заполняем таблицу
        self.populate_table()

        self.start_btn.setEnabled(len(self.audio_files) > 0)
        self.select_all_btn.setEnabled(len(self.audio_files) > 0)
        self.log_message(f"Найдено {len(self.audio_files)} аудиофайлов")

    def populate_table(self):
        """Заполняет таблицу файлами"""
        self.audio_table.setRowCount(len(self.audio_files))
        for i, audio_path in enumerate(self.audio_files):
            # Чекбокс
            checkbox_widget = CheckBoxWidget()
            checkbox_widget.setChecked(True)
            self.audio_table.setCellWidget(i, 0, checkbox_widget)
            self.checkbox_widgets.append(checkbox_widget)

            # Название файла
            self.audio_table.setItem(i, 1, QTableWidgetItem(os.path.basename(audio_path)))

            # Трек - Исполнитель
            title, artist = self.metadata_cache.get(audio_path, ("Неизвестно", "Неизвестно"))
            track_info = f"{title} - {artist}"
            self.audio_table.setItem(i, 2, QTableWidgetItem(track_info))

            # Размер файла
            try:
                size = os.path.getsize(audio_path)
                size_str = self.format_size(size)
                self.audio_table.setItem(i, 3, QTableWidgetItem(size_str))
            except:
                self.audio_table.setItem(i, 3, QTableWidgetItem("N/A"))

            # Статус LRC
            lrc_status = self.lrc_status_cache.get(audio_path, "Неизвестно")
            item = QTableWidgetItem(lrc_status)

            if "встроен + файл найден" in lrc_status:
                item.setForeground(Qt.darkBlue)
            elif "встроен" in lrc_status:
                item.setForeground(Qt.darkGreen)
            elif "файл найден" in lrc_status:
                item.setForeground(Qt.darkMagenta)
            else:
                item.setForeground(Qt.red)

            self.audio_table.setItem(i, 4, item)

    def apply_filter(self):
        """Применяет фильтр к таблице"""
        filter_text = self.filter_combo.currentText()

        for i, audio_path in enumerate(self.audio_files):
            lrc_status = self.lrc_status_cache.get(audio_path, "")
            should_show = True

            if filter_text == "Только с LRC файлами":
                should_show = "файл найден" in lrc_status
            elif filter_text == "Только с встроенным LRC":
                should_show = "встроен" in lrc_status
            elif filter_text == "Без LRC":
                should_show = "отсутствует" in lrc_status or "Неизвестно" in lrc_status

            self.audio_table.setRowHidden(i, not should_show)

    def get_selected_files(self):
        """Получить список выбранных файлов"""
        selected_files = []
        for i, checkbox_widget in enumerate(self.checkbox_widgets):
            if checkbox_widget.isChecked() and i < len(self.audio_files) and not self.audio_table.isRowHidden(i):
                selected_files.append(self.audio_files[i])
        return selected_files

    def toggle_select_all(self):
        """Переключение выбора всех файлов"""
        if self.select_all_btn.text() == "Выбрать все":
            for i, checkbox_widget in enumerate(self.checkbox_widgets):
                if not self.audio_table.isRowHidden(i):
                    checkbox_widget.setChecked(True)
            self.select_all_btn.setText("Убрать все")
        else:
            for checkbox_widget in self.checkbox_widgets:
                checkbox_widget.setChecked(False)
            self.select_all_btn.setText("Выбрать все")

    def format_size(self, size_bytes):
        """Форматирование размера файла"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def on_audio_selected(self):
        """Обработка выбора аудиофайла"""
        selected_items = self.audio_table.selectedItems()
        if not selected_items:
            return

        row = selected_items[0].row()
        if row < len(self.audio_files):
            audio_path = self.audio_files[row]
            lrc_path = self.lrc_files.get(audio_path)

            if lrc_path and os.path.exists(lrc_path):
                try:
                    with open(lrc_path, 'r', encoding='utf-8') as f:
                        lrc_content = f.read()
                    self.lrc_preview.setPlainText(lrc_content)
                except Exception as e:
                    self.lrc_preview.setPlainText(f"Ошибка чтения LRC файла:\n{str(e)}")
            else:
                # Пробуем получить встроенный LRC
                converter = AudioConverter("", "", self.ffmpeg_path, self.ffprobe_path)
                embedded_lrc = converter.get_embedded_lrc(audio_path)
                if embedded_lrc:
                    self.lrc_preview.setPlainText(embedded_lrc)
                else:
                    self.lrc_preview.setPlainText("LRC не найден")

    def ask_overwrite_lrc(self, files_with_lrc):
        """Спрашивает о перезаписи LRC"""
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle("LRC уже встроен")
        msg.setText(f"Найдено {len(files_with_lrc)} файлов с уже встроенным LRC.\nХотите перезаписать LRC?")

        yes_all_btn = msg.addButton("Да для всех", QMessageBox.YesRole)
        yes_btn = msg.addButton("Да", QMessageBox.YesRole)
        no_btn = msg.addButton("Нет", QMessageBox.NoRole)
        cancel_btn = msg.addButton("Отмена", QMessageBox.RejectRole)

        msg.exec_()

        if msg.clickedButton() == yes_all_btn:
            return "yes_all"
        elif msg.clickedButton() == yes_btn:
            return "yes"
        elif msg.clickedButton() == no_btn:
            return "no"
        else:
            return "cancel"

    def log_message(self, message):
        self.log_text.append(message)

    def start_conversion(self):
        if not self.input_folder or not self.output_folder:
            QMessageBox.warning(self, "Ошибка", "Выберите входную и выходную папки!")
            return

        if not self.audio_files:
            QMessageBox.warning(self, "Ошибка", "Нет файлов для конвертации!")
            return

        if not self.ffmpeg_path or not self.ffprobe_path:
            QMessageBox.warning(self, "Ошибка", "FFmpeg не найден!")
            return

        selected_files = self.get_selected_files()
        if not selected_files:
            QMessageBox.warning(self, "Ошибка", "Не выбрано ни одного файла для конвертации!")
            return

        # Проверяем файлы с встроенным LRC
        files_with_embedded_lrc = []
        converter = AudioConverter("", "", self.ffmpeg_path, self.ffprobe_path)

        for file_path in selected_files:
            if converter.has_embedded_lrc(file_path) and self.lrc_files.get(file_path):
                files_with_embedded_lrc.append(file_path)

        # Спрашиваем о перезаписи, если есть файлы с встроенным LRC
        overwrite_decision = "no"
        if files_with_embedded_lrc and self.embed_lrc_check.isChecked():
            overwrite_decision = self.ask_overwrite_lrc(files_with_embedded_lrc)

            if overwrite_decision == "cancel":
                return
            elif overwrite_decision == "yes_all":
                self.overwrite_lrc = True

        self.start_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.log_text.clear()

        self.converter = AudioConverter(
            self.input_folder,
            self.output_folder,
            self.ffmpeg_path,
            self.ffprobe_path,
            self.embed_lrc_check.isChecked(),
            selected_files,
            self.overwrite_lrc or (overwrite_decision == "yes")
        )

        self.converter.progress_updated.connect(self.progress_bar.setValue)
        self.converter.file_processed.connect(self.log_message)
        self.converter.finished.connect(self.conversion_finished)
        self.converter.error_occurred.connect(self.show_error)

        self.converter.start()

    def conversion_finished(self):
        self.start_btn.setEnabled(True)
        self.log_message("Конвертация завершена!")
        QMessageBox.information(self, "Готово", "Конвертация завершена успешно!")

    def show_error(self, error_message):
        self.log_message(f"ОШИБКА: {error_message}")
        self.start_btn.setEnabled(True)
        QMessageBox.critical(self, "Ошибка", error_message)


def main():
    app = QApplication(sys.argv)
    window = AudioConverterApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()