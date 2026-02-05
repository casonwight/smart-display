# Smart Display Configuration
# Copy this file to config.py and fill in your values

# Display
DISPLAY_WIDTH = 800
DISPLAY_HEIGHT = 480
MAX_PARTIAL_REFRESHES = 15

# GPIO Pins - Rotary Encoder
ENCODER_PIN_A = 5
ENCODER_PIN_B = 6
ENCODER_PIN_SW = 13

# GPIO Pins - Buttons
BUTTON_UP_PIN = 26
BUTTON_DOWN_PIN = 16

# GPIO Pins - Audio Amplifiers
AMP_SD_PIN_1 = 22
AMP_SD_PIN_2 = 23
I2S_BCLK_PIN = 18

# Audio
AUDIO_DEVICE = "hw:2,0"
DEFAULT_VOLUME = 50
VOLUME_STEP = 5

# Voice
WAKE_WORD = "hey olly"
VOICE_MIC_DEVICE = "plughw:3,0"  # USB microphone ALSA device
VOICE_SAMPLE_RATE = 16000  # Required by Vosk/Whisper
VOICE_COMMAND_DURATION = 3  # Seconds to record after wake word
PORCUPINE_ACCESS_KEY = "YOUR_PORCUPINE_ACCESS_KEY_HERE"  # Get from https://picovoice.ai/
PORCUPINE_MODEL_PATH = "models/Hey-Ollie_en_raspberry-pi_v4_0_0.ppn"

# Paths
RECIPE_DIR = "recipes/"
ASSETS_DIR = "assets/"
MODELS_DIR = "models/"

# UI Regions (x, y, width, height)
REGION_HEADER = (0, 0, 800, 60)
REGION_CONTENT = (0, 60, 800, 360)
REGION_FOOTER = (0, 420, 800, 60)

# Fonts
FONT_LARGE = 48
FONT_MEDIUM = 32
FONT_SMALL = 24
