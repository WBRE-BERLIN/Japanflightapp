<?php
/** Shared setup for all API endpoints. */
declare(strict_types=1);

require_once __DIR__ . '/lib/WebPush.php';
require_once __DIR__ . '/lib/Store.php';
require_once __DIR__ . '/lib/Digest.php';

$CONFIG = [
    // Secret for cron.php (all-inkl cron job / GitHub Actions). REQUIRED.
    'cron_key' => '',
    // Optional shared code the app must send to register for notifications.
    'app_token' => '',
    // Contact for push services ("mailto:you@example.com" or your site URL).
    'vapid_subject' => '',
    // Optional fixed VAPID keys (raw base64url as produced by `npx web-push generate-vapid-keys`).
    // Leave empty: a key pair is generated on first use and kept in api/storage/.
    'vapid_public' => '',
    'vapid_private' => '',
    'data_dir' => dirname(__DIR__) . '/data',
    'storage_dir' => __DIR__ . '/storage',
    'default_timezone' => 'Europe/Berlin',
];
$localFile = getenv('JFW_CONFIG') ?: __DIR__ . '/config.local.php';
if (is_file($localFile)) {
    $local = require $localFile;
    if (is_array($local)) {
        $CONFIG = array_merge($CONFIG, array_filter($local, static function ($v) { return $v !== null; }));
    }
}
date_default_timezone_set('UTC');

function cfg(string $key)
{
    global $CONFIG;
    return $CONFIG[$key] ?? null;
}

function json_out($data, int $status = 200): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}

function fail(string $msg, int $status = 400): void
{
    json_out(['ok' => false, 'error' => $msg], $status);
}

function require_method(string $method): void
{
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== $method) {
        fail('method not allowed', 405);
    }
}

function json_body(): array
{
    $raw = file_get_contents('php://input', false, null, 0, 64 * 1024);
    $data = json_decode((string)$raw, true);
    if (!is_array($data)) {
        fail('invalid JSON body');
    }
    return $data;
}

function require_app_token(): void
{
    $expected = (string)cfg('app_token');
    if ($expected === '') {
        return;
    }
    $given = (string)($_SERVER['HTTP_X_APP_TOKEN'] ?? '');
    if (!hash_equals($expected, $given)) {
        fail('wrong app code', 403);
    }
}

function require_cron_key(): void
{
    $expected = (string)cfg('cron_key');
    $given = (string)($_GET['key'] ?? $_SERVER['HTTP_X_CRON_KEY'] ?? '');
    if ($expected === '' || strlen($expected) < 16) {
        fail('cron_key is not configured (min. 16 characters)', 503);
    }
    if (!hash_equals($expected, $given)) {
        fail('forbidden', 403);
    }
}

function base_url(): string
{
    $https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
        || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https')
        || (($_SERVER['SERVER_PORT'] ?? '') === '443');
    $host = $_SERVER['HTTP_HOST'] ?? 'localhost';
    $path = rtrim(str_replace('\\', '/', dirname(dirname($_SERVER['SCRIPT_NAME'] ?? '/api/x.php'))), '/');
    return ($https ? 'https' : 'http') . '://' . $host . $path . '/';
}

function storage(): Store
{
    static $s = null;
    if ($s === null) {
        $s = new Store((string)cfg('storage_dir'));
    }
    return $s;
}

/** VAPID keys from config, or generated once and kept in storage. */
function vapid_keys(): array
{
    if (cfg('vapid_public') && cfg('vapid_private')) {
        return ['public' => cfg('vapid_public'), 'private_pem' => WebPush::pemFromRaw(cfg('vapid_public'), cfg('vapid_private'))];
    }
    return storage()->vapid(static function () { return WebPush::generateVapidKeys(); });
}

function pusher(): WebPush
{
    $k = vapid_keys();
    $subject = (string)cfg('vapid_subject');
    if ($subject === '') {
        $subject = rtrim(base_url(), '/');
        if (strpos($subject, 'https://') !== 0) {
            $subject = 'mailto:admin@example.com';
        }
    }
    return new WebPush($k['public'], $k['private_pem'], $subject);
}

function load_data(string $name): ?array
{
    $p = rtrim((string)cfg('data_dir'), '/') . '/' . $name;
    if (!is_file($p)) {
        return null;
    }
    $d = json_decode((string)file_get_contents($p), true);
    return is_array($d) ? $d : null;
}

function clean_prefs(array $p): array
{
    $time = preg_match('/^([01]\d|2[0-3]):[0-5]\d$/', (string)($p['notify_time'] ?? '')) ? $p['notify_time'] : '08:00';
    $tz = (string)($p['tz'] ?? cfg('default_timezone'));
    if (!in_array($tz, timezone_identifiers_list(), true)) {
        $tz = (string)cfg('default_timezone');
    }
    $windows = array_values(array_filter(array_map('strval', (array)($p['windows'] ?? ['may', 'oct', 'wild'])),
        static function ($w) { return preg_match('/^[a-z0-9_-]{1,20}$/', $w) === 1; }));
    $alert = isset($p['alert_below']) && is_numeric($p['alert_below']) && (float)$p['alert_below'] > 0
        ? (float)$p['alert_below'] : null;
    return [
        'daily' => !isset($p['daily']) || (bool)$p['daily'],
        'notify_time' => $time,
        'tz' => $tz,
        'windows' => $windows ?: ['may', 'oct', 'wild'],
        'alert_below' => $alert,
        'label' => substr(preg_replace('/[^\pL\pN ._-]/u', '', (string)($p['label'] ?? '')), 0, 40),
    ];
}
