import sys
import pygame
import os
import time
import json
import threading
import cloudinary
import cloudinary.api
import cloudinary.uploader
import requests
import cv2
import numpy as np
import random
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                            QPushButton, QLabel, QFileDialog, QSlider, QComboBox, 
                            QLineEdit, QColorDialog, QMessageBox, QGroupBox, QFormLayout, 
                            QTabWidget, QFrame)
from PyQt5.QtCore import (Qt, QTimer, QUrl, QSize, pyqtSignal, QThread, QDir, 
                         QFileSystemWatcher, QPropertyAnimation, QEasingCurve, 
                         QRect, QAbstractAnimation, QParallelAnimationGroup)
from PyQt5.QtGui import (QPixmap, QImage, QPalette, QColor, QFont, QIcon, QPainter)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent, QMediaPlaylist
import qrcode
from PIL import Image, ImageEnhance, ImageFilter
from datetime import datetime
from io import BytesIO

class CloudinaryMonitor(QThread):
    new_media_signal = pyqtSignal(str, str)  # file_path, media_type ('image' or 'video')
    
    def __init__(self, cloud_name, api_key, api_secret, parent=None):
        super().__init__(parent)
        self.cloud_name = cloud_name
        self.api_key = api_key
        self.api_secret = api_secret
        self.running = True
        self.known_media = set()
        self.last_checked = None
        self.image_formats = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']
        self.video_formats = ['mp4', 'webm', 'mov', 'avi', 'mkv', 'wmv', 'flv']
        self.error_count = 0
        self.max_errors = 5
        
        # Configure Cloudinary
        try:
            cloudinary.config(
                cloud_name=self.cloud_name,
                api_key=self.api_key,
                api_secret=self.api_secret
            )
        except Exception as e:
            print(f"Error configuring Cloudinary: {e}")
    
    def run(self):
        self.error_count = 0
        while self.running:
            try:
                if not os.path.exists(self.parent().download_folder):
                    os.makedirs(self.parent().download_folder)
                
                # Search for new media from Cloudinary
                query = {}
                if self.last_checked:
                    query['start_at'] = self.last_checked.isoformat()
                    
                result = cloudinary.Search().expression("resource_type:image OR resource_type:video").sort_by("created_at", "desc").max_results(100).execute()
                
                if result and 'resources' in result:
                    for resource in result['resources']:
                        try:
                            # Update last checked time
                            created_at = datetime.fromisoformat(resource['created_at'].replace('Z', '+00:00'))
                            if self.last_checked is None or created_at > self.last_checked:
                                self.last_checked = created_at
                            
                            # Check if it's a supported format
                            format_extension = resource.get('format', '').lower()
                            if not format_extension:
                                continue
                                
                            if format_extension in self.image_formats:
                                media_type = 'image'
                            elif format_extension in self.video_formats:
                                media_type = 'video'
                            else:
                                continue  # Skip unsupported formats
                            
                            # Download the media
                            url = resource['secure_url']
                            filename = os.path.basename(url)
                            if '?' in filename:
                                filename = filename.split('?')[0]
                            
                            download_path = os.path.join(self.parent().download_folder, filename)
                            
                            # Skip if file already exists or is in known media
                            resource_id = resource.get('public_id', '')
                            if os.path.exists(download_path) or resource_id in self.known_media:
                                continue
                                
                            self.known_media.add(resource_id)
                                
                            # Download the file
                            response = requests.get(url, stream=True, timeout=60)
                            if response.status_code == 200:
                                with open(download_path, 'wb') as f:
                                    for chunk in response.iter_content(1024):
                                        if not self.running:
                                            break
                                        f.write(chunk)
                                
                                # Verify file was downloaded completely
                                if os.path.exists(download_path) and os.path.getsize(download_path) > 0:
                                    # Emit signal with file path and media type
                                    self.new_media_signal.emit(download_path, media_type)
                                else:
                                    print(f"Warning: Downloaded file is empty: {download_path}")
                        except Exception as e:
                            print(f"Error processing resource: {e}")
                            continue
                
                # Reset error counter on successful run
                self.error_count = 0
                # Sleep between checks
                for i in range(30):  # Check every 30 seconds
                    if not self.running:
                        break
                    time.sleep(1)
                
            except Exception as e:
                print(f"Error checking Cloudinary: {e}")
                self.error_count += 1
                
                # If too many consecutive errors, slow down the checks
                if self.error_count >= self.max_errors:
                    print("Too many errors, slowing down Cloudinary checks")
                    time.sleep(300)  # 5 minutes
                else:
                    time.sleep(60)  # 1 minute
    
    def stop(self):
        self.running = False

class MediaDownloader(QThread):
    download_complete = pyqtSignal(str, str)  # url, local path
    
    def __init__(self, url, local_path, parent=None):
        super().__init__(parent)
        self.url = url
        self.local_path = local_path
    
    def run(self):
        try:
            response = requests.get(self.url, stream=True)
            if response.status_code == 200:
                with open(self.local_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=1024):
                        f.write(chunk)
                self.download_complete.emit(self.url, self.local_path)
        except Exception as e:
            print(f"Error downloading media {self.url}: {e}")
            
