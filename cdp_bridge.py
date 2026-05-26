#!/usr/bin/env python3
"""
CDP bridge for OpenCode GUI.
Connects to the opencode desktop renderer via Chrome DevTools Protocol
and injects text or dispatches keyboard commands.

Usage:
  cdp_bridge.py text "some text to insert"
  cdp_bridge.py enter
  cdp_bridge.py escape [count]
  cdp_bridge.py tab
  cdp_bridge.py backspace
  cdp_bridge.py delete_word
  cdp_bridge.py clear_line
  cdp_bridge.py agent_next
  cdp_bridge.py agent_prev
  cdp_bridge.py stdin

Also importable as a module from open-mic-server.py.
"""
import asyncio, json, sys, urllib.request, argparse

CDP_PORT = 9222
_cmd_id = 0


async def get_page_ws():
    url = f"http://localhost:{CDP_PORT}/json"
    resp = urllib.request.urlopen(url, timeout=3)
    targets = json.loads(resp.read())
    for t in targets:
        if t.get("type") == "page":
            return t["webSocketDebuggerUrl"]
    raise RuntimeError("No page target found. Is opencode running with --remote-debugging-port=9222?")


async def send_cmd(ws, method, params=None):
    global _cmd_id
    _cmd_id += 1
    msg = {"id": _cmd_id, "method": method}
    if params:
        msg["params"] = params
    await ws.send(json.dumps(msg))
    resp = await asyncio.wait_for(ws.recv(), timeout=5)
    return json.loads(resp)


async def eval_js(ws, js, await_promise=False):
    params = {"expression": js, "returnByValue": True, "awaitPromise": await_promise}
    r = await send_cmd(ws, "Runtime.evaluate", params)
    return r.get("result", {}).get("result", {}).get("value")


async def type_text(ws, text):
    escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    escaped = escaped.replace("\r", "").replace("\x00", "")
    code = f"""
    (() => {{
        const el = document.querySelector('[data-component="prompt-input"]');
        if (!el) return "NO_EL";
        el.focus();
        const sel = window.getSelection();
        if (sel.rangeCount > 0) {{
            sel.collapseToEnd();
            const range = sel.getRangeAt(0);
            const textNode = document.createTextNode('{escaped}');
            range.insertNode(textNode);
            range.setStartAfter(textNode);
            range.collapse(true);
            sel.removeAllRanges();
            sel.addRange(range);
        }} else {{
            el.textContent = el.textContent + '{escaped}';
        }}
        el.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText' }}));
        return "OK";
    }})()
    """
    return await eval_js(ws, code)


async def dispatch_key(ws, key, code, key_code, ctrl=False, shift=False):
    js = f"""
    (() => {{
        const el = document.querySelector('[data-component="prompt-input"]');
        if (!el) return "NO_EL";
        el.focus();
        const opts = {{ key: '{key}', code: '{code}', keyCode: {key_code}, which: {key_code},
            bubbles: true, cancelable: true, ctrlKey: {'true' if ctrl else 'false'}, shiftKey: {'true' if shift else 'false'} }};
        el.dispatchEvent(new KeyboardEvent('keydown', opts));
        el.dispatchEvent(new KeyboardEvent('keypress', opts));
        el.dispatchEvent(new KeyboardEvent('keyup', opts));
        return "OK";
    }})()
    """
    return await eval_js(ws, js)


async def _run_cdp(command, text=None, count=1):
    import websockets
    ws_url = await get_page_ws()
    async with websockets.connect(ws_url) as ws:
        if command == "text":
            return await type_text(ws, text)
        elif command == "enter":
            return await dispatch_key(ws, "Enter", "Enter", 13)
        elif command == "escape":
            for _ in range(count):
                await dispatch_key(ws, "Escape", "Escape", 27)
            return f"OK (escape x{count})"
        elif command == "tab":
            return await dispatch_key(ws, "Tab", "Tab", 9)
        elif command == "backspace":
            return await dispatch_key(ws, "Backspace", "Backspace", 8, ctrl=True)
        elif command == "delete_word":
            return await dispatch_key(ws, "Backspace", "Backspace", 8, ctrl=True)
        elif command == "clear_line":
            return await eval_js(ws, """
                (() => {
                    const el = document.querySelector('[data-component="prompt-input"]');
                    if (!el) return "NO_EL";
                    el.focus();
                    el.textContent = '';
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContent' }));
                    return "OK";
                })()
            """)
        elif command == "agent_next":
            return await dispatch_key(ws, ".", "Period", 190, ctrl=True)
        elif command == "agent_prev":
            return await dispatch_key(ws, ".", "Period", 190, ctrl=True, shift=True)
        elif command == "eval":
            return await eval_js(ws, text)
        elif command == "eval_async":
            return await eval_js(ws, text, await_promise=True)


def do_type_text(text):
    return asyncio.run(_run_cdp("text", text=text))


