---
name: Device test report
about: Report how kraken-lcd behaves on your Kraken model (works or not)
title: "[device] NZXT Kraken <model> on <distribution>"
labels: device-report
---

**Device and setup**

Paste the output of:

```
python3 -m kraken_lcd doctor
```

**What you did / what happened**

Tiles shown, screen flashes, refusals, anything odd. Relevant journal lines:

```
journalctl -u kraken-lcd -n 100 --no-pager
journalctl -u kraken-lcd | grep 'upload health'
```
