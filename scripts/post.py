"""
TopDividendETFs X poster

Runs on GitHub Actions. Looks in queue/ for posts whose time has come and posts them to X
as @TopDividendETFs, then moves each one to posted/ (or failed/) and pushes the change right away,
so a post can never go out twice.

Queue files come from the private scheduler page:
  queue/<when>_<kind>_<variant>_<hash>_<id>.json   the post (text, post_at, ...)
Posts are text only ("images": 0). A .png next to a post would be attached if "images" is 1 or more.

Secrets needed in the repo (Settings > Secrets and variables > Actions):
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
Optional repo variable:
  X_EXPECTED_HANDLE   defaults to TopDividendETFs. The script refuses to post from any other account.
"""
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
from requests_oauthlib import OAuth1

API = "https://api.x.com/2"
UPLOAD_V2 = "https://api.x.com/2/media/upload"
UPLOAD_V1 = "https://upload.twitter.com/1.1/media/upload.json"
EXPECTED = (os.environ.get("X_EXPECTED_HANDLE") or "TopDividendETFs").lstrip("@").lower()
MAX_ATTEMPTS = 3
TOO_LATE = timedelta(hours=24)      # a post this late is moved to failed/ instead of going out
MAX_PER_RUN = 4                     # never burst more than this in one run
GAP_SECONDS = 20                    # pause between posts in the same run
EARLY = timedelta(minutes=3)        # posts due within this window go now (covers "Post now" and small clock differences)


def now():
    return datetime.now(timezone.utc)


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def auth():
    keys = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing repo secrets: {', '.join(missing)}")
    return OAuth1(*(os.environ[k] for k in keys))


class XError(Exception):
    def __init__(self, status, msg):
        super().__init__(f"X {status}: {msg}")
        self.status = status


def x_error(r):
    try:
        j = r.json()
        msg = j.get("detail") or j.get("title") or (j.get("errors") or [{}])[0].get("message") or r.text
    except Exception:
        msg = r.text
    return XError(r.status_code, str(msg)[:300])


def whoami(oa):
    r = requests.get(f"{API}/users/me", auth=oa, timeout=30)
    if not r.ok:
        raise x_error(r)
    return r.json()["data"]["username"]


def upload(oa, path):
    with open(path, "rb") as fh:
        data = fh.read()
    r = requests.post(UPLOAD_V2, auth=oa, timeout=90,
                      files={"media": (os.path.basename(path), data, "image/png")},
                      data={"media_category": "tweet_image"})
    if r.ok:
        j = r.json()
        return str((j.get("data") or {}).get("id") or j.get("media_id_string"))
    # Older apps sometimes only have the v1.1 upload
    r1 = requests.post(UPLOAD_V1, auth=oa, timeout=90, files={"media": data})
    if r1.ok:
        return r1.json()["media_id_string"]
    raise x_error(r)


def tweet(oa, text, media_ids):
    body = {"text": text}
    if media_ids:
        body["media"] = {"media_ids": media_ids}
    r = requests.post(f"{API}/tweets", auth=oa, json=body, timeout=60)
    if not r.ok:
        raise x_error(r)
    return r.json()["data"]["id"]


def images_for(json_path, count):
    base = json_path[:-5]
    out = []
    for i in range(count or 0):
        p = f"{base}.png" if i == 0 else f"{base}-{i + 1}.png"
        if os.path.exists(p):
            out.append(p)
    return out


