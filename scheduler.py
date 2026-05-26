#!/usr/bin/env python3
"""
Scheduler for opencode — reads tasks from schedule.txt and fires them
via `opencode run --attach` to a headless opencode server running in tmux.
Each task runs in its own session titled "scheduler".

The headless server is started automatically in a tmux session named
"opencode-scheduler" if not already running.
"""
import json
import os
import re
import time
import threading
import subprocess
import shlex
from datetime import datetime, timedelta

SCHEDULE_FILE = '/home/rj/su/schedule.txt'
IDLE_THRESHOLD = 300        # minimum idle seconds for low-priority tasks
CHECK_INTERVAL = 60         # how often we poll the schedule

SERVER_PORT = 15110
SERVER_PASSWORD = 'scheduler123'
SERVER_TMUX = 'opencode-scheduler'
BACKEND_URL = f'http://127.0.0.1:{SERVER_PORT}'

# Map human-readable model names → provider/modelID for --model flag
MODEL_MAP = {
    'deepseek v4 flash free': 'opencode/deepseek-v4-flash-free',
    'deepseek':              'opencode/deepseek-v4-flash-free',
    'big pickle':            'opencode/big-pickle',
    'llama':                 'llama/Qwen3.6-35B-A3B-UD-Q4_K_M',
    'qwen':                  'llama/Qwen3.6-35B-A3B-UD-Q4_K_M',
    'big pickle free':       'opencode/big-pickle-free',
}


def _server_pid():
    """Return PID of the scheduler server if running, else None."""
    try:
        r = subprocess.run(
            ['tmux', 'list-panes', '-t', SERVER_TMUX, '-F', '#{pane_pid}'],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip().split('\n')[0])
    except (ValueError, OSError, subprocess.TimeoutExpired):
        pass
    return None


def ensure_server():
    """Start the headless opencode server in tmux if not already running."""
    pid = _server_pid()
    if pid is not None:
        # Verify it's actually responding
        try:
            r = subprocess.run(
                ['curl', '-so', '/dev/null', '-w', '%{http_code}',
                 f'http://127.0.0.1:{SERVER_PORT}/'],
                capture_output=True, text=True, timeout=5,
            )
            if r.stdout.strip() in ('401', '200'):
                return True
        except OSError:
            pass
        print("scheduler: server process exists but not responding, restarting")
        subprocess.run(['tmux', 'kill-session', '-t', SERVER_TMUX],
                       capture_output=True, timeout=5)

    print(f"scheduler: starting server on port {SERVER_PORT}")
    subprocess.run(['tmux', 'new-session', '-d', '-s', SERVER_TMUX],
                   capture_output=True, timeout=5)
    # Build and send the serve command with a known password
    cmd = f'cd /home/rj/su && OPENCODE_SERVER_PASSWORD={SERVER_PASSWORD} opencode serve --port {SERVER_PORT} --hostname 127.0.0.1'
    subprocess.run(['tmux', 'send-keys', '-t', SERVER_TMUX, cmd, 'Enter'],
                   capture_output=True, timeout=5)
    time.sleep(5)

    # Verify
    try:
        r = subprocess.run(
            ['curl', '-so', '/dev/null', '-w', '%{http_code}',
             f'http://127.0.0.1:{SERVER_PORT}/'],
            capture_output=True, text=True, timeout=5,
        )
        alive = r.stdout.strip() in ('401', '200')
        print(f"scheduler: server {'running' if alive else 'failed to start'}")
        return alive
    except OSError:
        return False