class VideoThread(QThread):
    update_frame = pyqtSignal(QImage)
    playback_completed = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.video_path = None
        self.running = False
        self.cap = None
        self.max_duration = 600  # 10 minutes in seconds
        self.enable_audio = False
        
        # Initialize QMediaPlayer for audio
        self.media_player = QMediaPlayer()
        self.media_player.setNotifyInterval(1000)
        self.media_player.setVolume(70)
    
    def set_video(self, video_path):
        self.video_path = video_path
    
    def run(self):
        if not self.video_path or not os.path.exists(self.video_path):
            print(f"Error: Video file does not exist: {self.video_path}")
            self.playback_completed.emit()
            return
        
        self.running = True
        
        # Start audio playback if enabled
        if self.enable_audio:
            try:
                self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(self.video_path)))
                self.media_player.play()
            except Exception as e:
                print(f"Error starting audio playback: {e}")
        
        try:
            self.cap = cv2.VideoCapture(self.video_path)
            
            if not self.cap.isOpened():
                print(f"Error: Unable to open video {self.video_path}")
                self.playback_completed.emit()
                return
                
            # Get the video's frame rate, default to 30fps if can't be determined
            fps = self.cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                fps = 30
                
            # Get video duration in seconds
            total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
            video_duration = total_frames / fps if total_frames > 0 else 0
            
            # Calculate frame delay
            frame_delay = 1.0 / fps
            
            # Initialize frame counter
            frame_count = 0
            start_time = time.time()
            
            while self.running:
                ret, frame = self.cap.read()
                if not ret:
                    # Video ended, break the loop
                    break
                
                try:
                    # Convert frame from BGR to RGB format
                    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    
                    # Convert frame to QImage
                    h, w, ch = rgb_frame.shape
                    bytes_per_line = ch * w
                    qt_image = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
                    
                    # Emit the frame
                    if not qt_image.isNull():
                        self.update_frame.emit(qt_image)
                    
                    # Increment frame counter
                    frame_count += 1
                    
                    # Check if we've reached the time limit for long videos
                    elapsed_time = time.time() - start_time
                    if video_duration > self.max_duration and elapsed_time >= self.max_duration:
                        print(f"Reached {self.max_duration} second limit for video: {self.video_path}")
                        break
                    
                    # Delay to maintain proper playback speed
                    time.sleep(frame_delay)
                except Exception as e:
                    print(f"Error processing video frame: {e}")
                    # Don't break the loop for a single frame error
                    continue
                    
        except Exception as e:
            print(f"Error in video thread: {e}")
        finally:
            # Ensure proper cleanup
            try:
                if self.cap:
                    self.cap.release()
            except:
                pass
                
            try:
                if self.media_player:
                    self.media_player.stop()
            except:
                pass
                
            self.playback_completed.emit()
            self.running = False
    
    def stop(self):
        self.running = False
        # Stop the media player for audio
        try:
            if self.media_player:
                self.media_player.stop()
        except:
            pass
        
        # Give thread time to clean up resources before waiting
        time.sleep(0.2)
        self.wait(1000)  # Wait up to 1 second for thread to complete
        
        
class SlideshowWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_media_index = 0
        self.media_files = []  # List of tuples (path, type)
        self.overlay_image = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.next_media)
        self.display_duration = 5000  # milliseconds (default for images)
        self.transition_style = "fade"  # fade, slide, zoom, crossfade, ken burns
        self.background_color = QColor(0, 0, 0)
        self.background_image = None
        self.vertical_margin = 50 
        # QR Code settings
        self.qr_code_enabled = True
        self.qr_code_data = "https://example.com"
        self.qr_code_size = 150
        self.qr_code_position = "bottom-right"
        self.background_music_was_playing = False
        
        # Video processing components
        self.video_thread = VideoThread(self)
        self.video_thread.update_frame.connect(self.update_video_frame)
        self.video_thread.playback_completed.connect(self.handle_video_completed)
        
        # Set black background
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.Window, self.background_color)
        self.setPalette(palette)
        
        # Image display label
        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: transparent;")
        
        # Video display label
        self.video_label = QLabel(self)
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background-color: transparent;")
        self.video_label.hide()
        
        # QR code label
        self.qr_label = QLabel(self)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setStyleSheet("background-color: rgba(255, 255, 255, 200);")
        
        # Generate initial QR code
        self.generate_qr_code()
        
        # Layout setup
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.image_label)
        self.layout.addWidget(self.video_label)

    def play_video(self, video_path):
        # Hide image and show video label
        self.image_label.hide()
        self.video_label.show()
        
        # Stop any currently playing video
        if self.video_thread.isRunning():
            self.video_thread.stop()
            # Wait for thread to fully stop
            self.video_thread.wait(500)  # Give it 500ms to clean up
        
        # Setup and start video thread with audio DISABLED
        self.video_thread.set_video(video_path)
        self.video_thread.enable_audio = False  # Always false to play silently
        self.video_thread.start()
        
        # Position QR code
        self.position_qr_code()

    def handle_video_completed(self):
        # Don't call next_media() directly as it bypasses transitions
        # The timer will handle the transition to the next media item
        # Only reset the timer if it's not already active
        if not self.timer.isActive():
            self.timer.start(self.display_duration)

    def clear_transition_labels(self):
        """
        Utility method to clean up any hanging transition labels
        Call this when having transition issues
        """
        for child in self.findChildren(QLabel):
            # Skip the main image and video labels
            if child != self.image_label and child != self.video_label and child != self.qr_label:
                try:
                    child.deleteLater()
                except:
                    pass

    def add_media(self, media_path, media_type):
        if media_path not in [m[0] for m in self.media_files]:
            self.media_files.append((media_path, media_type))
            if len(self.media_files) == 1:
                self.start_slideshow()
            elif not self.timer.isActive():
                self.start_slideshow()
    
    def start_slideshow(self):
        if self.media_files:
            # Clean up any existing transition elements
            self.clear_transition_labels()
            
            self.current_media_index = 0
            self.show_current_media()
            self.timer.start(self.display_duration)
    
    def stop_slideshow(self):
        self.timer.stop()
        if hasattr(self, 'video_thread'):
            self.video_thread.stop()
    
    def next_media(self):
        if self.media_files:
            # Stop any currently playing video
            if self.video_thread.isRunning():
                self.video_thread.stop()
                
            self.current_media_index = (self.current_media_index + 1) % len(self.media_files)
            self.show_current_media()
    
    def show_current_media(self):
        if not self.media_files:
            return
            
        try:
            media_path, media_type = self.media_files[self.current_media_index]
            
            # Verify file exists
            if not os.path.exists(media_path):
                print(f"Warning: File does not exist: {media_path}")
                self.media_files.pop(self.current_media_index)
                if not self.media_files:
                    return
                self.current_media_index = self.current_media_index % len(self.media_files)
                media_path, media_type = self.media_files[self.current_media_index]
            
            # Special handling for crossfade and Ken Burns transitions with images
            if media_type == 'image':
                if self.transition_style == "crossfade" and self.image_label.isVisible():
                    if self.perform_crossfade_transition(media_path):
                        # Set timer for next transition
                        self.timer.setInterval(self.display_duration)
                        # Position QR code
                        self.position_qr_code()
                        return
                elif self.transition_style == "ken burns":
                    if self.perform_ken_burns_transition(media_path):
                        # Set timer for next transition
                        self.timer.setInterval(self.display_duration)
                        # Position QR code
                        self.position_qr_code()
                        return
            
            # Store the current labels for transitions
            old_image_label = None
            if hasattr(self, 'temp_image_label'):
                old_image_label = self.temp_image_label
            else:
                # Create a temporary label for transitions
                self.temp_image_label = QLabel(self)
                self.temp_image_label.setAlignment(Qt.AlignCenter)
                self.temp_image_label.setStyleSheet("background-color: transparent;")
                self.temp_image_label.hide()
            
            # Set up the new content
            if media_type == 'image':
                self.display_image(media_path)
                self.timer.setInterval(self.display_duration)
            elif media_type == 'video':
                self.play_video(media_path)
                
                # Get video duration to set timer interval
                try:
                    cap = cv2.VideoCapture(media_path)
                    if not cap.isOpened():
                        print(f"Error: Could not open video file: {media_path}")
                        # Skip to next media
                        self.next_media()
                        return
                        
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    duration = total_frames / fps if fps > 0 else 0
                    cap.release()
                    
                    # If video is longer than 10 minutes, limit to 10 minutes (600000 ms)
                    # Otherwise play full video
                    if duration > 600:
                        self.timer.setInterval(600000)  # 10 minutes in milliseconds
                    else:
                        # Convert duration to milliseconds and add a small buffer (500ms)
                        self.timer.setInterval(int(duration * 1000) + 500)
                except Exception as e:
                    print(f"Error getting video duration: {e}")
                    self.timer.setInterval(600000)  # Default to 10 minutes if error occurs
            
            # Apply transition if we have an old image and not using crossfade or ken burns
            if old_image_label and self.transition_style != "none" and self.transition_style not in ["crossfade", "ken burns"]:
                self.apply_transition(old_image_label)
                
        except Exception as e:
            print(f"Error in show_current_media: {e}")
            # Attempt to recover by moving to next media
            self.next_media()
    
    def apply_transition(self, old_label):
        # Get the current content label (which is going to be shown)
        current_content = self.image_label if self.image_label.isVisible() else self.video_label
        
        # Create a snapshot of the old content to use during transition
        old_pixmap = old_label.pixmap() if old_label.pixmap() else None
        if not old_pixmap:
            # If no pixmap, just remove the old label and skip transition
            old_label.deleteLater()
            return
        
        # Create a dedicated transition label that will be used for animation
        transition_label = QLabel(self)
        transition_label.setAlignment(Qt.AlignCenter)
        transition_label.setStyleSheet("background-color: transparent;")
        transition_label.setPixmap(old_pixmap)
        transition_label.setGeometry(old_label.geometry())
        transition_label.show()
        transition_label.raise_()
        
        # We no longer need the old label
        old_label.hide()
        old_label.deleteLater()
        
        # Set the initial state of current content based on transition type
        if self.transition_style == "fade":
            # For fade, start with current content transparent
            current_content.setWindowOpacity(0.0)
            current_content.show()
            current_content.raise_()
            
        elif self.transition_style == "slide":
            # For slide, position current content off-screen to the right
            current_content.setGeometry(self.width(), 
                                       self.vertical_margin, 
                                       self.width(), 
                                       self.height() - (2 * self.vertical_margin))
            current_content.show()
            
        elif self.transition_style == "zoom":
            # For zoom, show current content and raise transition label above it
            current_content.show()
            transition_label.raise_()
            
        # Animation group to synchronize multiple animations if needed
        animations = []
        
        if self.transition_style == "fade":
            # Fade out animation for transition label
            fade_out = QPropertyAnimation(transition_label, b"windowOpacity")
            fade_out.setDuration(800)  # Slightly longer for smoother effect
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.setEasingCurve(QEasingCurve.InOutCubic)  # Smoother curve
            animations.append(fade_out)
            
            # Fade in animation for current content
            fade_in = QPropertyAnimation(current_content, b"windowOpacity")
            fade_in.setDuration(800)
            fade_in.setStartValue(0.0)
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.InOutCubic)
            animations.append(fade_in)
            
        elif self.transition_style == "slide":
            # Calculate proper dimensions
            content_width = self.width()
            if self.qr_code_enabled:
                content_width = int(self.width() * 0.75)
                
            # Slide out animation for transition label (to left)
            slide_out = QPropertyAnimation(transition_label, b"geometry")
            slide_out.setDuration(600)
            start_rect = transition_label.geometry()
            end_rect = QRect(-start_rect.width(), 
                             start_rect.y(), 
                             start_rect.width(), 
                             start_rect.height())
            slide_out.setStartValue(start_rect)
            slide_out.setEndValue(end_rect)
            slide_out.setEasingCurve(QEasingCurve.InOutQuad)
            animations.append(slide_out)
            
            # Slide in animation for current content (from right)
            slide_in = QPropertyAnimation(current_content, b"geometry")
            slide_in.setDuration(600)
            slide_in.setStartValue(QRect(self.width(), 
                                         self.vertical_margin, 
                                         content_width, 
                                         self.height() - (2 * self.vertical_margin)))
            slide_in.setEndValue(QRect(0, 
                                      self.vertical_margin, 
                                      content_width, 
                                      self.height() - (2 * self.vertical_margin)))
            slide_in.setEasingCurve(QEasingCurve.InOutQuad)
            animations.append(slide_in)
            
        elif self.transition_style == "zoom":
            # Zoom out and fade out for transition label
            zoom_out = QPropertyAnimation(transition_label, b"geometry")
            zoom_out.setDuration(700)
            start_rect = transition_label.geometry()
            center_x = start_rect.x() + start_rect.width() // 2
            center_y = start_rect.y() + start_rect.height() // 2
            end_rect = QRect(
                center_x - start_rect.width() // 4,
                center_y - start_rect.height() // 4,
                start_rect.width() // 2,
                start_rect.height() // 2
            )
            zoom_out.setStartValue(start_rect)
            zoom_out.setEndValue(end_rect)
            zoom_out.setEasingCurve(QEasingCurve.OutQuart)  # More dramatic curve
            animations.append(zoom_out)
            
            # Add fade effect to zoom
            fade_out = QPropertyAnimation(transition_label, b"windowOpacity")
            fade_out.setDuration(700)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.setEasingCurve(QEasingCurve.OutQuart)
            animations.append(fade_out)
        
        # Use a timer to clean up the transition label after animations complete
        def cleanup_transition():
            try:
                if transition_label and not transition_label.isNull():
                    transition_label.deleteLater()
            except:
                pass
        
        # Start all animations
        for anim in animations:
            anim.start()
        
        # Set a timer to clean up transition_label after animations finish
        QTimer.singleShot(max(anim.duration() for anim in animations) + 100, cleanup_transition)

    def perform_crossfade_transition(self, new_image_path):
        """
        Performs a beautiful crossfade transition between the current image and new image
        This works only for images, not videos
        """
        # Only proceed if we have an image currently displayed
        if not self.image_label.pixmap() or self.image_label.pixmap().isNull():
            return False
            
        # Create a snapshot of the current image
        old_pixmap = self.image_label.pixmap()
        
        # Load the new image
        new_image = QImage(new_image_path)
        if new_image.isNull():
            return False
            
        # Calculate display size
        display_width = self.width()
        display_height = self.height() - (2 * self.vertical_margin)
        
        if self.qr_code_enabled:
            display_width = int(self.width() * 0.75)
        
        # Scale both images to the same size
        scaled_new_pixmap = QPixmap.fromImage(new_image).scaled(
            display_width, display_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        
        # Create a semi-transparent overlay label
        overlay_label = QLabel(self)
        overlay_label.setGeometry(
            0, self.vertical_margin,
            display_width, display_height
        )
        overlay_label.setAlignment(Qt.AlignCenter)
        overlay_label.setStyleSheet("background-color: transparent;")
        overlay_label.setPixmap(scaled_new_pixmap)
        overlay_label.setWindowOpacity(0.0)
        overlay_label.show()
        overlay_label.raise_()
        
        # Crossfade animation
        fade_in = QPropertyAnimation(overlay_label, b"windowOpacity")
        fade_in.setDuration(1200)  # Slightly longer for a smoother effect
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.InOutCubic)
        
        # When animation finishes, update the actual image label
        def finish_transition():
            self.image_label.setPixmap(scaled_new_pixmap)
            overlay_label.deleteLater()
        
        fade_in.finished.connect(finish_transition)
        fade_in.start()
        
        return True

    def perform_ken_burns_transition(self, new_image_path):
        """
        Performs a Ken Burns effect transition (subtle zoom and pan) for images
        """
        # Load the new image
        new_image = QImage(new_image_path)
        if new_image.isNull():
            return False
            
        # Calculate base display size
        display_width = self.width()
        display_height = self.height() - (2 * self.vertical_margin)
        
        if self.qr_code_enabled:
            display_width = int(self.width() * 0.75)
        
        # Create a slightly larger image for zoom effect (110%)
        zoom_factor = 1.1
        large_width = int(display_width * zoom_factor)
        large_height = int(display_height * zoom_factor)
        
        # Scale the new image larger to allow for zoom and pan
        scaled_new_pixmap = QPixmap.fromImage(new_image).scaled(
            large_width, large_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        
        # Determine random direction for Ken Burns effect
        # This randomly selects if we zoom in or out and which direction we pan
        zoom_in = random.choice([True, False])
        directions = ["top-left", "top-right", "bottom-left", "bottom-right"]
        direction = random.choice(directions)
        
        # Calculate start and end positions based on direction and zoom
        start_x = 0
        start_y = 0
        end_x = 0
        end_y = 0
        
        # Calculate the margin that exists due to aspect ratio scaling
        x_margin = max(0, scaled_new_pixmap.width() - display_width)
        y_margin = max(0, scaled_new_pixmap.height() - display_height)
        
        if zoom_in:
            # Start zoomed out, end zoomed in
            # Start positions
            if "left" in direction:
                start_x = 0
            else:  # right
                start_x = min(x_margin, scaled_new_pixmap.width() - display_width)
                
            if "top" in direction:
                start_y = 0
            else:  # bottom
                start_y = min(y_margin, scaled_new_pixmap.height() - display_height)
                
            # End positions - move in opposite direction
            if "left" in direction:
                end_x = min(x_margin, scaled_new_pixmap.width() - display_width)
            else:  # right
                end_x = 0
                
            if "top" in direction:
                end_y = min(y_margin, scaled_new_pixmap.height() - display_height)
            else:  # bottom
                end_y = 0
        else:
            # Start zoomed in, end zoomed out
            # Start positions
            if "left" in direction:
                start_x = min(x_margin, scaled_new_pixmap.width() - display_width)
            else:  # right
                start_x = 0
                
            if "top" in direction:
                start_y = min(y_margin, scaled_new_pixmap.height() - display_height)
            else:  # bottom
                start_y = 0
                
            # End positions - move in opposite direction
            if "left" in direction:
                end_x = 0
            else:  # right
                end_x = min(x_margin, scaled_new_pixmap.width() - display_width)
                
            if "top" in direction:
                end_y = 0
            else:  # bottom
                end_y = min(y_margin, scaled_new_pixmap.height() - display_height)
        
        # Create a label for the Ken Burns effect
        ken_burns_label = QLabel(self)
        ken_burns_label.setGeometry(0, self.vertical_margin, display_width, display_height)
        ken_burns_label.setStyleSheet("background-color: transparent;")
        
        # Function to update the position of the image during animation
        def update_ken_burns_position(progress):
            # Calculate current position
            current_x = start_x + (end_x - start_x) * progress
            current_y = start_y + (end_y - start_y) * progress
            
            # Create a sub-pixmap from the larger image
            ken_burns_pixmap = scaled_new_pixmap.copy(
                int(current_x), int(current_y), 
                min(display_width, scaled_new_pixmap.width()), 
                min(display_height, scaled_new_pixmap.height())
            )
            
            # Apply overlay if needed
            if self.overlay_image:
                painter = QPainter(ken_burns_pixmap)
                overlay = QPixmap(self.overlay_image)
                overlay_scaled = overlay.scaled(
                    ken_burns_pixmap.width(), ken_burns_pixmap.height(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                painter.setOpacity(0.3)
                painter.drawPixmap(
                    (ken_burns_pixmap.width() - overlay_scaled.width()) // 2,
                    (ken_burns_pixmap.height() - overlay_scaled.height()) // 2,
                    overlay_scaled
                )
                painter.end()
            
            ken_burns_label.setPixmap(ken_burns_pixmap)
        
        # Hide the old image label and show our ken burns label
        self.image_label.hide()
        ken_burns_label.show()
        ken_burns_label.raise_()
        
        # Animation duration should be almost as long as the display duration
        duration = min(self.display_duration - 500, 4000)  # Max 4 seconds or less than display time
        
        # Animation to drive the effect
        ken_burns_anim = QPropertyAnimation(self, b"windowOpacity")  # We're just using the animation framework
        ken_burns_anim.setStartValue(0.0)
        ken_burns_anim.setEndValue(1.0)
        ken_burns_anim.setDuration(duration)
        ken_burns_anim.setEasingCurve(QEasingCurve.InOutSine)
        
        # Connect value changed to our update function
        ken_burns_anim.valueChanged.connect(update_ken_burns_position)
        
        # When animation finishes, update the actual image label and clean up
        def finish_ken_burns():
            # Get the final frame
            final_pixmap = ken_burns_label.pixmap()
            if final_pixmap and not final_pixmap.isNull():
                self.image_label.setPixmap(final_pixmap)
            
            self.image_label.show()
            ken_burns_label.deleteLater()
        
        ken_burns_anim.finished.connect(finish_ken_burns)
        
        # Start the animation
        update_ken_burns_position(0)  # Initialize with starting position
        ken_burns_anim.start()
        
        return True

    def apply_overlay_to_current_image(self):
        """Apply overlay to the current image after transition completes"""
        if not self.overlay_image or not self.image_label.pixmap():
            return
            
        pixmap = self.image_label.pixmap()
        overlay = QPixmap(self.overlay_image)
        
        # Create a new pixmap for drawing
        result = QPixmap(pixmap.size())
        result.fill(Qt.transparent)
        
        painter = QPainter(result)
        # First draw the original image
        painter.drawPixmap(0, 0, pixmap)
        
        # Scale overlay to match original
        overlay_scaled = overlay.scaled(
            pixmap.width(), pixmap.height(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        
        # Draw overlay with transparency
        painter.setOpacity(0.3)
        painter.drawPixmap(
            (pixmap.width() - overlay_scaled.width()) // 2,
            (pixmap.height() - overlay_scaled.height()) // 2,
            overlay_scaled
        )
        painter.end()
        
        # Update the image label with the overlaid image
        self.image_label.setPixmap(result)

    
    def update_video_frame(self, frame):
        if frame.isNull():
            return
            
        # Scale the frame to fit the display area
        display_width = self.width()
        display_height = self.height() - (2 * self.vertical_margin)
        
        if self.qr_code_enabled:
            display_width = int(self.width() * 0.75)
        
        scaled_frame = frame.scaled(
            display_width, display_height, 
            Qt.KeepAspectRatio, 
            Qt.SmoothTransformation
        )
        
        # Convert QImage to QPixmap and display it
        pixmap = QPixmap.fromImage(scaled_frame)
        self.video_label.setPixmap(pixmap)
        self.video_label.setGeometry(
            0, self.vertical_margin, 
            display_width, display_height
        )
    
    def display_image(self, image_path):
        # Hide video and show image widget
        self.video_label.hide()
        self.image_label.show()
        
        # Stop any currently playing video
        if self.video_thread.isRunning():
            self.video_thread.stop()
        
        try:
            # Load and display the image
            image = QImage(image_path)
            if image.isNull():
                print(f"Error loading image: {image_path}")
                return
                
            pixmap = QPixmap.fromImage(image)
            
            display_width = self.width()
            display_height = self.height() - (2 * self.vertical_margin)
            
            if self.qr_code_enabled:
                display_width = int(self.width() * 0.75)
            
            scaled_pixmap = pixmap.scaled(
                display_width, display_height, 
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            )
            
            # Apply overlay if set
            if self.overlay_image:
                painter = QPainter(scaled_pixmap)
                overlay = QPixmap(self.overlay_image)
                overlay_scaled = overlay.scaled(
                    scaled_pixmap.width(), scaled_pixmap.height(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                painter.setOpacity(0.3)
                painter.drawPixmap(
                    (scaled_pixmap.width() - overlay_scaled.width()) // 2,
                    (scaled_pixmap.height() - overlay_scaled.height()) // 2,
                    overlay_scaled
                )
                painter.end()
            
            self.image_label.setPixmap(scaled_pixmap)
            self.image_label.setGeometry(
                0, self.vertical_margin, 
                display_width, display_height
            )
            
            # Position QR code
            self.position_qr_code()
            
        except Exception as e:
            print(f"Error displaying image: {e}")
    
    def set_background_image(self, image_path):
        self.background_image = image_path
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        
        if self.background_image:
            bg_pixmap = QPixmap(self.background_image)
            if not bg_pixmap.isNull():
                painter.drawPixmap(self.rect(), bg_pixmap, bg_pixmap.rect())
        else:
            painter.fillRect(self.rect(), self.background_color)
        
        super().paintEvent(event)
    
    def set_display_duration(self, duration_ms):
        self.display_duration = duration_ms
        if self.timer.isActive():
            self.timer.setInterval(duration_ms)
    
    def set_transition_style(self, style):
        self.transition_style = style
    
    def set_background_color(self, color):
        self.background_color = color
        palette = self.palette()
        palette.setColor(QPalette.Window, color)
        self.setPalette(palette)
    
    def set_overlay_image(self, image_path):
        self.overlay_image = image_path
        if self.media_files:
            self.show_current_media()
    
    def set_qr_code_data(self, data):
        self.qr_code_data = data
        self.generate_qr_code()
    
    def set_qr_code_enabled(self, enabled):
        self.qr_code_enabled = enabled
        if self.media_files:
            self.show_current_media()
    
    def set_qr_code_position(self, position):
        self.qr_code_position = position
        if self.media_files:
            self.show_current_media()
    
    def set_qr_code_size(self, size):
        self.qr_code_size = size
        self.generate_qr_code()
        if self.media_files:
            self.show_current_media()
    
    def generate_qr_code(self):
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(self.qr_code_data)
            qr.make(fit=True)
            
            qr_img = qr.make_image(fill_color="black", back_color="white")
            
            # Use a BytesIO buffer instead of temporary file
            buffer = BytesIO()
            qr_img.save(buffer, format="PNG")
            buffer.seek(0)
            
            pixmap = QPixmap()
            pixmap.loadFromData(buffer.read())
            
            self.qr_label.setPixmap(pixmap.scaled(
                self.qr_code_size, self.qr_code_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            ))
            
        except Exception as e:
            print(f"Error generating QR code: {e}")
    
    def position_qr_code(self):
        if not self.qr_code_enabled:
            self.qr_label.hide()
            return
            
        try:
            # Calculate position based on the current window size
            qr_x = self.width() - self.qr_code_size - 20  # Default bottom-right
            qr_y = self.height() - self.qr_code_size - 20
            
            if self.qr_code_position == "top-right":
                qr_y = 20
            elif self.qr_code_position == "top-left":
                qr_x = 20
                qr_y = 20
            elif self.qr_code_position == "bottom-left":
                qr_x = 20
            
            # Make sure QR code stays within window bounds
            qr_x = max(0, min(qr_x, self.width() - self.qr_code_size))
            qr_y = max(0, min(qr_y, self.height() - self.qr_code_size))
            
            self.qr_label.setGeometry(qr_x, qr_y, self.qr_code_size, self.qr_code_size)
            self.qr_label.raise_()
            self.qr_label.show()
        except Exception as e:
            print(f"Error positioning QR code: {e}")
            self.qr_label.hide()
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.media_files:
            self.show_current_media()

class MediaPlayer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Initialize pygame mixer with more robust error handling
        try:
            import pygame
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
            self.pygame = pygame
            self.current_music = None
            self.playing = False
        except Exception as e:
            print(f"Error initializing pygame mixer: {e}")
            self.pygame = None
            
        # Volume control
        self.volume_slider = QSlider(Qt.Horizontal, self)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.valueChanged.connect(self.set_volume)
        
        # Set initial volume
        self.set_volume(70)
        
        # Layout
        layout = QVBoxLayout(self)
        
        # Music controls
        music_layout = QHBoxLayout()
        
        self.play_button = QPushButton("Play", self)
        self.play_button.clicked.connect(self.toggle_playback)
        
        self.select_music_button = QPushButton("Select Music", self)
        self.select_music_button.clicked.connect(self.select_music_file)
        
        volume_label = QLabel("Volume:", self)
        
        music_layout.addWidget(self.play_button)
        music_layout.addWidget(self.select_music_button)
        music_layout.addWidget(volume_label)
        music_layout.addWidget(self.volume_slider)
        
        layout.addLayout(music_layout)
        
        # Disable controls if pygame isn't available
        if not hasattr(self, 'pygame') or self.pygame is None:
            self.play_button.setEnabled(False)
            self.select_music_button.setEnabled(False)
            self.volume_slider.setEnabled(False)
    
    def set_volume(self, volume):
        if hasattr(self, 'pygame') and self.pygame and hasattr(self.pygame.mixer, 'music'):
            try:
                self.pygame.mixer.music.set_volume(volume / 100.0)
            except Exception as e:
                print(f"Error setting volume: {e}")
    
    def toggle_playback(self):
        if not hasattr(self, 'pygame') or self.pygame is None:
            return
            
        try:
            if self.playing:
                self.pygame.mixer.music.pause()
                self.playing = False
                self.play_button.setText("Play")
            else:
                if self.current_music:
                    self.pygame.mixer.music.unpause()
                    self.playing = True
                    self.play_button.setText("Pause")
        except Exception as e:
            print(f"Error toggling playback: {e}")
    
    def select_music_file(self):
        if not hasattr(self, 'pygame') or self.pygame is None:
            return
            
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Music File", "", "Audio Files (*.mp3 *.wav *.ogg)"
        )
        
        if file_path:
            try:
                # Stop any currently playing music
                self.pygame.mixer.music.stop()
                
                # Load and play new music
                self.pygame.mixer.music.load(file_path)
                self.pygame.mixer.music.play(-1)  # -1 means loop indefinitely
                
                self.current_music = file_path
                self.playing = True
                self.play_button.setText("Pause")
            except Exception as e:
                print(f"Error playing music: {e}")
                QMessageBox.warning(self, "Playback Error", 
                    f"Could not play the selected music file: {os.path.basename(file_path)}\n\nError: {str(e)}")

class CloudinarySlideshow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Initialize variables
        self.cloud_name = ""
        self.api_key = ""
        self.api_secret = ""
        self.download_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloaded_media")
        self.cloudinary_monitor = None
        self.downloaders = []
        self.folder_watcher = QFileSystemWatcher(self)
        
        # Create central widget and main layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)
        
        # Create sidebar toggle button
        self.toggle_sidebar_button = QPushButton("≡")
        self.toggle_sidebar_button.setFixedSize(30, 30)
        self.toggle_sidebar_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(52, 152, 219, 0.7);
                color: white;
                border-radius: 15px;
                font-weight: bold;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: rgba(41, 128, 185, 0.8);
            }
        """)
        self.toggle_sidebar_button.clicked.connect(self.toggle_sidebar)
        
        # Create sidebar widget
        self.sidebar_visible = True
        
        # Create download folder if it doesn't exist
        if not os.path.exists(self.download_folder):
            os.makedirs(self.download_folder)
        
        # Set window properties
        self.setWindowTitle("Cloudinary Slideshow")
        self.setMinimumSize(1024, 768)
        
        # Create slideshow widget (right side - 75% of width)
        self.slideshow = SlideshowWidget()
        
        # Create control panel (left side - 25% of width)
        self.control_panel = QWidget()
        self.control_panel.setMaximumWidth(300)
        self.control_layout = QVBoxLayout(self.control_panel)
        
        # Add control panel and slideshow to main layout
        self.main_layout.addWidget(self.control_panel)
        self.main_layout.addWidget(self.slideshow, 3)  # 3:1 ratio
        
        # Create and add controls to control panel
        self.create_cloudinary_controls()
        self.create_slideshow_controls()
        self.create_qr_code_controls()
        self.create_appearance_controls()
        
        # Add media player
        self.media_player = MediaPlayer()
        self.control_layout.addWidget(self.media_player)
        
        # Add spacer to push controls up
        self.control_layout.addStretch()
        
        # Status bar
        self.statusBar().showMessage("Ready. Please connect to Cloudinary.")
        
        # Set up folder monitoring
        self.setup_folder_monitoring()
        
    def setup_folder_monitoring(self):
        self.folder_watcher.addPath(self.download_folder)
        self.folder_watcher.directoryChanged.connect(self.refresh_media_list)
        self.refresh_media_list()
    
    def refresh_media_list(self):
        # Get all media files in the download folder
        media_files = []
        for file in os.listdir(self.download_folder):
            file_path = os.path.join(self.download_folder, file)
            ext = file.split('.')[-1].lower()
            
            if ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']:
                media_type = 'image'
            elif ext in ['mp4', 'webm', 'mov', 'avi', 'mkv', 'wmv', 'flv']:
                media_type = 'video'
            else:
                continue
                
            media_files.append((file_path, media_type))
        
        # Update slideshow with new media list
        self.slideshow.media_files = media_files
        if media_files and not self.slideshow.timer.isActive():
            self.slideshow.start_slideshow()
    
    def toggle_sidebar(self):
        self.sidebar_visible = not self.sidebar_visible
        if self.sidebar_visible:
            self.control_panel.show()
            self.toggle_sidebar_button.setText("≡")
        else:
            self.control_panel.hide()
            self.toggle_sidebar_button.setText("≡")
        
        self.toggle_sidebar_button.raise_()
        self.toggle_sidebar_button.move(10, 10)
    
    def create_cloudinary_controls(self):
        # Image source group
        image_source_group = QGroupBox("Image Source")
        image_source_layout = QVBoxLayout()
        
        # Local folder section
        local_folder_label = QLabel("Current Folder:")
        self.folder_path_label = QLabel(self.download_folder)
        self.folder_path_label.setWordWrap(True)
        
        local_folder_button = QPushButton("Select Folder")
        local_folder_button.clicked.connect(self.select_local_folder)
        
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(local_folder_label)
        folder_layout.addWidget(self.folder_path_label, 1)  # 1 = stretch factor
        
        image_source_layout.addLayout(folder_layout)
        image_source_layout.addWidget(local_folder_button)
        
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        image_source_layout.addWidget(separator)
        
        # Cloudinary section
        cloudinary_label = QLabel("Cloudinary Download:")
        image_source_layout.addWidget(cloudinary_label)
        
        cloudinary_form = QFormLayout()
        self.cloud_name_input = QLineEdit()
        self.api_key_input = QLineEdit()
        self.api_secret_input = QLineEdit()
        self.api_secret_input.setEchoMode(QLineEdit.Password)
        
        connect_button = QPushButton("Connect")
        connect_button.clicked.connect(self.connect_to_cloudinary)
        
        cloudinary_form.addRow("Cloud Name:", self.cloud_name_input)
        cloudinary_form.addRow("API Key:", self.api_key_input)
        cloudinary_form.addRow("API Secret:", self.api_secret_input)
        cloudinary_form.addRow("", connect_button)
        
        image_source_layout.addLayout(cloudinary_form)
        image_source_group.setLayout(image_source_layout)
        
        self.control_layout.addWidget(image_source_group)
    
    def select_local_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Select Media Folder")
        if folder_path:
            self.download_folder = folder_path
            self.folder_path_label.setText(folder_path)
            
            # Update folder watcher
            self.folder_watcher.removePaths(self.folder_watcher.directories())
            self.folder_watcher.addPath(folder_path)
            
            self.refresh_media_list()
            
            if self.cloudinary_monitor and self.cloudinary_monitor.isRunning():
                self.statusBar().showMessage(f"Downloaded media will be saved to: {folder_path}")
            else:
                self.statusBar().showMessage(f"Using media from: {folder_path}")
    
    def create_slideshow_controls(self):
        # Slideshow settings group
        slideshow_group = QGroupBox("Slideshow Settings")
        slideshow_layout = QFormLayout()
        
        # Display duration
        self.duration_slider = QSlider(Qt.Horizontal)
        self.duration_slider.setRange(1, 10)
        self.duration_slider.setValue(5)
        self.duration_slider.valueChanged.connect(
            lambda value: self.slideshow.set_display_duration(value * 1000)
        )
        duration_label = QLabel("5 seconds")
        self.duration_slider.valueChanged.connect(
            lambda value: duration_label.setText(f"{value} seconds")
        )
        
        duration_layout = QHBoxLayout()
        duration_layout.addWidget(self.duration_slider)
        duration_layout.addWidget(duration_label)
        
        # Transition style
        self.transition_combo = QComboBox()
        self.transition_combo.addItems(["None", "Fade", "Slide", "Zoom"])
        self.transition_combo.currentTextChanged.connect(
            lambda text: self.slideshow.set_transition_style(text.lower())
        )
        
        # Overlay image
        overlay_button = QPushButton("Select Overlay Image")
        overlay_button.clicked.connect(self.select_overlay_image)
        
        slideshow_layout.addRow("Display Duration:", duration_layout)
        slideshow_layout.addRow("Transition Style:", self.transition_combo)
        slideshow_layout.addRow("Overlay Image:", overlay_button)
        
        slideshow_group.setLayout(slideshow_layout)
        self.control_layout.addWidget(slideshow_group)
    
    def create_qr_code_controls(self):
        # QR code settings group
        qr_group = QGroupBox("QR Code Settings")
        qr_layout = QFormLayout()
        
        # QR code enable/disable
        self.qr_enabled_combo = QComboBox()
        self.qr_enabled_combo.addItems(["Enabled", "Disabled"])
        self.qr_enabled_combo.currentTextChanged.connect(
            lambda text: self.slideshow.set_qr_code_enabled(text == "Enabled")
        )
        
        # QR code position
        self.qr_position_combo = QComboBox()
        self.qr_position_combo.addItems(["Bottom-Right", "Bottom-Left", "Top-Right", "Top-Left"])
        self.qr_position_combo.currentTextChanged.connect(
            lambda text: self.slideshow.set_qr_code_position(text.lower().replace("-", ""))
        )
        
        # QR code size
        self.qr_size_slider = QSlider(Qt.Horizontal)
        self.qr_size_slider.setRange(100, 300)
        self.qr_size_slider.setValue(150)
        self.qr_size_slider.valueChanged.connect(self.slideshow.set_qr_code_size)
        qr_size_label = QLabel("150 px")
        self.qr_size_slider.valueChanged.connect(
            lambda value: qr_size_label.setText(f"{value} px")
        )
        
        qr_size_layout = QHBoxLayout()
        qr_size_layout.addWidget(self.qr_size_slider)
        qr_size_layout.addWidget(qr_size_label)
        
        # QR code URL/data
        self.qr_data_input = QLineEdit("https://example.com")
        self.qr_data_input.editingFinished.connect(
            lambda: self.slideshow.set_qr_code_data(self.qr_data_input.text())
        )
        
        qr_layout.addRow("QR Code:", self.qr_enabled_combo)
        qr_layout.addRow("Position:", self.qr_position_combo)
        qr_layout.addRow("Size:", qr_size_layout)
        qr_layout.addRow("URL/Data:", self.qr_data_input)
        
        qr_group.setLayout(qr_layout)
        self.control_layout.addWidget(qr_group)
    
    def create_appearance_controls(self):
        # Appearance settings group
        appearance_group = QGroupBox("Appearance")
        appearance_layout = QFormLayout()
        
        # Background color
        bg_color_button = QPushButton("Choose Background Color")
        bg_color_button.clicked.connect(self.choose_background_color)
        
        # Background image
        bg_image_button = QPushButton("Choose Background Image")
        bg_image_button.clicked.connect(self.choose_background_image)
        
        # Clear background image
        clear_bg_button = QPushButton("Clear Background Image")
        clear_bg_button.clicked.connect(lambda: self.slideshow.set_background_image(None))
        
        # Vertical margin slider
        self.margin_slider = QSlider(Qt.Horizontal)
        self.margin_slider.setRange(0, 100)
        self.margin_slider.setValue(50)
        self.margin_slider.valueChanged.connect(
            lambda value: setattr(self.slideshow, 'vertical_margin', value)
        )
        margin_label = QLabel("50 px")
        self.margin_slider.valueChanged.connect(
            lambda value: margin_label.setText(f"{value} px")
        )
        
        margin_layout = QHBoxLayout()
        margin_layout.addWidget(self.margin_slider)
        margin_layout.addWidget(margin_label)
        
        appearance_layout.addRow("Background:", bg_color_button)
        appearance_layout.addRow("Background Image:", bg_image_button)
        appearance_layout.addRow("", clear_bg_button)
        appearance_layout.addRow("Vertical Margin:", margin_layout)
        
        appearance_group.setLayout(appearance_layout)
        self.control_layout.addWidget(appearance_group)
    
    def connect_to_cloudinary(self):
        self.cloud_name = self.cloud_name_input.text().strip()
        self.api_key = self.api_key_input.text().strip()
        self.api_secret = self.api_secret_input.text().strip()
        
        if not all([self.cloud_name, self.api_key, self.api_secret]):
            QMessageBox.warning(self, "Missing Information", 
                            "Please enter all Cloudinary credentials.")
            return
        
        # Stop existing monitor if running
        if self.cloudinary_monitor and self.cloudinary_monitor.isRunning():
            self.cloudinary_monitor.stop()
        
        # Create and start new monitor (using the current download_folder)
        self.cloudinary_monitor = CloudinaryMonitor(
            self.cloud_name, self.api_key, self.api_secret, self
        )
        self.cloudinary_monitor.new_media_signal.connect(self.handle_new_media)
        self.cloudinary_monitor.start()
        
        # Make sure the download folder exists
        if not os.path.exists(self.download_folder):
            os.makedirs(self.download_folder)
            
        self.statusBar().showMessage(f"Connected to Cloudinary. New media will be downloaded to: {self.download_folder}")

    def handle_new_media(self, file_path, media_type):
        # Add to slideshow
        self.slideshow.add_media(file_path, media_type)
        self.statusBar().showMessage(f"Added media to slideshow: {os.path.basename(file_path)}")

    def download_media(self, media_url, media_type):
        # Extract filename from URL
        filename = os.path.basename(media_url.split('/')[-1])
        if '?' in filename:
            filename = filename.split('?')[0]
        
        local_path = os.path.join(self.download_folder, filename)
        
        # Download media
        downloader = MediaDownloader(media_url, local_path, self)
        downloader.download_complete.connect(
            lambda url, path: self.slideshow.add_media(path, media_type)
        )
        self.downloaders.append(downloader)
        downloader.start()
        
        self.statusBar().showMessage(f"Downloading new media: {filename}")

    def handle_downloaded_image(self, url, local_path):
        # Add to slideshow
        self.slideshow.add_media(local_path, 'image')
        self.statusBar().showMessage(f"Added image to slideshow: {os.path.basename(local_path)}")
    
    def select_overlay_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Overlay Image", "", "Image Files (*.png *.jpg *.jpeg *.gif)"
        )
        
        if file_path:
            self.slideshow.set_overlay_image(file_path)
            
    def choose_background_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Background Image", "", "Image Files (*.png *.jpg *.jpeg *.gif)"
        )
    
        if file_path:
            self.slideshow.set_background_image(file_path)
    
    def choose_background_color(self):
        color = QColorDialog.getColor(self.slideshow.background_color, self)
        if color.isValid():
            self.slideshow.set_background_color(color)
    
    def handle_error(self, message, exception=None):
        """Central error handling function to log errors and recover when possible"""
        error_msg = f"{message}"
        if exception:
            error_msg += f": {str(exception)}"
        
        print(f"ERROR: {error_msg}")
        
        # Show the error in status bar briefly
        self.statusBar().showMessage(f"Error: {message}", 5000)
        
        # For critical errors that need user attention, show a message box
        if exception and not isinstance(exception, (IOError, OSError)):
            QMessageBox.warning(self, "Error", error_msg)
            
    def showEvent(self, event):
        super().showEvent(event)
        # Position the toggle button
        self.toggle_sidebar_button.raise_()
        self.toggle_sidebar_button.move(10, 10)
        
        # Add the toggle button to the main window, not to a layout
        self.toggle_sidebar_button.setParent(self.central_widget)
        self.toggle_sidebar_button.show()
    
    def closeEvent(self, event):
        # Signal all running threads to stop
        if hasattr(self, 'cloudinary_monitor') and self.cloudinary_monitor and self.cloudinary_monitor.isRunning():
            self.cloudinary_monitor.stop()
        
        # Stop the slideshow to stop any video playback
        if hasattr(self, 'slideshow'):
            self.slideshow.stop_slideshow()
        
        # Clean up downloaders
        if hasattr(self, 'downloaders'):
            for downloader in self.downloaders:
                if downloader.isRunning():
                    downloader.wait(1000)  # Wait up to 1 second
        
        # Quit pygame mixer
        try:
            if hasattr(self, 'media_player') and hasattr(self.media_player, 'pygame') and self.media_player.pygame:
                self.media_player.pygame.mixer.quit()
        except Exception as e:
            print(f"Error shutting down pygame: {e}")
        
        # Give Qt time to clean up threads
        time.sleep(0.2)
        
        super().closeEvent(event)

def main():
    try:
        app = QApplication(sys.argv)
        try:
            pygame.init()  # Initialize pygame
        except Exception as e:
            print(f"Warning: Failed to initialize pygame: {e}")
            
        app.setStyle('Fusion')  # Modern look and feel
        
        # Apply stylesheet for a more modern look
        app.setStyleSheet("""
            QWidget {
                font-size: 10pt;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #cccccc;
                border-radius: 5px;
                margin-top: 1ex;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 5px;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #1c6ea4;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #999999;
            }
            QSlider::groove:horizontal {
                border: 1px solid #bbb;
                background: white;
                height: 10px;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #3498db;
                border: 1px solid #5c5c5c;
                width: 18px;
                margin: -2px 0;
                border-radius: 9px;
            }
        """)
        
        window = CloudinarySlideshow()
        window.show()
        
        return app.exec_()
    except Exception as e:
        print(f"Critical error in main: {e}")
        QMessageBox.critical(None, "Critical Error", 
                         f"A critical error occurred starting the application:\n\n{str(e)}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
