# Owner recovery runbook

For the owner, at the keyboard, when the control plane is
down and the agent is not available to fix it (spec 036:
the recovery drill is run by the owner, manually, with this
page as the only instruction).

**Time needed:** about 15 minutes. **You need:** access to
the gatekeeper host (mini-server) and this page. Nothing
here can make things worse: every step is read-only until
step 5, and step 5 is reversible until step 7.

## 1. Check what is actually down

```sh
ssh mini-server
systemctl status syncyard-daemon
```

If the daemon answers `status` over its socket
(`syncyard-cli --home /var/lib/syncyard status`), the
control plane is alive — stop here and report what you see.

## 2. Read the last state, do not fix anything yet

```sh
ls /var/lib/syncyard/queue | wc -l
tail -5 /var/lib/syncyard/ledger.jsonl
cat /var/lib/syncyard/PAUSED.json 2>/dev/null
```

Write down: the record count, the last ledger event, and
whether a pause record exists. This is the photograph.

## 3. Restart the daemon once

```sh
sudo systemctl restart syncyard-daemon
sleep 5
syncyard-cli --home /var/lib/syncyard status
```

On boot the daemon reconciles before accepting work: it
recovers acknowledged submits and interrupted dispatches
from the off-VM witness store, and names any record it
could not read. If status answers and the counts match
your photograph, you are done — report the restart.

## 4. If the state home is damaged

Restore from the fleet backup (restic) into a NEW
directory first, never over the live one:

```sh
restic -r <fleet-repo> restore latest --target /tmp/sy-restore
ls /tmp/sy-restore/var/lib/syncyard/queue | wc -l
```

Compare the count against your photograph. Only when the
restored tree is whole do you move it into place (step 5).

## 5. Swap in the restored state (reversible)

```sh
sudo systemctl stop syncyard-daemon
mv /var/lib/syncyard /var/lib/syncyard.damaged-<date>
mv /tmp/sy-restore/var/lib/syncyard /var/lib/syncyard
sudo systemctl start syncyard-daemon
```

## 6. Verify

`syncyard-cli --home /var/lib/syncyard status` must answer,
counts must match the photograph (or the restored count),
and `syncyard-cli ledger` must show a
`daemon_boot_reconciled` event with zero unrecorded
submits. The pause stays set until you lift it
(`syncyard-cli start`) — the fleet does not resume on its
own after a recovery.

## 7. Report

Send the Syncyard chat: the photograph (counts, last
ledger event), what you restored, and the boot event. Keep
the `syncyard.damaged-<date>` directory until the chat
confirms the recovery — deleting it is the only
irreversible step, and it is not yours to rush.
