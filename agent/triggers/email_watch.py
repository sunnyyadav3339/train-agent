"""Email trigger: start a run when a new mail matches a rule ("when an email arrives").

Rules live in data/triggers.yaml (git-ignored: it can hold addresses):

    email:
      - name: forward-to-agent
        from: "me@gmail.com"          # substring of the From header (optional)
        subject: "agent:"             # substring of the subject (optional)
        task: "{body}"                # what to run; {from} {subject} {body} are filled in

The inbox is opened read-only (nothing is marked as read or changed). Only mail that
arrives after the watcher first starts can trigger; the last UID seen is kept in
data/email_state.json so a restart does not replay old mail.
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import re
from email.header import decode_header, make_header
from pathlib import Path

import yaml

from ..skills import render


def load_rules(path: Path) -> list[dict]:
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [r for r in (doc.get("email") or []) if isinstance(r, dict) and r.get("task")]


def _header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _body_text(msg: email.message.Message, limit: int = 4000) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    html = ""
    for part in parts:
        ctype = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain":
            return text.strip()[:limit]
        if ctype == "text/html" and not html:
            html = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html).strip()[:limit]


def matches(rule: dict, sender: str, subject: str) -> bool:
    if rule.get("from") and str(rule["from"]).lower() not in sender.lower():
        return False
    if rule.get("subject") and str(rule["subject"]).lower() not in subject.lower():
        return False
    return True


class EmailWatcher:
    def __init__(self, rules_path: Path, state_path: Path, host: str, user: str, password: str):
        self.rules_path = Path(rules_path)
        self.state_path = Path(state_path)
        self.host, self.user, self.password = host, user, password
        self.last_uid: int | None = None
        if self.state_path.exists():
            try:
                self.last_uid = json.loads(self.state_path.read_text(encoding="utf-8")).get("last_uid")
            except ValueError:
                pass

    @classmethod
    def from_env(cls, data_dir: Path) -> "EmailWatcher | None":
        host = os.getenv("IMAP_HOST", "imap.gmail.com").strip()
        user = os.getenv("IMAP_USER", "").strip()
        pw = os.getenv("IMAP_APP_PASSWORD", "").strip().replace(" ", "")
        if not (user and pw):
            return None
        return cls(data_dir / "triggers.yaml", data_dir / "email_state.json", host, user, pw)

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"last_uid": self.last_uid}), encoding="utf-8")

    def poll(self) -> list[tuple[str, dict]]:
        """Check for new mail. Returns [(task, info)] for mails that match a rule."""
        rules = load_rules(self.rules_path)
        out: list[tuple[str, dict]] = []
        with imaplib.IMAP4_SSL(self.host, timeout=30) as imap:
            imap.login(self.user, self.password)
            imap.select("INBOX", readonly=True)
            if self.last_uid is None:
                typ, data = imap.uid("search", None, "ALL")
                uids = [int(u) for u in (data[0] or b"").split()]
                self.last_uid = max(uids) if uids else 0  # start from now: old mail never triggers
                self._save()
                return out
            typ, data = imap.uid("search", None, f"UID {self.last_uid + 1}:*")
            uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > self.last_uid)
            for uid in uids:
                typ, msg_data = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
                raw = next((p[1] for p in msg_data if isinstance(p, tuple)), b"")
                msg = email.message_from_bytes(raw)
                sender, subject = _header(msg.get("From")), _header(msg.get("Subject"))
                self.last_uid = uid
                for rule in rules:
                    if matches(rule, sender, subject):
                        info = {"from": sender, "subject": subject, "body": _body_text(msg), "rule": rule.get("name")}
                        out.append((render(str(rule["task"]), info), info))
                        break
            self._save()
        return out
