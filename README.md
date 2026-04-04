# PicoWave

`PicoWave` is a custom oscilloscope app for the `PicoScope 2204A`, built with Python, `PySide6`, `numpy`, and `picosdk`. The goal of the project is to add workflow, annotation, visualization, math, and UI features that are not available in the stock PicoScope software.

The app talks to real hardware through the `ps2000` driver family. There is no demo or simulation mode.

## Features

- Real hardware connection for the PicoScope 2204A
- Startup connection dialog to choose an available oscilloscope
- `Run / Stop` acquisition control
- `Block` and `Fast streaming` modes
- Combined `Timebase / Sample rate` control with automatic pairing logic
- Channel A, Channel B, and `Custom Math Channel`
- Trigger configuration, trigger marker, and direct trigger dragging on the waveform
- Waveform history with paging and preview thumbnails
- Waveform annotations, eraser, inline text, zoom box, wheel zoom, and pan
- Front-panel scope status widget with activity LED
- Logging to `logs/picowave.log`

## Requirements

- Windows
- Python 3.11+ recommended
- PicoScope 2204A
- PicoSDK installed from Pico Technology

## Quick Start

1. Install the official PicoSDK from Pico Technology:
   `https://www.picotech.com/library/our-oscilloscope-software-development-kit-sdk`
2. Create and activate a virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\activate
```

3. Install the Python dependencies:

```powershell
pip install -r requirements.txt
```

4. Start the app:

```powershell
python main.py
```

## Basic User Guide

1. Launch the app.
2. In the startup dialog, select the connected PicoScope and click `Connect`.
3. Use `Mode` to choose `Block` or `Fast streaming`.
4. Use `Timebase / Sample rate` to choose the time window and the valid sample rate for the current mode.
5. Click `stopped` to start acquisition. It changes to `running`.
6. Click Channel `A`, `B`, or `Custom Math Channel` to open the side configuration panel.
7. Click `Trigger` to configure trigger mode, type, source, threshold, direction, and pre-trigger.
8. Use the waveform `+ / -` history control and the preview row to browse saved captures.
9. Use the pen icon to annotate the waveform or the magnifying glass to draw a zoom box.

## Development

Install the full Python dependencies:

```powershell
.\venv\Scripts\activate
pip install -r requirements.txt
```

Run the tests:

```powershell
.\venv\Scripts\activate
python -m unittest -q
```

## Project Files

- `main.py`: main UI and PicoScope controller code
- `test_main.py`: regression and behavior tests
- `picowave/icons/`: UI icons
- `data/`: reference material and vendor documents
- `logs/`: runtime log output

## Contributors

- Jared Chinchilla
- OpenAI Codex

## Notes

- This project uses the `ps2000` API path for the `2204A`.
- Timing options are revalidated automatically when `Mode`, channel state, voltage range, or `Timebase` changes.
- Waveform history is reset when changes invalidate comparison between saved captures and new acquisitions.
- Runtime logs are written to `logs/picowave.log`.

## Troubleshooting

If the startup dialog does not show any oscilloscope:

- make sure the official PicoSDK is installed from Pico Technology
- make sure `picosdk` is installed in the active Python environment
- make sure the PicoScope 2204A is plugged in and recognized by Windows
- check `logs/picowave.log` for device-discovery and driver-loading errors
