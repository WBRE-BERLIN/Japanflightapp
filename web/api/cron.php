<?php
/**
 * Notification scheduler. Call every 15 minutes:
 *   https://YOUR-SITE/api/cron.php?key=CRON_KEY
 * After each scrape the GitHub workflow calls it with &event=scrape so price
 * alerts go out right away.
 *
 * Optional: &force=daily sends the daily digest to everybody now (testing).
 */
declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

require_cron_key();
@set_time_limit(120);

if (!cfg('notifications')) {
    json_out(['ok' => true, 'note' => 'notifications are turned off (config: notifications)', 'sent' => 0]);
}

$event = (string)($_GET['event'] ?? 'tick');
$force = (string)($_GET['force'] ?? '');
$latest = load_data('latest.json');
if ($latest === null) {
    json_out(['ok' => true, 'note' => 'no data/latest.json yet', 'sent' => 0]);
}
$history = load_data('history.json');
$appUrl = base_url();
$push = pusher();
$log = [];

storage()->withSubscriptions(static function (array &$subs) use ($latest, $history, $appUrl, $push, $force, &$log) {
    foreach ($subs as $i => &$s) {
        $windows = $s['windows'] ?? ['may', 'wild'];
        try {
            $tz = new DateTimeZone($s['tz'] ?? (string)cfg('default_timezone'));
        } catch (Exception $e) {
            $tz = new DateTimeZone('Europe/Berlin');
        }
        $now = new DateTimeImmutable('now', $tz);
        $today = $now->format('Y-m-d');
        $messages = [];

        // 1) daily digest at the chosen local time
        $due = ($s['daily'] ?? true) && ($s['last_daily'] ?? null) !== $today && $now->format('H:i') >= ($s['notify_time'] ?? '08:00');
        if ($due || $force === 'daily') {
            $m = Digest::daily($latest, $history, $windows, $appUrl);
            if ($m) {
                $messages[] = ['daily', $m];
            }
        }

        // 2) price alert (once per new low below the threshold)
        if (!empty($s['alert_below'])) {
            $m = Digest::alert($latest, $windows, (float)$s['alert_below'], $appUrl);
            if ($m === null) {
                $s['last_alert_price'] = null;   // re-arm when the price goes back up
            } elseif (($s['last_alert_price'] ?? null) === null || $m['price'] < (float)$s['last_alert_price'] - 1) {
                $messages[] = ['alert', $m];
            }
        }

        foreach ($messages as [$kind, $m]) {
            $price = $m['price'] ?? null;
            unset($m['price']);
            $res = $push->send($s, json_encode($m, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
                $kind === 'alert' ? 6 * 3600 : 12 * 3600, $kind === 'alert' ? 'high' : 'normal', $kind);
            $ok = $res['status'] >= 200 && $res['status'] < 300;
            $log[] = ['id' => $s['id'] ?? '?', 'kind' => $kind, 'status' => $res['status']];
            if ($ok) {
                $s['failures'] = 0;
                if ($kind === 'daily') {
                    $s['last_daily'] = $today;
                } else {
                    $s['last_alert_price'] = $price;
                }
            } elseif ($res['gone']) {
                $s['_remove'] = true;
                break;
            } else {
                $s['failures'] = (int)($s['failures'] ?? 0) + 1;
                if ($kind === 'daily' && $s['failures'] >= 3) {
                    $s['last_daily'] = $today;   // give up for today
                }
            }
        }
    }
    unset($s);
    $subs = array_values(array_filter($subs, static function ($s) { return empty($s['_remove']); }));
});

foreach ($log as $l) {
    storage()->appendLog(['at' => gmdate('c'), 'event' => $event] + $l);
}
json_out(['ok' => true, 'event' => $event, 'sent' => count(array_filter($log, static function ($l) {
    return $l['status'] >= 200 && $l['status'] < 300;
})), 'attempts' => $log]);
