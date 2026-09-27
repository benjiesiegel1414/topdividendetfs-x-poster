# topdividendetfs-x-poster (private)

Posts scheduled TopDividendETFs posts to X as @TopDividendETFs. Text only, no images.

Posts are added by the private scheduler page (topdividendetfs.com/x-queue, locked behind a GitHub token
that can write to this repo). Every 30 minutes a cron-job.org timer starts the "Post to X" workflow,
which posts anything that is due and moves it to `posted/`. Anything X rejects lands in `failed/` with the reason.

The script checks which account the keys belong to before posting and stops if it isn't @TopDividendETFs.

Secrets (Settings > Secrets and variables > Actions): X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
