<?php
/** POST {endpoint}: send the current daily digest to this device right now. */
declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

require_method('POST');
require_app_token();
$in = json_body();
$endpoint = (string)($in['endpoint'] ?? '');
$sub = storage()->withSubscriptions(static function (array &$subs) use ($endpoint) {
    foreach ($subs as $s) {
        if (($s['endpoint'] ?? '') === $endpoint) {
            return $s;
        }
    }
    return null;
});
if ($sub === null) {
    fail('device not registered', 404);
}
$latest = load_data('latest.json');
$msg = $latest
    ? Digest::daily($latest, load_data('history.json'), $sub['windows'] ?? ['may', 'wild'], base_url())
    : ['title' => 'Japan Flight Watch', 'body' => 'Notifications work! No flight data uploaded yet.', 'url' => base_url(), 'tag' => 'test'];
$msg['title'] = '[Test] ' . $msg['title'];
$res = pusher()->send($sub, json_encode($msg, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), 600, 'high');
storage()->appendLog(['at' => gmdate('c'), 'kind' => 'test', 'id' => $sub['id'], 'status' => $res['status']]);
$ok = $res['status'] >= 200 && $res['status'] < 300;
json_out(['ok' => $ok, 'push_status' => $res['status'], 'detail' => $ok ? null : $res['body']], $ok ? 200 : 502);
