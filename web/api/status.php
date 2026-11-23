<?php
/** GET: public push key + server info for the app. */
declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

$latest = load_data('latest.json');
try {
    $keys = vapid_keys();
    $push = ['vapid_public_key' => $keys['public']];
} catch (Throwable $e) {
    $push = ['vapid_public_key' => null, 'push_error' => $e->getMessage()];
}
$subs = storage()->withSubscriptions(static function (array &$s) { return count($s); });
json_out([
    'ok' => true,
    'server_time' => gmdate('c'),
    'php' => PHP_VERSION,
    'token_required' => (string)cfg('app_token') !== '',
    'cron_configured' => strlen((string)cfg('cron_key')) >= 16,
    'subscriptions' => $subs,
    'data_generated_at' => $latest['generated_at'] ?? null,
] + $push);
