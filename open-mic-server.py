#!/usr/bin/env python3
"""
open-mic-server.py - PC web service for OpenCodeMic

Python replacement for the original Perl server. Listens for POST requests
from the OpenCodeMic Android app and sends keystrokes to the active window
and/or the opencode GUI via CDP bridge.

Usage:
    python3 open-mic-server.py [port]
"""
import json, os, re, subprocess, sys, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import cdp_bridge

BUFFER_FILE = '/tmp/opencode_mic_buffer.txt'


class ServerState:
    def __init__(self):
        self.focus_mode = True
        self.paused = False
        self.auto_exec = False
        self.auto_exec_timer = None
        self.last_sent = ''
        self.word_counts = []


state = ServerState()


def read_buffer():
    if not os.path.exists(BUFFER_FILE):
        return ''
    try:
        with open(BUFFER_FILE) as f:
            return f.read()
    except OSError:
        return ''


def save_buffer(content):
    try:
        with open(BUFFER_FILE, 'w') as f:
            f.write(content)
    except OSError as e:
        print(f"save_buffer: {e}", file=sys.stderr)


def clear_buffer():
    try:
        os.unlink(BUFFER_FILE)
    except FileNotFoundError:
        pass


def send_cdp_keys(*keys):
    if not state.focus_mode:
        return
    key_str = ','.join(keys)
    if key_str == 'Escape':
        cdp_bridge.do_escape(3)
    elif key_str == 'Enter':
        cdp_bridge.do_enter()
    elif key_str == 'Tab':
        cdp_bridge.do_tab()
    elif key_str == 'C-w':
        cdp_bridge.do_delete_word()
    elif key_str == 'C-u':
        cdp_bridge.do_clear_line()


def send_text(text):
    if state.focus_mode:
        cdp_bridge.do_type_text(text)
    else:
        subprocess.run(['xdotool', 'type', text])


def cancel_auto_exec():
    if state.auto_exec_timer:
        state.auto_exec_timer.cancel()
        state.auto_exec_timer = None


def spawn_auto_exec():
    if not state.auto_exec:
        return
    cancel_auto_exec()
    timer = threading.Timer(2.0, do_auto_enter)
    timer.daemon = True
    timer.start()
    state.auto_exec_timer = timer


def do_auto_enter():
    if state.focus_mode:
        cdp_bridge.do_enter()
    else:
        subprocess.run(['xdotool', 'key', 'Enter'])


