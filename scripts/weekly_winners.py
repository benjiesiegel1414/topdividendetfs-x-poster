"""
Weekly "winners" post for @TopDividendETFs

Every Friday at 4:00 PM Eastern the community votes on topdividendetfs.com reset to zero.
On the first poster run at or after 4:00 PM on a Friday this:
  1. calls the vote script's weeklyReset action, which saves this week's final totals to the
     "Weekly Winners" tab of the votes sheet and then sets every vote back to 0
     (if the reset already happened, it just hands back the saved totals)
  2. builds a post announcing this week's winner and the top 10, says votes are reset,
     and links to topdividendetfs.com
  3. drops it into queue/ due right now, so the normal flow in post.py posts it in the same run

One per week: if a weekly post for today exists in queue/, posted/ or failed/ nothing new is made.
Turn it off by setting the repo variable WEEKLY_WINNERS_ENABLED to "false".
"""
import csv
import glob
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
RESET_HOUR_ET = 16                       # 4:00 PM Eastern, Fridays
FRIDAY = 4
LINK = "topdividendetfs.com"             # no period after it, ever
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
URL_RE = re.compile(r"(https?://\S+|\b[\w-]+(\.[\w-]+)*\.(com|net|org|io)\b\S*)", re.I)


def enabled():
    return (os.environ.get("WEEKLY_WINNERS_ENABLED") or "true").lower() != "false"


def x_length(text):
    """Roughly how X counts a post: links count as 23, emoji and other wide characters as 2."""
    text = URL_RE.sub("x" * 23, text)
    n = 0
    for ch in text:
        o = ord(ch)
        if 0xFE00 <= o <= 0xFE0F or o == 0x200D:      # emoji joiners and variation marks add nothing
            continue
        n += 1 if (o <= 4351 or 8192 <= o <= 8205 or 8208 <= o <= 8223 or 8242 <= o <= 8247) else 2
    return n


def rank(votes, csv_url, n=10):
    """Same join and order as the top voted page: tickers listed on the site, most votes first."""
    votes = {str(k).strip().upper(): int(v) for k, v in votes.items()
             if str(v).strip().lstrip("-").isdigit()}
    c = requests.get(f"{csv_url}&t={int(time.time())}", timeout=60)
    c.raise_for_status()
    seen, rows = set(), []
    for i, row in enumerate(csv.reader(io.StringIO(c.content.decode("utf-8-sig")))):
        if i == 0 or len(row) < 6:
            continue
        sym, name = row[0].strip().upper(), row[1].strip()
        if not sym or not name or not re.fullmatch(r"[A-Z][A-Z.\-]{0,5}", sym) or sym in seen:
            continue
        seen.add(sym)
        if votes.get(sym, 0) > 0:
            rows.append((sym, votes[sym]))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows[:n]


def post_text(rows):
    """X only allows one cashtag per post, so the winner gets the $ and the list stays plain."""
    count = len(rows)
    lines = [f"{MEDALS.get(i, f'#{i}')} {sym}" for i, (sym, _) in enumerate(rows, 1)]
    head = f"This week’s Top {count} voted ETFs are in 🏆"
    crown = f"Congrats to ${rows[0][0]} for taking the crown 👑"
    tail = f"Votes are now reset to 0 🔄\nGo vote for next week’s winner 👇\n{LINK}"
    text = f"{head}\n\n" + "\n".join(lines) + f"\n\n{crown}\n\n{tail}"
    if x_length(text) > 280:                      # drop the congrats line before anything else
        text = f"{head}\n\n" + "\n".join(lines) + f"\n\n{tail}"
    while x_length(text) > 280 and len(lines) > 3:
        lines.pop()
        text = f"{head}\n\n" + "\n".join(lines) + f"\n\n{tail}"
    return text


def queue_weekly_winners(save_and_push, csv_url, votes_url, now=None):
    if not enabled():
        return
    et = (now or datetime.now(timezone.utc)).astimezone(ET)
    if et.weekday() != FRIDAY or et.hour < RESET_HOUR_ET:
        return
    day = et.strftime("%Y%m%d")
    if any(glob.glob(f"{d}/*_topvoted_weekly_{day}_*.json") for d in ("queue", "posted", "failed")):
        return

    base = votes_url.split("?")[0]
    try:
        r = requests.get(f"{base}?action=weeklyReset&t={int(time.time())}", timeout=90)
        r.raise_for_status()
        snap = r.json()
    except Exception as e:
        print(f"Weekly winners: could not reach the vote script, will try next run: {e}")
        return
    if snap.get("status") not in ("reset", "already") or snap.get("week") != et.strftime("%Y-%m-%d"):
        print(f"Weekly winners: vote script says {snap.get('status')} for {snap.get('week')}, will try next run")
        return
    print(f"Weekly winners: votes {'reset just now' if snap['status'] == 'reset' else 'were already reset'} "
          f"for the week ending {snap['week']}")

    try:
        rows = rank(snap.get("votes") or {}, csv_url)
    except Exception as e:
        print(f"Weekly winners: could not read the ETF list, will try next run: {e}")
        return
    if not rows:
        print("Weekly winners: no ETF had any votes this week, nothing to post")
        return

    stamp = datetime.now(timezone.utc)
    rec = {
        "id": f"tw{day}",
        "kind": "topvoted",
        "variant": "weekly",
        "week": snap["week"],
        "syms": [s for s, _ in rows],
        "votes": {s: n for s, n in rows},
        "images": 0,
        "text": post_text(rows),
        "post_at": stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "created_at": stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "auto": True,
    }
    path = f"queue/{stamp.strftime('%Y%m%dT%H%M')}_topvoted_weekly_{day}_tw{day}.json"
    with open(path, "w") as fh:
        json.dump(rec, fh, indent=2, ensure_ascii=False)
    save_and_push(f"Queued weekly winners post {day}")
    print(f"Weekly winners: queued {path}")
