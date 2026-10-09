# topdividendetfs-x-poster (private)

Posts scheduled TopDividendETFs posts to X as @TopDividendETFs. Text only, no images.

Posts are added by the private scheduler page (topdividendetfs.com/x-queue, locked behind a GitHub token
that can write to this repo). Every 30 minutes a cron-job.org timer starts the "Post to X" workflow,
which posts anything that is due and moves it to `posted/`. Anything X rejects lands in `failed/` with the reason.

The script checks which account the keys belong to before posting and stops if it isn't @TopDividendETFs.

Secrets (Settings > Secrets and variables > Actions): X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET

Weekly winners: every Friday at 4:00 PM Eastern the first run resets the topdividendetfs.com votes to 0 (the vote script saves the final totals to the "Weekly Winners" tab of the ETF_Votes sheet first) and posts this week's winner and top 10 with a link to topdividendetfs.com. See scripts/weekly_winners.py. Turn it off with the repo variable WEEKLY_WINNERS_ENABLED = false.
