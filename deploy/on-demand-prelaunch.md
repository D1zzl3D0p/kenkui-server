# On-demand prelaunch operations

The single-user, test-only deployment defaults to **no scheduled Modal background work**. `KENKUI_BACKGROUND_SCHEDULES` is a deployment-time switch: omitted or `0` means no periodic recovery or maintenance; `1` restores the existing one-minute recovery and daily idle-mode maintenance schedules. Other values fail deployment configuration.

Normal upload/inspection, job dispatch, synthesis, lease renewal, completion and email delivery remain on demand. All five Modal functions remain registered and callable; the backup login/public encryption recipient remain isolated to maintenance. No new service, credential, timer or replacement scheduler is introduced.

With no calls, scheduled background Modal CPU and request activity are zero. Database/storage quotas and user-triggered conversion costs remain separate; removing schedules alone does not guarantee the total hosting bill. Existing models/storage may still incur charges outside their included allowances.

## Tradeoffs while schedules are off

- An interrupted/preempted worker has no periodic recovery tick. A test job can remain nonterminal with reserved credits until the operator calls the existing `recover_jobs` function after its lease expires. Normal worker execution still renews its lease. Do not edit jobs/leases/credits directly.
- Retention, source cleanup, expired recovery-proof pruning and logical backups run only when the operator invokes `retain_objects`. Test artifacts/source files can therefore accumulate beyond their usual cleanup dates. The app's advertised download window is not a promise of permanent storage.
- This is a deliberate prelaunch cost choice, not production disaster-recovery acceptance. Enable periodic operations when their reliability/retention behavior is wanted.

## Apply and verify

1. Coordinate with the controller and confirm no active test job before replacing the worker deployment.
2. Deploy the combined reviewed server revision and unchanged core to the **existing** Modal app/internal environment. For the current promotion that remains app `kenkui-staging`, environment `staging`; public production authority is configured separately. Set `KENKUI_BACKGROUND_SCHEDULES=0` (or leave it absent). Preserve existing worker/mail/database/bridge maps and limits.
3. Read back both function definitions: `recover_jobs` and `retain_objects` must have no schedule. Check the app's schedule inventory, not just source text. Other on-demand functions remain present. Recovery's two-second idle window is now static source configuration; an old dynamic override is unnecessary after this deployment.
4. Observe that no periodic calls continue across at least two former minute ticks, then inspect Neon via control-plane metadata without SQL keep-alives. No new test book is needed just to establish absence of scheduled calls.
5. The controller must separately retire schedules on any old Modal app/environment still pointing at Render. Updating this app does not stop other apps. Drain and stop only specifically identified retired apps; do not guess IDs or stop unrelated workloads.

For manual recovery, use the existing Modal SDK with explicit app/environment, for example `modal.Function.from_name('kenkui-staging', 'recover_jobs', environment_name='staging').remote()`. That is a real invocation, so first confirm the intended test job and current lease. A maintenance invocation also performs cleanup before backup and needs its ordinary scope review.

To re-enable the former background behavior, set `KENKUI_BACKGROUND_SCHEDULES=1` in the deployment command and redeploy the same app. Changing a runtime secret alone does not change a registered schedule.
