<?php
/** Turns data/latest.json into notification texts. */
declare(strict_types=1);

final class Digest
{
    public static function money(float $v): string
    {
        return '€' . number_format($v, 0, '.', ',');
    }

    public static function span(string $depart, string $ret): string
    {
        $d = new DateTimeImmutable($depart);
        $r = new DateTimeImmutable($ret);
        return $d->format('M') === $r->format('M')
            ? $d->format('j') . '–' . $r->format('j M')
            : $d->format('j M') . '–' . $r->format('j M');
    }

    private static function line(string $label, array $o, array $dests): string
    {
        $air = implode('/', array_slice($o['airlines'] ?? [], 0, 2)) ?: '?';
        $bag = ($o['bag'] ?? '') === 'included' ? '' : ' +bags';
        $dest = $dests[$o['dest']]['name'] ?? $o['dest'];
        return sprintf('%s %s%s · %s · %s · %s', $label, self::money((float)$o['price_total']), $bag,
            $air, self::span($o['depart'], $o['ret']), $dest);
    }

    /** Best offers (per notification preferences) as [label => compact offer]. */
    public static function picks(array $latest, array $windows): array
    {
        $out = [];
        $sum = $latest['summary'] ?? [];
        foreach (($latest['config']['windows'] ?? []) as $w) {
            if (in_array($w['id'], $windows, true) && !empty($sum['windows'][$w['id']]['best'])) {
                $out[self::shortLabel($w)] = $sum['windows'][$w['id']]['best'];
            }
        }
        if (in_array('wild', $windows, true) && !empty($sum['wildcards'][0])) {
            $out['Wildcard'] = $sum['wildcards'][0];
        }
        return $out;
    }

    private static function shortLabel(array $w): string
    {
        $l = (string)($w['label'] ?? $w['id']);
        $l = preg_split('/\s*[·|-]\s*/u', $l)[0];
        return mb_substr($l, 0, 14);
    }

    public static function best(array $picks): ?array
    {
        $best = null;
        foreach ($picks as $o) {
            if ($best === null || $o['eff_total'] < $best['eff_total']) {
                $best = $o;
            }
        }
        return $best;
    }

    public static function daily(array $latest, ?array $history, array $windows, string $appUrl): ?array
    {
        $picks = self::picks($latest, $windows);
        $best = self::best($picks);
        if ($best === null) {
            return [
                'title' => 'Japan flights: nothing within your limits yet',
                'body' => 'No offers match the default filters right now. Open the app to relax them.',
                'url' => $appUrl, 'tag' => 'daily',
            ];
        }
        $dests = $latest['config']['destinations'] ?? [];
        $lines = [];
        foreach ($picks as $label => $o) {
            $lines[] = self::line($label, $o, $dests);
        }
        $delta = self::deltaVsYesterday($history, $best['window']);
        $title = sprintf('✈ Japan for 2: from %s (%s pp)', self::money((float)$best['price_total']),
            self::money((float)$best['price_pp']));
        if ($delta !== null && abs($delta) >= 1) {
            $title .= $delta < 0 ? ' ↓' . self::money(-$delta) : ' ↑' . self::money($delta);
        }
        $age = self::ageHours($latest);
        if ($age !== null && $age > 36) {
            $lines[] = sprintf('⚠ Data is %d h old – check the scraper.', (int)$age);
        }
        return [
            'title' => $title,
            'body' => implode("\n", $lines),
            'url' => $appUrl . '?offer=' . rawurlencode((string)$best['id']),
            'tag' => 'daily',
        ];
    }

    public static function alert(array $latest, array $windows, float $threshold, string $appUrl): ?array
    {
        $best = self::best(self::picks($latest, $windows));
        if ($best === null || (float)$best['eff_total'] > $threshold) {
            return null;
        }
        $dests = $latest['config']['destinations'] ?? [];
        return [
            'title' => '🔥 Price alert: ' . self::money((float)$best['price_total']) . ' for 2',
            'body' => self::line('', $best, $dests) . "\nBelow your limit of " . self::money($threshold) . ' (incl. bag estimate).',
            'url' => $appUrl . '?offer=' . rawurlencode((string)$best['id']),
            'tag' => 'alert',
            'price' => (float)$best['eff_total'],
        ];
    }

    private static function deltaVsYesterday(?array $history, string $window): ?float
    {
        if (!$history || empty($history['daily'])) {
            return null;
        }
        $days = array_keys($history['daily']);
        sort($days);
        $today = end($days);
        $prev = prev($days);
        if ($prev === false) {
            return null;
        }
        $key = $window . '|*';
        $a = $history['daily'][$today][$key]['eff_total'] ?? null;
        $b = $history['daily'][$prev][$key]['eff_total'] ?? null;
        return ($a !== null && $b !== null) ? (float)$a - (float)$b : null;
    }

    public static function ageHours(array $latest): ?float
    {
        if (empty($latest['generated_at'])) {
            return null;
        }
        $t = strtotime((string)$latest['generated_at']);
        return $t ? (time() - $t) / 3600 : null;
    }
}
