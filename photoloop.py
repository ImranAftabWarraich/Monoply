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

# ... [Keep all your existing classes until SlideshowWidget] ...

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

    # ... [Keep all your existing methods from update_video_frame to resizeEvent] ...

# ... [Keep all your remaining classes unchanged] ...

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