def read_schedule():
    """
    Return (tasks, lines, task_indices) where:
      tasks = list of (datetime, priority, model, agent, message) tuples
      lines = raw lines from the file (including comments/blanks)
      task_indices = set of line indices in `lines` that correspond to tasks
    """
    tasks = []
    lines = []
    task_indices = set()
    if not os.path.exists(SCHEDULE_FILE):
        return tasks, lines, task_indices
    with open(SCHEDULE_FILE) as f:
        for i, raw_line in enumerate(f):
            lines.append(raw_line)
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('|', 5)
            dt_str = parts[0].strip()
            priority = (parts[1].strip() if len(parts) >= 2 else 'low').lower()
            if priority not in ('low', 'high'):
                priority = 'low'
            model = parts[2].strip() if len(parts) >= 3 else ''
            agent = parts[3].strip() if len(parts) >= 4 else ''
            message = parts[4].strip() if len(parts) >= 5 else ''
            if not message:
                message = parts[1].strip() if len(parts) == 2 else ''
            try:
                dt = datetime.strptime(dt_str, '%Y/%m/%d %H:%M')
            except ValueError:
                continue
            tasks.append((dt, priority, model, agent, message))
            task_indices.add(i)
    return tasks, lines, task_indices


def resolve_model_id(name):
    """Convert a human-readable model name to provider/modelID."""
    if not name:
        return ''
    name_lower = name.strip().lower()
    # If it already looks like provider/id, pass through
    if '/' in name:
        return name.strip()
    return MODEL_MAP.get(name_lower, name.strip())


