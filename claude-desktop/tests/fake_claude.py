#!/usr/bin/env python3
"""Nachbau von `claude --print --input-format stream-json …` für Tests ohne Konto.

Verhalten je nach Frage:
  enthält "posteingang" -> Paperless-Werkzeug (ohne Rückfrage) + Antwort
  enthält "lösch"       -> Rückfrage für Bash, Antwort je nach Entscheidung
  enthält "login"       -> Fehler wie ohne Anmeldung
  sonst                 -> gestreamte Textantwort
"""

import json
import sys
import time
import uuid

SID = str(uuid.uuid4())


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def stream(text):
    out({"type": "stream_event", "event": {"type": "message_start"}, "parent_tool_use_id": None, "session_id": SID})
    for word in text.split(" "):
        out({"type": "stream_event", "parent_tool_use_id": None, "session_id": SID,
             "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": word + " "}}})
        time.sleep(0.01)
    out({"type": "assistant", "parent_tool_use_id": None, "session_id": SID,
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})


def tool(name, inp, result="ok", error=False):
    tid = "toolu_" + uuid.uuid4().hex[:8]
    out({"type": "assistant", "parent_tool_use_id": None, "session_id": SID,
         "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
    out({"type": "user", "parent_tool_use_id": None, "session_id": SID,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": result,
                                                    "is_error": error}]}})


def result(text="", error=False):
    out({"type": "result", "subtype": "success", "is_error": error, "result": text, "session_id": SID})


def main():
    print(" ".join(sys.argv[1:]), file=open("/tmp/fake_claude_args.txt", "w"))
    lines = iter(sys.stdin)
    for line in lines:
        msg = json.loads(line)
        if msg["type"] == "control_request":
            out({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"],
                                                          "response": {}}})
            out({"type": "system", "subtype": "init", "session_id": SID, "model": "claude-opus-5-5"})
            continue
        if msg["type"] != "user":
            continue
        q = msg["message"]["content"].lower()
        if "login" in q:
            out({"type": "assistant", "session_id": SID, "parent_tool_use_id": None, "message": {"content": [
                {"type": "text", "text": "Not logged in · Please run /login"}]}})
            result("Not logged in · Please run /login", True)
        elif "posteingang" in q:
            stream("Ich schaue in deinen Paperless-Posteingang.")
            tool("mcp__paperless__posteingang", {}, "[...]")
            stream("Im Posteingang liegen **2 Dokumente**:\n\n1. *Bestellung USB-Kabel* – Amazon, 20.11.2026\n"
                   "2. *Lieferschein USB-Kabel* – Amazon, 21.11.2026\n\nSoll ich beide zusammenführen und als erledigt markieren?")
            result()
        elif "lösch" in q:
            stream("Ich räume den Download-Ordner auf.")
            tool("Bash", {"command": "ls ~/Downloads/*.tmp"}, "a.tmp b.tmp")
            rid = "req_perm1"
            out({"type": "control_request", "request_id": rid, "request": {
                "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "rm ~/Downloads/*.tmp"},
                "description": "Löscht 2 temporäre Dateien", "permission_suggestions": [
                    {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "rm ~/Downloads/*.tmp"}],
                     "behavior": "allow", "destination": "session"}]}})
            answer = json.loads(next(lines))
            print(json.dumps(answer), file=open("/tmp/fake_claude_answer.txt", "w"))
            allowed = answer["response"]["response"]["behavior"] == "allow"
            if allowed:
                tool("Bash", {"command": "rm ~/Downloads/*.tmp"}, "")
                stream("Erledigt – 2 Dateien gelöscht.")
            else:
                stream("Alles klar, ich lasse die Dateien liegen.")
            result()
        else:
            stream("Gern! Das ist eine **Testantwort** aus der Attrappe.")
            result()


main()
