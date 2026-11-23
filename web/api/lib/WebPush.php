<?php
/**
 * Minimal, dependency-free Web Push sender for shared hosting.
 *
 *  - VAPID authentication (RFC 8292, ES256 JWT)
 *  - Payload encryption "aes128gcm" (RFC 8291 / RFC 8188)
 *
 * Needs only ext-openssl and ext-curl (both standard on all-inkl).
 */
declare(strict_types=1);

final class WebPush
{
    private const P256_SPKI_PREFIX = '3059301306072a8648ce3d020106082a8648ce3d030107034200';
    private const P256_SEC1_PREFIX = '30770201010420';
    private const P256_SEC1_MIDDLE = 'a00a06082a8648ce3d030107a144034200';

    /** @var string raw 65 byte uncompressed public key */
    private $publicKey;
    /** @var \OpenSSLAsymmetricKey|resource */
    private $privateKey;
    /** @var string */
    private $subject;

    public function __construct(string $publicKeyB64u, string $privateKeyPem, string $subject)
    {
        $this->publicKey = self::b64uDecode($publicKeyB64u);
        $key = openssl_pkey_get_private($privateKeyPem);
        if ($key === false || strlen($this->publicKey) !== 65) {
            throw new RuntimeException('Invalid VAPID key pair');
        }
        $this->privateKey = $key;
        $this->subject = $subject;
    }

    /** Create a new VAPID key pair: ['public' => b64url, 'private_pem' => PEM]. */
    public static function generateVapidKeys(): array
    {
        $key = self::newEcKey();
        openssl_pkey_export($key, $pem);
        return ['public' => self::b64uEncode(self::publicPoint($key)), 'private_pem' => $pem];
    }

    /** Build a PEM private key from raw base64url keys (format of `npx web-push generate-vapid-keys`). */
    public static function pemFromRaw(string $publicB64u, string $privateB64u): string
    {
        $d = str_pad(self::b64uDecode($privateB64u), 32, "\0", STR_PAD_LEFT);
        $pub = self::b64uDecode($publicB64u);
        $der = hex2bin(self::P256_SEC1_PREFIX) . $d . hex2bin(self::P256_SEC1_MIDDLE) . $pub;
        return "-----BEGIN EC PRIVATE KEY-----\n" . chunk_split(base64_encode($der), 64, "\n")
            . "-----END EC PRIVATE KEY-----\n";
    }