def get_last_scheduler_session():
    """Return the session ID of the most recent 'scheduler' session, or None."""
    try:
        result = subprocess.run(
            ['opencode', 'session', 'list', '--format', 'json'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        sessions = json.loads(result.stdout)
        for s in sessions:
            if s.get('title') == 'scheduler':
                return s.get('id')
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def clean_old_scheduler_sessions(keep_id=None):
    """Delete all 'scheduler' sessions except the one to keep."""
    try:
        result = subprocess.run(
            ['opencode', 'session', 'list', '--format', 'json'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return
        sessions = json.loads(result.stdout)
        for s in sessions:
            if s.get('title') == 'scheduler' and s.get('id') != keep_id:
                sid = s['id']
                subprocess.run(
                    ['opencode', 'session', 'delete', sid],
                    capture_output=True, timeout=15,
                )
                print(f"  scheduler: deleted old session {sid[:25]}…")
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as e:
        print(f"  scheduler: cleanup error: {e}")


def execute_task(model, agent, message):
    """
    Run `opencode run --attach` to execute the task on the headless server.
    Reuses the existing 'scheduler' session if one exists; creates a new one otherwise.
    Returns True on success, False otherwise.
    """
    if not ensure_server():
        print(f"  scheduler: cannot start server, skipping task")
        return False

    cmd = [
        'opencode', 'run',
        '--attach', BACKEND_URL,
        '-p', SERVER_PASSWORD,
        '-u', 'opencode',
        '--dir', '/home/rj/su',
    ]

    # Reuse existing scheduler session or create new one
    existing_id = get_last_scheduler_session()
    if existing_id:
        cmd.extend(['--continue', '--session', existing_id])
        print(f"  scheduler: reusing session {existing_id[:25]}…")
    else:
        cmd.extend(['--title', 'scheduler'])
        print(f"  scheduler: creating new scheduler session")

    if agent:
        cmd.extend(['--agent', agent])

    model_id = resolve_model_id(model)
    if model_id:
        cmd.extend(['--model', model_id])

    # Append the message as positional arg
    cmd.append(message)

    cmd_str = ' '.join(shlex.quote(c) for c in cmd)
    print(f"  scheduler: running — {cmd_str[:250]}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,          # 10 minute max per task
        )
        if result.returncode == 0:
            print(f"  scheduler: task completed (exit 0)")
            last_line = result.stdout.strip().split('\n')[-1] if result.stdout.strip() else ''
            if last_line:
                print(f"  scheduler: last output: {last_line[:200]}")

            # Clean up old scheduler sessions (keep the one we just used)
            # Get the session ID from the continued/created session
            used_id = get_last_scheduler_session()
            clean_old_scheduler_sessions(keep_id=used_id)
            return True
        else:
            print(f"  scheduler: task failed (exit {result.returncode})")
            stderr_snippet = result.stderr.strip()[:400] if result.stderr else ''
            if stderr_snippet:
                print(f"  scheduler: stderr: {stderr_snippet}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  scheduler: task timed out after 600s")
        return False
    except FileNotFoundError:
        print(f"  scheduler: 'opencode' not found in PATH")
        return False
    except Exception as e:
        print(f"  scheduler: unexpected error: {e}")
        return False


def scheduler_loop(logger=None):
    """
    Main scheduler loop.  `logger` is optional — if provided and has a
    `get_idle_seconds()` method, low-priority tasks will wait until the GUI
    has been idle for IDLE_THRESHOLD seconds.
    """
    print("scheduler: started")
    ensure_server()
    while True:
        try:
            _, lines, task_indices = read_schedule()
            now = datetime.now()

            # Find expired task lines and decide: fire, reschedule (low), or retry (failed)
            to_remove_indices = set()   # lines to remove from the original file
            extra_lines = []            # lines to append (retries, reschedules)

            for idx in task_indices:
                raw = lines[idx].strip()
                parts = raw.split('|', 5)
                try:
                    dt = datetime.strptime(parts[0].strip(), '%Y/%m/%d %H:%M')
                except ValueError:
                    continue
                if now < dt:
                    continue   # not yet due

                prio = (parts[1].strip() if len(parts) >= 2 else 'low').lower()
                if prio not in ('low', 'high'):
                    prio = 'low'
                model = parts[2].strip() if len(parts) >= 3 else ''
                agent = parts[3].strip() if len(parts) >= 4 else ''
                msg = parts[4].strip() if len(parts) >= 5 else ''

                should_fire = (prio == 'high')
                if not should_fire:
                    idle_s = None
                    if logger and hasattr(logger, 'get_idle_seconds'):
                        idle_s = logger.get_idle_seconds()
                    should_fire = idle_s is not None and idle_s >= IDLE_THRESHOLD

                if should_fire:
                    to_remove_indices.add(idx)
                    if execute_task(model, agent, msg):
                        print(f"  scheduler: task executed OK")
                    else:
                        retry_dt = now + timedelta(minutes=5)
                        extra_lines.append(f"{retry_dt.strftime('%Y/%m/%d %H:%M')} | {prio} | {model} | {agent} | {msg}\n")
                        print(f"  scheduler: will retry at {retry_dt.strftime('%H:%M')}")
                else:
                    # Low priority — reschedule further out
                    to_remove_indices.add(idx)  # replace with new time
                    new_dt = (now + timedelta(seconds=IDLE_THRESHOLD)).replace(second=0, microsecond=0)
                    extra_lines.append(f"{new_dt.strftime('%Y/%m/%d %H:%M')} | {prio} | {model} | {agent} | {msg}\n")
                    status = f"idle {idle_s:.0f}s" if should_fire is False and idle_s is not None else "no idle info"
                    print(f"  scheduler: busy ({status}), rescheduled to {new_dt.strftime('%H:%M')}")

            if to_remove_indices or extra_lines:
                # Re-read to capture any lines the agent may have appended
                _, new_lines, _ = read_schedule()
                agent_added = new_lines[len(lines):] if len(new_lines) > len(lines) else []

                # Build output: keep original non-expired lines + agent additions + extras
                result = []
                for i, line in enumerate(lines):
                    if i not in to_remove_indices:
                        result.append(line)
                if result and not result[-1].endswith('\n'):
                    result[-1] += '\n'
                result.extend(agent_added)
                result.extend(extra_lines)
                out = ''.join(result)
                if not out.endswith('\n'):
                    out += '\n'
                with open(SCHEDULE_FILE, 'w') as f:
                    f.write(out)
        except Exception as e:
            print(f"scheduler: error: {e}")

        time.sleep(CHECK_INTERVAL)


def start(logger=None):
    """Start the scheduler thread. `logger` is optional (for idle detection)."""
    t = threading.Thread(target=scheduler_loop, args=(logger,), daemon=True, name='scheduler')
    t.start()
    return t