def git(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def save_and_push(message):
    git("add", "-A")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return
    git("commit", "-m", message)
    for attempt in range(5):
        if git("push", check=False).returncode == 0:
            return
        git("pull", "--rebase", check=False)
        time.sleep(3 + attempt * 2)
    sys.exit("Could not push to GitHub after 5 tries")


def move(json_path, record, folder):
    """Write the updated record into posted/ or failed/ and remove the queue files."""
    name = os.path.basename(json_path)
    with open(os.path.join(folder, name), "w") as fh:
        json.dump(record, fh, indent=2)
    for p in [json_path] + glob.glob(json_path[:-5] + "*.png"):
        if os.path.exists(p):
            os.remove(p)



# ---------------------------------------------------------------------------
# Daily "top voted ETFs" post
# Every day at 1:00 PM Eastern this builds a post from the live vote totals on
# topdividendetfs.com/top-voted-etfs.html and drops it into queue/, then the
# normal flow below posts it. One per day: if one already exists for today in
# queue/, posted/ or failed/ nothing new is made. Turn it off by setting the
# repo variable TOPVOTED_ENABLED to "false".
# ---------------------------------------------------------------------------
TOPVOTED_ENABLED = (os.environ.get("TOPVOTED_ENABLED") or "true").lower() != "false"
TOPVOTED_HOUR_ET = 13          # 1:00 PM Eastern
TOPVOTED_LAST_HOUR_ET = 17     # if every run before 5 PM failed, skip that day
TOPVOTED_CSV = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vRYVQtSlWkwIDCeeB-YaQEpelovyW9ofaItXrzXZ_"
                "ntodK4QasRTKhP-swVWISmXIDZTIvQlbvNZm_o/pub?output=csv")
TOPVOTED_VOTES = ("https://script.google.com/macros/s/AKfycbwiuyy8aUNB3tNKouJ18zxE8r8nuiCTk3lG9PCYeqEQndImu_"
                  "8915rNHudiayXtFNbi/exec?action=getAll")
TOPVOTED_LINK = "https://topdividendetfs.com"
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def top_voted(n=10):
    """Same join and ranking as the top-voted page: sheet tickers + live votes, most votes first."""
    import csv
    import io
    import re
    stamp = int(time.time())
    c = requests.get(f"{TOPVOTED_CSV}&t={stamp}", timeout=60)
    c.raise_for_status()
    v = requests.get(f"{TOPVOTED_VOTES}&t={stamp}", timeout=90)
    v.raise_for_status()
    votes = {str(k).strip().upper(): int(x) for k, x in v.json().items() if str(x).strip().lstrip("-").isdigit()}
    seen, rows = set(), []
    for i, row in enumerate(csv.reader(io.StringIO(c.content.decode("utf-8-sig")))):
        if i == 0 or len(row) < 6:
            continue
        sym, name = row[0].strip().upper(), row[1].strip()
        if not sym or not name or not re.fullmatch(r"[A-Z][A-Z.\-]{0,5}", sym) or sym in seen:
            continue
        seen.add(sym)
        rows.append((sym, votes.get(sym, 0)))
    rows = [r for r in rows if r[1] > 0]
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows[:n]


def top_voted_text(rows):
    lines = []
    for i, (sym, n) in enumerate(rows, 1):
        lines.append(f"{MEDALS.get(i, f'#{i}')} {sym} {n} vote{'' if n == 1 else 's'}")
    return ("This week’s top voted income ETFs 🏆\n\n" + "\n".join(lines) +
            "\n\nGO VOTE 🗳️")


