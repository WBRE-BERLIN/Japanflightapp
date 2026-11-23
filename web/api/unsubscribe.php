<?php
/** POST {endpoint}: remove a device. */
declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

require_method('POST');
$in = json_body();
$endpoint = (string)($in['endpoint'] ?? '');
$removed = storage()->withSubscriptions(static function (array &$subs) use ($endpoint) {
    $n = count($subs);
    $subs = array_values(array_filter($subs, static function ($s) use ($endpoint) {
        return ($s['endpoint'] ?? '') !== $endpoint;
    }));
    return $n - count($subs);
});
json_out(['ok' => true, 'removed' => $removed]);
