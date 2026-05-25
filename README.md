# OpenCodeMic

Voice-controlled coding assistant. Speak commands on your Android phone and they appear on your desktop — in your terminal, editor, or anywhere.

> **⚠ Security**: This is a development toy, not a production service. The Android app sends plaintext over HTTP to the Perl server with no authentication or encryption. Anyone on your LAN can send keystrokes to your desktop. Do not expose the Perl server or CDP bridge to the internet or untrusted networks.

## Architecture

```
Phone (Android)                    Desktop (Linux)
┌─────────────────┐    TCP/9876    ┌──────────────────────┐
│ OpenCodeMic app  │ ───────────→  │ open-mic-server.pl   │
│ (Vosk STT)       │               │   ├── xdotool        │
│ streaming speech │               │   └── cdp_bridge.py  │
│ → text           │               │       (openCode GUI) │
└─────────────────┘               └──────────────────────┘
```

- **Phone**: captures audio, streams it to a Vosk model for speech-to-text, sends recognized text to the Perl server
- **Perl server**: receives text, applies keyword mappings (enter, backspace, tab, etc.), sends keystrokes via xdotool or injects text into opencode's GUI via Chrome DevTools Protocol

## Dependencies

- **Android 8+** (API 26) phone
- **Desktop**: Perl with `JSON::PP` and `HTTP::Server::PSGI`, Python 3, xdotool
- **Vosk model** (see below)

## Getting a Vosk Model

Vosk provides models at https://alphacephei.com/vosk/models

Download one of these:

| Model | Size | Accuracy | Notes |
|-------|------|----------|-------|
| `vosk-model-small-en-us-0.15` | ~40 MB | Low | Bundled fallback |
| `vosk-model-en-us-0.22` | ~2.6 GB | High | Recommended |

### Installing the Model

1. Download the model to your phone (e.g., via browser, USB transfer, or `adb push`)
2. Open the OpenCodeMic app
3. Tap **Settings** (gear icon)
4. Tap **Browse for Model** and navigate to the extracted model directory
5. The app copies it to internal storage and makes it available in the model list
6. Select the model and tap **Save**

Alternatively, you can use the included small model (`vosk-model-small-en-us-0.15`) without downloading anything — it's bundled with the app as a fallback.

## Building

```bash
git clone https://github.com/ScratchingMyHead/OpenCodeMic.git
cd OpenCodeMic
./gradlew assembleDebug
```

For direct install to a connected device:

```bash
./gradlew installDebug
```

## Setup

1. **Install the APK** on your Android phone
2. **Install the Perl server** on your desktop:

   ```bash
   cpan HTTP::Server::PSGI
   ```

3. **Launch opencode with remote debugging** (required for CDP bridge commands):

   ```bash
   opencode --remote-debugging-port=9222
   ```

   A wrapper script [`opencode.sh`](opencode.sh) does this automatically.

4. **Run the server** on your desktop (must be on the same network):

   ```bash
   perl open-mic-server.pl
   ```

5. **Configure the app**: open Settings, enter your desktop's IP address and port 9876, save

6. **Tap the mic button** to start. Speak — text appears on your desktop.

## Focus Mode

The server has two modes for where keystrokes are sent:

- **"focus on"** (default) — sends text and key commands **only to the opencode GUI** via the Chrome DevTools Protocol bridge (`cdp_bridge.py`). Requires opencode to be launched with `--remote-debugging-port=9222`. Key combos (tab, backspace, clear line, stop) are sent to opencode only.
- **"focus off"** — sends text and key commands to **whatever window is currently active** via xdotool. Tab, backspace, and other combos are skipped. Works with any application.

Say "focus on" or "focus off" at any time to switch.

## Voice Commands

| Say | Action |
|-----|--------|
| "go go" / "enter" / "execute" | Press Enter |
| "tab" / "next agent" | Tab key |
| "backspace" / "delete word" | Delete previous word |
| "clear line" / "erase text" | Clear line (Ctrl+U) |
| "stop stop" | Escape × 3 |
| punctuation (period, comma, etc.) | Types the symbol |
| "focus on" / "focus off" | Switch between opencode GUI and active window (see above) |
| "enable automatic execution" | Auto-press Enter after 2s silence |
| "disable automatic execution" | Turn auto-execution off |

The server lives at `open-mic-server.pl` inside the repo.