def process_text(chunk):
    chunk = re.sub(r'\[.+?\]', '', chunk)
    chunk = chunk.strip()
    if not chunk:
        return

    buffer = read_buffer()
    combined = (buffer + " " + chunk).strip()

    if re.search(r'\bstop\W*stop\b', combined, re.IGNORECASE):
        print("KEYWORD: stop stop -> Escape x3")
        for _ in range(3):
            send_cdp_keys('Escape')
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\bgo\W*go\b', combined, re.IGNORECASE):
        print("KEYWORD: go go -> Enter")
        send_cdp_keys('Enter')
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\b(?:enter|execute)\b', combined, re.IGNORECASE):
        print("KEYWORD: enter/execute -> Enter")
        send_cdp_keys('Enter')
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\bundo\b', combined, re.IGNORECASE):
        n = len(re.findall(r'\bundo\b', combined, re.IGNORECASE))
        print(f"KEYWORD: undo x {n}")
        for _ in range(n):
            if not state.word_counts:
                break
            wc = state.word_counts.pop()
            for _ in range(wc):
                send_cdp_keys('C-w')
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\bdelete\W*word\b', combined, re.IGNORECASE):
        print("KEYWORD: delete word -> C-w")
        send_cdp_keys('C-w')
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\b(?:clear\W*line|erase\W*text)\b', combined, re.IGNORECASE):
        print("KEYWORD: clear line/erase text -> C-u")
        send_cdp_keys('C-u')
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\btab\b|\bnext\W*agent\b', combined, re.IGNORECASE):
        print("KEYWORD: tab/next agent -> Tab")
        send_cdp_keys('Tab')
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\bpause\W*work\b', combined, re.IGNORECASE):
        state.paused = True
        print(f"KEYWORD: pause work -> paused={state.paused}")
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\bresume\W*work\b', combined, re.IGNORECASE):
        state.paused = False
        print(f"KEYWORD: resume work -> paused={state.paused}")
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\bfocus\W*off\b', combined, re.IGNORECASE):
        state.focus_mode = False
        print(f"KEYWORD: focus off -> focus_mode={state.focus_mode}")
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\bfocus\W*on\b', combined, re.IGNORECASE):
        state.focus_mode = True
        print(f"KEYWORD: focus on -> focus_mode={state.focus_mode}")
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\b(?:focus|activate)\W*terminal\b', combined, re.IGNORECASE):
        print("KEYWORD: focus/activate terminal -> GUI")
        try:
            result = subprocess.run(
                ['xdotool', 'search', '--name', '^OpenCode$'],
                capture_output=True, text=True, timeout=5
            )
            wid = result.stdout.strip()
            if wid:
                subprocess.run(['wmctrl', '-i', '-a', wid], timeout=5)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\benable\W*automatic\W*execution\b', combined, re.IGNORECASE):
        state.auto_exec = True
        print("KEYWORD: enable automatic execution")
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r'\bdisable\W*automatic\W*execution\b', combined, re.IGNORECASE):
        state.auto_exec = False
        cancel_auto_exec()
        print("KEYWORD: disable automatic execution")
        clear_buffer()
        state.last_sent = ''
        return

    if re.search(r"^i['´`]?m\s+not\s+sure\.?\s*$", chunk, re.IGNORECASE):
        print(f"FILTER (hallucination): {chunk}")
        return
    if re.search(r"^i['´`]?m\s+not\s+going\s+to\s+get\s+it\.?\s*$", chunk, re.IGNORECASE):
        print(f"FILTER (hallucination): {chunk}")
        return
    if re.search(r'\bclick\b', chunk, re.IGNORECASE):
        print(f"FILTER (click): {chunk}")
        return

    if state.paused:
        print(f"PAUSED (dropped): {chunk}")
        return

    if chunk == state.last_sent:
        print(f"SKIP (duplicate): {chunk}")
        return

    chunk = re.sub(r'\bperiod\b', '.', chunk, flags=re.IGNORECASE)
    chunk = re.sub(r'\bcomma\b', ',', chunk, flags=re.IGNORECASE)
    chunk = re.sub(r'\bquestion\s*mark\b', '?', chunk, flags=re.IGNORECASE)
    chunk = re.sub(r'\bexclamation\s*mark\b', '!', chunk, flags=re.IGNORECASE)
    chunk = re.sub(r'\bdash\b', '-', chunk, flags=re.IGNORECASE)
    chunk = re.sub(r'\bslash\b', '/', chunk, flags=re.IGNORECASE)
    chunk = re.sub(r'\bcolon\b', ':', chunk, flags=re.IGNORECASE)
    chunk = re.sub(r'\bsemicolon\b', ';', chunk, flags=re.IGNORECASE)
    chunk = re.sub(r' ([.,!?:;])', r'\1', chunk)

    to_send = f" {chunk}"
    print(f"SEND:{to_send}", end='')
    send_text(to_send)
    print(" [OK]")
    state.last_sent = chunk

    wc = len(chunk.split())
    if wc > 0:
        state.word_counts.append(wc)

    tail = chunk[-10:] if len(chunk) > 10 else chunk
    save_buffer(tail)
    spawn_auto_exec()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length).decode()
        if raw:
            try:
                data = json.loads(raw)
                text = data.get('text', '')
                if text:
                    process_text(text)
            except json.JSONDecodeError:
                pass
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'ok': True}).encode())

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9876
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f"OpenCodeMic server listening on http://0.0.0.0:{port}")
    print(f"Buffer: {BUFFER_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
        server.server_close()
