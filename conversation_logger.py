#!/usr/bin/env python3
"""
Conversation logger for opencode GUI — polls the renderer for new
assistant output, logs to daily files, and exposes idle detection.
Designed for virtualized conversation lists (only last N turns in DOM).
"""
import json
import os
import time
from datetime import datetime
import cdp_bridge

LOG_DIR = '/home/rj/su/logs'
POLL_INTERVAL = 5

GET_TURNS_JS = (
    "(() => {"
    'var turns = document.querySelectorAll(\'[data-component="session-turn"]\');'
    "var result = [];"
    "var now = Date.now();"
    "turns.forEach(function(t) {"
    "var assistant = t.querySelector('[data-slot=\"session-turn-assistant-content\"]');"
    "var userEl = t.querySelector('[data-slot=\"session-turn-user-content\"]');"
    "var reasoning = t.querySelector('[data-component=\"reasoning-part\"]');"
    "var reasoningText = reasoning ? reasoning.textContent : '';"
    "var fullText = t.textContent;"
    "var cleanText = reasoningText ? fullText.replace(reasoningText, '').trim() : fullText.trim();"
    "var isStreaming = !!t.querySelector('[data-component=\"text-shimmer\"][data-active=\"true\"]');"
    "result.push({"
    "hasAssistant: !!assistant,"
    "hasUser: !!userEl,"
    "hasReasoning: !!reasoning,"
    "isStreaming: isStreaming,"
    "fullText: fullText,"
    "cleanText: cleanText,"
    "reasoningText: reasoningText"
    "});"
    "});"
    "result.push({_meta: {turnCount: turns.length, time: now}});"
    "return JSON.stringify(result);"
    "})()"
)

GET_STATUS_JS = (
    "(() => {"
    "var spinner = document.querySelector('[data-component=\"session-progress\"]');"
    "var isBusy = spinner && spinner.getAttribute('data-state') === 'showing';"
    "var agentTrigger = document.querySelector('[data-slot=\"select-select-trigger-value\"]');"
    "var agent = agentTrigger ? agentTrigger.textContent.trim() : '';"
    "return JSON.stringify({busy: isBusy, agent: agent});"
    "})()"
)


class ConversationLogger:
    def __init__(self):
        self.last_hash = None
        self.last_turn_count = 0
        self.last_output_time = datetime.now()
        self.last_streaming = False
        self.current_agent = None
        self.current_text = ""
        os.makedirs(LOG_DIR, exist_ok=True)

    def poll(self):
        try:
            raw = cdp_bridge.do_eval_js(GET_TURNS_JS)
            if not raw:
                return
            data = json.loads(raw)

            meta = None
            turns = []
            for item in data:
                if "_meta" in item:
                    meta = item["_meta"]
                else:
                    turns.append(item)

            if meta is None:
                return

            turn_count = meta["turnCount"]
            now = datetime.now()

            content_hash = hash(json.dumps([t["fullText"] for t in turns]))
            any_streaming = any(t["isStreaming"] for t in turns)

            # Log new content
            if self.last_hash is not None and content_hash != self.last_hash:
                self._log_turns(turns, now, any_streaming)

            if self.last_hash is None:
                self._log_turns(turns, now, any_streaming)

            # Track output completion
            if self.last_streaming and not any_streaming:
                self.last_output_time = now
            elif content_hash != self.last_hash and not any_streaming:
                self.last_output_time = now

            self.last_hash = content_hash
            self.last_turn_count = turn_count
            self.last_streaming = any_streaming

            # Read status periodically
            if self.current_agent is None or turn_count != self.last_turn_count:
                self._update_status()

        except Exception as e:
            pass

    def _log_turns(self, turns, now, streaming):
        log_file = os.path.join(LOG_DIR, now.strftime("%Y-%m-%d") + ".md")
        ts = now.strftime("%H:%M:%S")
        agent = self.current_agent or "?"
        model_tag = f"{ts} \u00b7 {agent} \u00b7 Big Pickle"
        lines = []
        for t in turns:
            text = t["fullText"].strip()
            if not text:
                continue
            if t["hasAssistant"] and t["hasReasoning"]:
                if t["cleanText"]:
                    lines.append(f"{model_tag}\n**Thinking:** {t['reasoningText']}\n**Assistant:** {t['cleanText']}")
                else:
                    lines.append(f"{model_tag}\n**Thinking:** {t['reasoningText']}")
                continue
            if t["hasAssistant"]:
                label = "Assistant"
            elif t["hasUser"]:
                label = "User"
            else:
                label = "Turn"
            lines.append(f"{model_tag}\n**{label}:** {text}")

        if lines:
            sep = "\n\n" if os.path.exists(log_file) and os.path.getsize(log_file) > 0 else ""
            tag = " [streaming]" if streaming else ""
            with open(log_file, "a") as f:
                f.write(f"{sep}{'──' * 30}\n")
                for line in lines:
                    f.write(f"{line}{tag}\n\n")

    def _update_status(self):
        try:
            raw = cdp_bridge.do_eval_js(GET_STATUS_JS)
            if raw:
                st = json.loads(raw)
                self.current_agent = st.get("agent", "")
        except Exception:
            pass

    def get_last_output_time(self):
        return self.last_output_time

    def get_idle_seconds(self):
        if self.last_output_time is None:
            return None
        return (datetime.now() - self.last_output_time).total_seconds()

    def get_current_agent(self):
        return self.current_agent


def poll_loop(logger):
    while True:
        logger.poll()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    logger = ConversationLogger()
    print("conversation_logger: polling every 5s...")
    try:
        poll_loop(logger)
    except KeyboardInterrupt:
        print("stopped")