def do_enter():
    return asyncio.run(_run_cdp("enter"))


def do_escape(count=1):
    return asyncio.run(_run_cdp("escape", count=count))


def do_tab():
    return asyncio.run(_run_cdp("tab"))


def do_backspace():
    return asyncio.run(_run_cdp("backspace"))


def do_delete_word():
    return asyncio.run(_run_cdp("delete_word"))


def do_clear_line():
    return asyncio.run(_run_cdp("clear_line"))


def do_agent_next():
    return asyncio.run(_run_cdp("agent_next"))


def do_agent_prev():
    return asyncio.run(_run_cdp("agent_prev"))


def do_eval_js(js):
    return asyncio.run(_run_cdp("eval", text=js))


def do_eval_js_async(js):
    return asyncio.run(_run_cdp("eval_async", text=js))


async def run_command(args):
    import websockets
    ws_url = await get_page_ws()
    async with websockets.connect(ws_url) as ws:
        if args.command == "text":
            text = args.text
            return await type_text(ws, text)

        elif args.command == "enter":
            return await dispatch_key(ws, "Enter", "Enter", 13)

        elif args.command == "escape":
            for _ in range(args.count):
                await dispatch_key(ws, "Escape", "Escape", 27)
            return f"OK (escape x{args.count})"

        elif args.command == "tab":
            return await dispatch_key(ws, "Tab", "Tab", 9)

        elif args.command == "backspace":
            return await dispatch_key(ws, "Backspace", "Backspace", 8, ctrl=True)

        elif args.command == "delete_word":
            return await dispatch_key(ws, "Backspace", "Backspace", 8, ctrl=True)

        elif args.command == "clear_line":
            return await eval_js(ws, """
                (() => {
                    const el = document.querySelector('[data-component="prompt-input"]');
                    if (!el) return "NO_EL";
                    el.focus();
                    el.textContent = '';
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContent' }));
                    return "OK";
                })()
            """)

        elif args.command == "agent_next":
            return await dispatch_key(ws, ".", "Period", 190, ctrl=True)

        elif args.command == "agent_prev":
            return await dispatch_key(ws, ".", "Period", 190, ctrl=True, shift=True)

        elif args.command == "eval":
            return await eval_js(ws, args.js)

        elif args.command == "eval_async":
            return await eval_js(ws, args.js, await_promise=True)

        elif args.command == "stdin":
            for line in sys.stdin:
                line = line.rstrip("\n")
                if not line:
                    continue
                if line.startswith("text:"):
                    await type_text(ws, line[5:])
                elif line == "enter":
                    await dispatch_key(ws, "Enter", "Enter", 13)
                elif line.startswith("escape"):
                    count = 1
                    if len(line) > 6:
                        try:
                            count = int(line[6:])
                        except ValueError:
                            pass
                    for _ in range(count):
                        await dispatch_key(ws, "Escape", "Escape", 27)
                elif line == "tab":
                    await dispatch_key(ws, "Tab", "Tab", 9)
                elif line == "backspace":
                    await dispatch_key(ws, "Backspace", "Backspace", 8, ctrl=True)
                elif line == "delete_word":
                    await dispatch_key(ws, "Backspace", "Backspace", 8, ctrl=True)
                elif line == "clear_line":
                    await eval_js(ws, """
                        (() => {
                            const el = document.querySelector('[data-component="prompt-input"]');
                            if (!el) return "NO_EL";
                            el.focus();
                            el.textContent = '';
                            el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContent' }));
                            return "OK";
                        })()
                    """)
                print(f"CDP: {line[:50]}")
                sys.stdout.flush()
            return "stdin done"


async def main():
    parser = argparse.ArgumentParser(description="CDP bridge for opencode GUI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("text", help="Insert text into prompt input")
    p.add_argument("text")

    sub.add_parser("enter", help="Send Enter key")

    p = sub.add_parser("escape", help="Send Escape key(s)")
    p.add_argument("count", nargs="?", type=int, default=1)

    sub.add_parser("tab", help="Send Tab key")
    sub.add_parser("backspace", help="Send Backspace")
    sub.add_parser("delete_word", help="Send Ctrl+W (delete word backward)")
    sub.add_parser("clear_line", help="Send Ctrl+U (clear to start of line)")
    sub.add_parser("stdin", help="Read commands from stdin")
    sub.add_parser("agent_next", help="Cycle to next agent (Ctrl+.)")
    sub.add_parser("agent_prev", help="Cycle to previous agent (Shift+Ctrl+.)")

    p = sub.add_parser("eval", help="Evaluate JavaScript in the renderer")
    p.add_argument("js", help="JavaScript expression to evaluate")

    p = sub.add_parser("eval_async", help="Evaluate async JavaScript (awaits Promise)")
    p.add_argument("js", help="JavaScript expression returning a Promise")

    args = parser.parse_args()
    result = await run_command(args)
    if result:
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
