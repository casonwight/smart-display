# Smart Display
I am working on a smart assistant with a Raspberry Pi 5 and an e-ink display.

## Menus
The display should have the following functions:
- Home/default
- Recipes
- Timers
- Music

### Home Screen
Shows the time and maybe a cool background image of an otter. Anytime the rotary encoder is pushed/twisted, it goes to a screen to select one of the other three options.
Buttons always control only volume.
Rotary encoder lets user click between apps. 
The screen should allow for partial refresh, so "boxes" around each option are the only part of the screen that should be refreshed.

### Recipes
All recipes are saved using the cooklang formatting.
When clicked with the rotary encoder, shows recipe category options of recipes stored on the device.
When a recipe category is selected (via encoder twisting and then pushing), all of the recipes for the category are shown and can be selected.
When a recipe is selected, it shows the recipe on screen. Scrolling through the recipe is allowed via rotary encoder.
Partial refresh selection boxes apply here as well.

### Timers
- View current timers (when selecting a timer, can delete it or add more time)
- Start a new timer
- Delete all timers

### Music
Basic spotify music functions

### Mic controls (stretch goal)
I want to be able to use a wake word to perform most of the above functions. Things like the following (after a wake word):
- "Show me the chocolate chip cookie recipe"
- "Start a timer for 12 minutes"
- "Play 'Surface Pressure' on spotify"

## Components
This device has two speakers, a mic, a raspberry pi 5, an e-ink display, a rotary encoder, and two buttons.

### Speakers
Gikfun 2" 4Ohm 3W Full Range Audio Speaker Stereo Woofer Loudspeaker for Arduino (Pack of 2pcs) EK1725
MAX98357 MAX98357A I2S 3W Class D Amplifier Breakout Interface I2S DAC Decoder for Audio Raspberry Pi Esp32 Arduino Zero
I have two speakers, each with a red and black wire. These red and black wires are connected to two small amp boards. 
Each board has two slots to receive the red/black speaker wires and each has 7 outgoing connections that go to the gpio pins.
Here are the wire colors (same colors for both amps):
- VIN (orange)
- GND (yellow)
- SD (green)
- GAIN (blue)
- DIN (purple)
- BCLK (grey)
- LRC (white)

### Microphone
DUNGZDUZ USB Microphone for Laptop and Desktop Computer, High Sensitivity for Clear Call, Plug-and-Play, High Gain, Cordless Mini-Sized Portable, Ideal for Work & Study
My microphone is a usb microphone attached to the pi via a usb extension cable.

### Rotary Encoder
Konohan 10 Pcs 360 Degree Rotary Encoder Code Switch Digital Potentiometer with Push Button 5 Pins Handle Length 20mm and Knob Cap Compatible with Arduino EC11
I have a rotary encoder with a push button that has 5 outgoing wires that connect to the gpio pins.
- SWITCH (red)
- GND for switch (brown)
- OUT A (orange)
- GND for rotation (yellow)
- OUT B (green)

### Buttons
Gebildet 12pcs 7mm 3V-6V-12V-24V-230V/1A Prewired Mini Momentary Push Button,SPST Nomal Open ON/Off 2 Pin Round Button for Model Railway Hobby
I have 2 buttons each with a red and a black wire that can connect to the gpio pins
- Signal (red)
- GND (black)

### E-Ink Display
Waveshare 7.5inch E-Ink Display HAT Compatible with Raspberry Pi 5/4B/3B/Zero/Zero W/Zero 2W/Pico/Pico W/Pico WH, 800×480 Resolution SPI Interface
Finally, I have an e-ink display with and e-paper driver HAT that has 8 connectors for gpio pins
- PWR (brown)
- BUSY (purple)
- RST (white)
- DC (green)
- CS (orange)
- CLK (yellow)
- DIN (blue)
- GND (black)
- VCC (red)

## GPIO Pin Layout
Contained in `gpio_pin_layout.csv`

