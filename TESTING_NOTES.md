# Component Testing Notes

## Testing Status Summary

| Component | Status | Notes |
|-----------|--------|-------|
| Button 1 (GPIO 26) | Working | Use `pinctrl poll 26` to test |
| Button 2 (GPIO 16) | Working | Use `pinctrl poll 16` to test |
| Rotary Encoder Push (GPIO 13) | Working | Use `pinctrl poll 13` to test |
| Rotary Encoder Rotation (GPIO 5,6) | Working | Use `pinctrl poll 5,6` to test |
| Speakers | Working (mono) | Needs resistors for stereo - see below |
| Microphone | Working | Reduced gain to 25% for less distortion |
| E-Ink Display | Working | GPIO 4 conflict fixed in config.txt |

---

## After Reboot - Required Setup Commands

Run these commands after each reboot to enable audio:

```bash
# Enable I2S BCLK pin
pinctrl set 18 a2

# Enable both amp SD pins (for mono until resistors arrive)
pinctrl set 22,23 op dh
```

---

## Speakers - Stereo Fix (Waiting for Resistors)

Currently both speakers play mono (same audio). To enable true stereo:

### Parts Needed (ordered, arriving tomorrow)
- 100K ohm resistor (for LEFT channel - Amp 2)
- 330K ohm resistor (for RIGHT channel - Amp 1)

### Wiring Changes Required
1. Disconnect SD wires (green) from GPIO 22 and GPIO 23
2. Amp 1 SD (green) -> 330K resistor -> 5V (Pin 2 or 4) = RIGHT channel
3. Amp 2 SD (green) -> 100K resistor -> 5V (Pin 2 or 4) = LEFT channel

### After resistor install, control via:
- Enable: `pinctrl set 22,23 ip` (high-impedance, resistor sets voltage)
- Shutdown: `pinctrl set 22,23 op dl` (output low)

---

## Microphone Settings

USB mic gain was too high (100%), causing distortion. Current settings:

```bash
# Set mic to 25% gain, disable auto gain
amixer -c 3 set 'Mic' 25%
amixer -c 3 set 'Auto Gain Control' off
```

Note: Some white noise remains - this is typical for cheap USB mics. Consider:
- Powered USB hub (isolates from Pi power noise)
- Ferrite choke on USB cable
- Better quality mic (or I2S MEMS mic like INMP441)

---

## E-Ink Display Configuration

Modified `/usr/local/lib/python3.13/dist-packages/waveshare_epd-0.0.0-py3.13.egg/waveshare_epd/epdconfig.py`:

```python
# Changed from defaults to match actual wiring:
RST_PIN  = 27  # was 17
PWR_PIN  = 4   # was 18
```

Modified `/boot/firmware/config.txt`:
- Changed `dtoverlay=max98357a` to `dtoverlay=max98357a,no-sdmode`
- This prevents the audio overlay from claiming GPIO 4 (needed by display)

---

## GPIO Pin Changes Made

| Component | Wire | Old Pin | New Pin | Reason |
|-----------|------|---------|---------|--------|
| Amp GAIN (both) | Blue | Pin 14 (GND) | Pin 14 (GND) | No change — GAIN stays at GND = 9 dB |

---

## Test Commands Reference

```bash
# Test buttons
pinctrl set 26,16 ip pu && timeout 10 pinctrl poll 26,16

# Test encoder
pinctrl set 5,6,13 ip pu && timeout 10 pinctrl poll 5,6,13

# Test speakers (after enabling I2S and SD pins)
# NOTE: Speakers are on card 2 (MAX98357A), not card 0!
speaker-test -D hw:2,0 -t sine -f 440 -c 2 -s 1  # Left
speaker-test -D hw:2,0 -t sine -f 440 -c 2 -s 2  # Right

# Test microphone
arecord -D plughw:3,0 -f cd -d 4 /tmp/test.wav && aplay -D hw:2,0 /tmp/test.wav

# Test display (after reboot)
python3 -c "from waveshare_epd import epd7in5_V2; epd = epd7in5_V2.EPD(); epd.init(); epd.Clear(); epd.sleep()"
```