    /**
     * Send one push message.
     * @param array $subscription ['endpoint' => ..., 'keys' => ['p256dh' => ..., 'auth' => ...]]
     * @return array ['status' => int, 'body' => string, 'gone' => bool]
     */
    public function send(array $subscription, string $payload, int $ttl = 86400, string $urgency = 'normal', ?string $topic = null): array
    {
        $endpoint = (string)($subscription['endpoint'] ?? '');
        $p256dh = self::b64uDecode((string)($subscription['keys']['p256dh'] ?? ''));
        $auth = self::b64uDecode((string)($subscription['keys']['auth'] ?? ''));
        if (!preg_match('#^https?://#', $endpoint) || strlen($p256dh) !== 65 || strlen($auth) < 16) {
            return ['status' => 0, 'body' => 'invalid subscription', 'gone' => true];
        }
        $body = $this->encrypt($payload, $p256dh, $auth);
        $headers = [
            'Authorization: vapid t=' . $this->vapidJwt($endpoint) . ', k=' . self::b64uEncode($this->publicKey),
            'Content-Encoding: aes128gcm',
            'Content-Type: application/octet-stream',
            'TTL: ' . $ttl,
            'Urgency: ' . $urgency,
        ];
        if ($topic !== null) {
            $headers[] = 'Topic: ' . substr(preg_replace('/[^A-Za-z0-9_-]/', '', $topic), 0, 32);
        }
        $ch = curl_init($endpoint);
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $body,
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 20,
            CURLOPT_CONNECTTIMEOUT => 10,
        ]);
        $resp = curl_exec($ch);
        $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $err = curl_error($ch);
        curl_close($ch);
        return [
            'status' => $status,
            'body' => $resp === false ? $err : substr((string)$resp, 0, 500),
            'gone' => in_array($status, [404, 410], true),
        ];
    }

    /** RFC 8291 encryption, single record, returns the full aes128gcm body. */
    public function encrypt(string $payload, string $uaPublic, string $authSecret, ?string $salt = null): string
    {
        $salt = $salt ?? random_bytes(16);
        $asKey = self::newEcKey();
        $asPublic = self::publicPoint($asKey);
        $uaKey = openssl_pkey_get_public(self::spkiPem($uaPublic));
        if ($uaKey === false) {
            throw new RuntimeException('Invalid subscriber public key');
        }
        $ecdhSecret = openssl_pkey_derive($uaKey, $asKey, 32);
        if ($ecdhSecret === false) {
            throw new RuntimeException('ECDH failed');
        }
        $prkKey = hash_hmac('sha256', $ecdhSecret, $authSecret, true);
        $keyInfo = "WebPush: info\0" . $uaPublic . $asPublic;
        $ikm = hash_hmac('sha256', $keyInfo . "\x01", $prkKey, true);
        $prk = hash_hmac('sha256', $ikm, $salt, true);
        $cek = substr(hash_hmac('sha256', "Content-Encoding: aes128gcm\0\x01", $prk, true), 0, 16);
        $nonce = substr(hash_hmac('sha256', "Content-Encoding: nonce\0\x01", $prk, true), 0, 12);

        $tag = '';
        $cipher = openssl_encrypt($payload . "\x02", 'aes-128-gcm', $cek, OPENSSL_RAW_DATA, $nonce, $tag, '', 16);
        if ($cipher === false) {
            throw new RuntimeException('AES-GCM encryption failed');
        }
        $header = $salt . pack('N', 4096) . chr(strlen($asPublic)) . $asPublic;
        return $header . $cipher . $tag;
    }

    public function vapidJwt(string $endpoint): string
    {
        $parts = parse_url($endpoint);
        $aud = $parts['scheme'] . '://' . $parts['host'] . (isset($parts['port']) ? ':' . $parts['port'] : '');
        $header = self::b64uEncode(json_encode(['typ' => 'JWT', 'alg' => 'ES256']));
        $claims = self::b64uEncode(json_encode(['aud' => $aud, 'exp' => time() + 12 * 3600, 'sub' => $this->subject]));
        $input = $header . '.' . $claims;
        if (!openssl_sign($input, $der, $this->privateKey, OPENSSL_ALGO_SHA256)) {
            throw new RuntimeException('VAPID signing failed');
        }
        return $input . '.' . self::b64uEncode(self::derToRaw($der));
    }

    // ── helpers ─────────────────────────────────────────────────────────────
    private static function newEcKey()
    {
        $key = openssl_pkey_new(['curve_name' => 'prime256v1', 'private_key_type' => OPENSSL_KEYTYPE_EC]);
        if ($key === false) {
            throw new RuntimeException('Could not create EC key (OpenSSL)');
        }
        return $key;
    }

    private static function publicPoint($key): string
    {
        $d = openssl_pkey_get_details($key);
        return "\x04" . str_pad($d['ec']['x'], 32, "\0", STR_PAD_LEFT) . str_pad($d['ec']['y'], 32, "\0", STR_PAD_LEFT);
    }

    private static function spkiPem(string $point): string
    {
        $der = hex2bin(self::P256_SPKI_PREFIX) . $point;
        return "-----BEGIN PUBLIC KEY-----\n" . chunk_split(base64_encode($der), 64, "\n") . "-----END PUBLIC KEY-----\n";
    }

    /** ASN.1 DER ECDSA signature → 64 byte r||s. */
    private static function derToRaw(string $der): string
    {
        $pos = 2;
        if (ord($der[1]) & 0x80) {
            $pos += ord($der[1]) & 0x7f;
        }
        $out = '';
        for ($i = 0; $i < 2; $i++) {
            $pos++; // 0x02 INTEGER
            $len = ord($der[$pos++]);
            $int = substr($der, $pos, $len);
            $pos += $len;
            $int = ltrim($int, "\0");
            $out .= str_pad($int, 32, "\0", STR_PAD_LEFT);
        }
        return $out;
    }

    public static function b64uEncode(string $s): string
    {
        return rtrim(strtr(base64_encode($s), '+/', '-_'), '=');
    }

    public static function b64uDecode(string $s): string
    {
        $s = strtr($s, '-_', '+/');
        $d = base64_decode($s . str_repeat('=', (4 - strlen($s) % 4) % 4), true);
        return $d === false ? '' : $d;
    }
}
