Pushing a change to `triggers/quotes` starts the live-quotes workflow; pushing to `triggers/refresh` starts the full refresh.
A scheduled Claude Code routine writes a timestamp here at 9:45 AM, 12:30 PM and 4:30 PM ET on weekdays,
because GitHub's own cron schedule was skipping most runs for this repository.
