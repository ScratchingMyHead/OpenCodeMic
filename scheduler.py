#!/usr/bin/env python3
"""
Scheduler for opencode — reads tasks from schedule.txt and fires them
via `opencode run --attach` to the desktop backend. Each task runs in its
own isolated session titled "scheduler", visible in the GUI sidebar.
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
BACKEND_URL = 'http://127.0.0.1:38023'

# Map human-readable model names → provider/modelID for --model flag
MODEL_MAP = {
    'deepseek v4 flash free': 'opencode/deepseek-v4-flash-free',
    'deepseek':              'opencode/deepseek-v4-flash-free',
    'big pickle':            'opencode/big-pickle',
    'llama':                 'llama/Qwen3.6-35B-A3B-UD-Q4_K_M',
    'qwen':                  'llama/Qwen3.6-35B-A3B-UD-Q4_K_M',
}


def read_schedule():
    """Return list of (datetime, priority, model, agent, message) tuples."""
    if not os.path.exists(SCHEDULE_FILE):
        return []
    tasks = []
    with open(SCHEDULE_FILE) as f:
        for line in f:
            line = line.strip()
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
    return tasks


def write_schedule(tasks):
    lines = []
    for dt, prio, model, agent, msg in tasks:
        fields = [dt.strftime('%Y/%m/%d %H:%M'), prio, model, agent, msg]
        lines.append(' | '.join(fields))
    with open(SCHEDULE_FILE, 'w') as f:
        if lines:
            f.write('\n'.join(lines) + '\n')
        else:
            f.write('')


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
                print(f"  scheduler: deleted old session {sid[:20]}…")
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as e:
        print(f"  scheduler: cleanup error: {e}")


def execute_task(model, agent, message):
    """
    Run `opencode run --attach` to execute the task. Reuses the existing
    'scheduler' session if one exists; creates a new one otherwise.
    Returns True on success (process exited 0), False otherwise.
    """
    cmd = [
        'opencode', 'run',
        '--attach', BACKEND_URL,
        '--dir', '/home/rj/su',
    ]

    # Reuse existing scheduler session or create new one
    existing_id = get_last_scheduler_session()
    if existing_id:
        cmd.extend(['--continue', '--session', existing_id])
        print(f"  scheduler: reusing session {existing_id[:20]}…")
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
    print(f"  scheduler: running — {cmd_str[:200]}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,          # 10 minute max per task
        )
        if result.returncode == 0:
            print(f"  scheduler: task completed (exit 0)")
            # Print last line of stdout for confirmation
            last_line = result.stdout.strip().split('\n')[-1] if result.stdout.strip() else ''
            if last_line:
                print(f"  scheduler: last output: {last_line[:200]}")

            # Clean up old scheduler sessions (keep the one we just used)
            new_id = get_last_scheduler_session()
            clean_old_scheduler_sessions(keep_id=new_id)
            return True
        else:
            print(f"  scheduler: task failed (exit {result.returncode})")
            stderr_snippet = result.stderr.strip()[:300] if result.stderr else ''
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
    while True:
        try:
            tasks = read_schedule()
            now = datetime.now()
            expired = [(dt, prio, model, agent, msg)
                       for dt, prio, model, agent, msg in tasks if now >= dt]
            pending = [(dt, prio, model, agent, msg)
                       for dt, prio, model, agent, msg in tasks if now < dt]

            if expired:
                for dt, prio, model, agent, msg in expired:
                    should_fire = (prio == 'high')

                    if not should_fire:
                        idle_s = None
                        if logger and hasattr(logger, 'get_idle_seconds'):
                            idle_s = logger.get_idle_seconds()
                        should_fire = idle_s is not None and idle_s >= IDLE_THRESHOLD

                    if should_fire:
                        if execute_task(model, agent, msg):
                            print(f"  scheduler: task executed OK")
                        else:
                            # Re-schedule for 5 minutes later on failure
                            retry_dt = now + timedelta(minutes=5)
                            pending.append((retry_dt, prio, model, agent, msg))
                            print(f"  scheduler: will retry at {retry_dt.strftime('%H:%M')}")
                    else:
                        new_dt = (now + timedelta(seconds=IDLE_THRESHOLD)).replace(second=0, microsecond=0)
                        pending.append((new_dt, prio, model, agent, msg))
                        status = f"idle {idle_s:.0f}s" if idle_s is not None else "no idle info"
                        print(f"  scheduler: busy ({status}), rescheduled to {new_dt.strftime('%H:%M')}")

                write_schedule(pending)
        except Exception as e:
            print(f"scheduler: error: {e}")

        time.sleep(CHECK_INTERVAL)


def start(logger=None):
    """Start the scheduler thread. `logger` is optional (for idle detection)."""
    t = threading.Thread(target=scheduler_loop, args=(logger,), daemon=True, name='scheduler')
    t.start()
    return t
