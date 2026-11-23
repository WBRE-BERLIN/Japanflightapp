<?php
/** POST {subscription, prefs}: register or update a device for notifications. */
declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

require_method('POST');
require_app_token();
$in = json_body();
$sub = $in['subscription'] ?? null;
if (!is_array($sub) || empty($sub['endpoint']) || empty($sub['keys']['p256dh']) || empty($sub['keys']['auth'])) {
    fail('subscription missing');
}
$endpoint = (string)$sub['endpoint'];
if (!preg_match(cfg('allow_http_endpoints') ? '#^https?://#' : '#^https://#', $endpoint) || strlen($endpoint) > 1000) {
    fail('invalid endpoint');
}
$prefs = clean_prefs((array)($in['prefs'] ?? []));
$record = [
    'id' => substr(hash('sha256', $endpoint), 0, 16),
    'endpoint' => $endpoint,
    'keys' => ['p256dh' => (string)$sub['keys']['p256dh'], 'auth' => (string)$sub['keys']['auth']],
] + $prefs;

$result = storage()->withSubscriptions(static function (array &$subs) use ($record) {
    foreach ($subs as $i => $s) {
        if (($s['endpoint'] ?? '') === $record['endpoint']) {
            $subs[$i] = array_merge($s, $record, ['updated' => gmdate('c')]);
            return ['created' => false, 'id' => $record['id']];
        }
    }
    if (count($subs) >= 50) {
        return ['error' => 'too many devices registered'];
    }
    $subs[] = $record + ['created' => gmdate('c'), 'last_daily' => null, 'last_alert_price' => null, 'failures' => 0];
    return ['created' => true, 'id' => $record['id']];
});
if (isset($result['error'])) {
    fail($result['error'], 429);
}
json_out(['ok' => true] + $result + ['prefs' => $prefs]);
