<?php
/** File based storage (JSON + flock) – no database needed. */
declare(strict_types=1);

final class Store
{
    /** @var string */
    private $dir;

    public function __construct(string $dir)
    {
        $this->dir = rtrim($dir, '/');
        if (!is_dir($this->dir) && !mkdir($this->dir, 0750, true) && !is_dir($this->dir)) {
            throw new RuntimeException('cannot create storage dir');
        }
        $ht = $this->dir . '/.htaccess';
        if (!is_file($ht)) {
            @file_put_contents($ht, "Require all denied\n<IfModule !mod_authz_core.c>\nDeny from all\n</IfModule>\n");
        }
    }

    /**
     * Run $fn with the decoded subscriptions under an exclusive lock.
     * $fn receives the array by reference and may modify it.
     */
    public function withSubscriptions(callable $fn)
    {
        $path = $this->dir . '/subscriptions.json';
        $fh = fopen($path, 'c+');
        if (!$fh) {
            throw new RuntimeException('cannot open subscriptions');
        }
        try {
            flock($fh, LOCK_EX);
            $raw = stream_get_contents($fh);
            $subs = json_decode($raw ?: '[]', true);
            if (!is_array($subs)) {
                $subs = [];
            }
            $before = json_encode($subs);
            $result = $fn($subs);
            $after = json_encode(array_values($subs));
            if ($after !== $before) {
                ftruncate($fh, 0);
                rewind($fh);
                fwrite($fh, json_encode(array_values($subs), JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
                fflush($fh);
            }
            return $result;
        } finally {
            flock($fh, LOCK_UN);
            fclose($fh);
        }
    }

    public function vapid(callable $generate): array
    {
        $path = $this->dir . '/vapid.json';
        $fh = fopen($path, 'c+');
        if (!$fh) {
            throw new RuntimeException('cannot open vapid.json');
        }
        try {
            flock($fh, LOCK_EX);
            $keys = json_decode(stream_get_contents($fh) ?: '', true);
            if (!is_array($keys) || empty($keys['public']) || empty($keys['private_pem'])) {
                $keys = $generate();
                ftruncate($fh, 0);
                rewind($fh);
                fwrite($fh, json_encode($keys, JSON_PRETTY_PRINT));
                fflush($fh);
            }
            return $keys;
        } finally {
            flock($fh, LOCK_UN);
            fclose($fh);
        }
    }

    public function appendLog(array $entry): void
    {
        $path = $this->dir . '/push-log.jsonl';
        if (is_file($path) && filesize($path) > 256 * 1024) {
            $lines = array_slice(file($path) ?: [], -400);
            file_put_contents($path, implode('', $lines), LOCK_EX);
        }
        file_put_contents($path, json_encode($entry, JSON_UNESCAPED_SLASHES) . "\n", FILE_APPEND | LOCK_EX);
    }
}
