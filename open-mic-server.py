#!/usr/bin/env python3
"""
open-mic-server.py - PC web service for OpenCodeMic

Listens for POST requests from the OpenCodeMic Android app and sends
keystrokes to the active window and/or the opencode GUI via CDP bridge.

Usage:
    python3 open-mic-server.py
    python3 open-mic-server.py --password mysecret
    python3 open-mic-server.py --password mysecret --https
    python3 open-mic-server.py --https
"""
import argparse, json, os, re, subprocess, sys, threading, ssl
from http.server import HTTPServer, BaseHTTPRequestHandler
import cdp_bridge
import scheduler
import conversation_logger

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
    try:
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
    except Exception as e:
        print(f"CDP ERROR: {e} — is the opencode GUI running?")


def send_text(text):
    if state.focus_mode:
        try:
            cdp_bridge.do_type_text(text)
        except Exception as e:
            print(f"CDP ERROR: {e} — is the opencode GUI running?")
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


BEEP_FILE = '/home/rj/su/computerbeep_69.mp3'


def process_text(chunk):
    chunk = re.sub(r'\[.+?\]', '', chunk)
    chunk = chunk.strip()
    if not chunk:
        return

    # "computer" prefix — play beep; if just "computer" alone, drop it
    if chunk.lower().startswith('computer'):
        try:
            subprocess.Popen(
                ['mpg123', '-q', BEEP_FILE],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            try:
                subprocess.Popen(
                    ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', BEEP_FILE],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except FileNotFoundError:
                print("WARNING: no mpg123 or ffplay found for beep")
        # If chunk is exactly "computer" (or "computer "), drop it
        if chunk.lower().strip() == 'computer':
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
    password = ''

    def do_POST(self):
        if self.password:
            auth = self.headers.get('Authorization', '')
            if auth != f'Bearer {self.password}':
                self.send_response(401)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'unauthorized'}).encode())
                return
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


def ensure_cert(cert_path, key_path):
    """Generate a self-signed cert if missing."""
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return
    import shutil
    if not shutil.which('openssl'):
        print("WARNING: openssl not found. Generate a certificate manually:")
        print(f"  openssl req -x509 -newkey rsa:2048 -keyout {key_path} -out {cert_path} -days 365 -nodes -subj /CN=OpenCodeMic")
        print("Running without HTTPS...", file=sys.stderr)
        return
    subprocess.run([
        'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
        '-keyout', key_path, '-out', cert_path,
        '-days', '365', '-nodes',
        '-subj', '/CN=OpenCodeMic'
    ], capture_output=True)
    if os.path.exists(cert_path) and os.path.exists(key_path):
        print(f"Generated self-signed cert: {cert_path}, {key_path}")
    else:
        print("WARNING: failed to generate cert. Running without HTTPS...", file=sys.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='OpenCodeMic server')
    parser.add_argument('port', nargs='?', type=int, default=9876, help='Port to listen on')
    parser.add_argument('--https', action='store_true', help='Enable HTTPS with auto-generated self-signed cert')
    parser.add_argument('--cert', help='SSL certificate file (default: ~/.config/opencode-mic/cert.pem)')
    parser.add_argument('--key', help='SSL key file (default: ~/.config/opencode-mic/key.pem)')
    parser.add_argument('--password', help='Shared secret for client authentication')
    args = parser.parse_args()

    Handler.password = args.password or ''

    if args.password and not args.https and not (args.cert and args.key):
        print(f"WARNING: password set without HTTPS — transmitted in plaintext!")

    if args.https:
        cert_dir = os.path.expanduser('~/.config/opencode-mic')
        os.makedirs(cert_dir, exist_ok=True)
        args.cert = args.cert or os.path.join(cert_dir, 'cert.pem')
        args.key = args.key or os.path.join(cert_dir, 'key.pem')

    if args.cert and args.key:
        ensure_cert(args.cert, args.key)
        if os.path.exists(args.cert) and os.path.exists(args.key):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(args.cert, args.key)
            server = HTTPServer(('0.0.0.0', args.port), Handler)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            proto = 'https'
            tag = ' + HTTPS' if args.password else ''
            print(f"Running with HTTPS{tag} on port {args.port}")
        else:
            print("WARNING: cert generation failed, falling back to HTTP")
            server = HTTPServer(('0.0.0.0', args.port), Handler)
            proto = 'http'
    else:
        server = HTTPServer(('0.0.0.0', args.port), Handler)
        proto = 'http'

    logger = conversation_logger.ConversationLogger()
    logger_thread = threading.Thread(
        target=conversation_logger.poll_loop, args=(logger,),
        daemon=True, name='logger'
    )
    logger_thread.start()
    scheduler.start(logger)
    print(f"OpenCodeMic server listening on {proto}://0.0.0.0:{args.port}")
    print(f"Buffer: {BUFFER_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
        server.server_close()