def queue_top_voted():
    if not TOPVOTED_ENABLED:
        return
    from zoneinfo import ZoneInfo
    et = now().astimezone(ZoneInfo("America/New_York"))
    if not (TOPVOTED_HOUR_ET <= et.hour < TOPVOTED_LAST_HOUR_ET) and not (
            et.hour == TOPVOTED_HOUR_ET - 1 and et.minute >= 57):
        return
    day = et.strftime("%Y%m%d")
    if any(glob.glob(f"{d}/*_topvoted_daily_{day}_*.json") for d in ("queue", "posted", "failed")):
        return
    try:
        rows = top_voted()
    except Exception as e:
        print(f"Top voted: could not read the vote totals, will try next run: {e}")
        return
    if len(rows) < 5:
        print(f"Top voted: only {len(rows)} ETFs have votes right now, will try next run")
        return
    due = et.replace(hour=TOPVOTED_HOUR_ET, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    rec = {
        "id": f"tv{day}",
        "kind": "topvoted",
        "variant": "daily",
        "syms": [s for s, _ in rows],
        "images": 0,
        "text": top_voted_text(rows),
        "post_at": due.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "created_at": now().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "auto": True,
    }
    path = f"queue/{due.strftime('%Y%m%dT%H%M')}_topvoted_daily_{day}_tv{day}.json"
    with open(path, "w") as fh:
        json.dump(rec, fh, indent=2, ensure_ascii=False)
    save_and_push(f"Queued daily top voted post {day}")
    print(f"Top voted: queued {path}")


def main():
    os.makedirs("posted", exist_ok=True)
    os.makedirs("failed", exist_ok=True)
    queue_top_voted()
    try:
        from weekly_winners import queue_weekly_winners
        queue_weekly_winners(save_and_push, TOPVOTED_CSV, TOPVOTED_VOTES)
    except Exception as e:
        print(f"Weekly winners: skipped this run: {e}")
    due = []
    for path in sorted(glob.glob("queue/*.json")):
        try:
            with open(path) as fh:
                rec = json.load(fh)
            if parse_time(rec["post_at"]) <= now() + EARLY:
                due.append((path, rec))
        except Exception as e:
            print(f"Skipping unreadable {path}: {e}")

    if not due:
        print("Nothing due")
        return

    oa = auth()
    handle = whoami(oa)
    if handle.lower() != EXPECTED:
        sys.exit(f"These X keys belong to @{handle}, not @{EXPECTED}. Nothing was posted")
    print(f"Posting as @{handle}, {len(due)} due")

    posted = 0
    for path, rec in due:
        late = now() - parse_time(rec["post_at"])
        if late > TOO_LATE:
            rec["error"] = f"Missed its time by more than {int(TOO_LATE.total_seconds() // 3600)} hours, so it was not posted"
            rec["failed_at"] = now().isoformat()
            move(path, rec, "failed")
            save_and_push(f"Missed: {os.path.basename(path)}")
            continue
        if posted >= MAX_PER_RUN:
            print("Hit the per-run limit, the rest go next run")
            break
        if posted:
            time.sleep(GAP_SECONDS)
        try:
            media = [upload(oa, p) for p in images_for(path, rec.get("images", 0))]
            tid = tweet(oa, rec["text"], media)
        except XError as e:
            if e.status == 429:
                print(f"Rate limited by X, will try next run: {e}")
                break
            rec["attempts"] = rec.get("attempts", 0) + 1
            rec["error"] = str(e)
            final = e.status in (400, 401, 403) or rec["attempts"] >= MAX_ATTEMPTS
            if final:
                rec["failed_at"] = now().isoformat()
                move(path, rec, "failed")
                save_and_push(f"Failed: {os.path.basename(path)}")
            else:
                with open(path, "w") as fh:
                    json.dump(rec, fh, indent=2)
                save_and_push(f"Retry later: {os.path.basename(path)}")
            print(f"{'Failed' if final else 'Will retry'} {path}: {e}")
            continue
        except requests.RequestException as e:
            print(f"Network problem, will try next run: {e}")
            break
        rec.pop("error", None)
        rec["tweet_id"] = tid
        rec["url"] = f"https://x.com/{handle}/status/{tid}"
        rec["posted_at"] = now().isoformat()
        move(path, rec, "posted")
        save_and_push(f"Posted {rec.get('kind', '')} {os.path.basename(path)[:13]}")
        posted += 1
        print(f"Posted {rec['url']}")


def check():
    """Connection test: confirms the keys work and belong to the right account. Posts nothing."""
    handle = whoami(auth())
    if handle.lower() != EXPECTED:
        sys.exit(f"Keys work, but they belong to @{handle}, not @{EXPECTED}")
    print(f"Connection OK. Keys belong to @{handle}")


if __name__ == "__main__":
    check() if "--check" in sys.argv else main()
