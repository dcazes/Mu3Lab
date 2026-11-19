<?php
// Give each new Wallos account the household's currency instead of the euros Wallos picks.
// argv[1]: the currency code; argv[2]: the highest account id already handled.
// An account is changed once, only while it still uses euros and has no subscriptions,
// so a currency someone chose themselves is never overwritten.
if (PHP_SAPI !== 'cli') {
    exit(1);
}
set_exception_handler(function (Throwable $error) {
    echo 'MU3LAB_ERROR ' . str_replace(["\r", "\n"], ' ', $error->getMessage()) . "\n";
    exit(1);
});
$code = strtoupper(trim($argv[1] ?? ''));
$after = (int) ($argv[2] ?? 0);
if (!preg_match('/^[A-Z]{3}$/', $code)) {
    throw new RuntimeException('A three-letter currency code is required');
}
$db = new SQLite3('/var/www/html/db/wallos.db');
$db->busyTimeout(5000);
$seen = $after;
$users = $db->prepare('SELECT id, main_currency FROM user WHERE id > :after ORDER BY id');
$users->bindValue(':after', $after, SQLITE3_INTEGER);
$rows = $users->execute();
$changed = 0;
while ($user = $rows->fetchArray(SQLITE3_ASSOC)) {
    $id = (int) $user['id'];
    $seen = max($seen, $id);
    if ($id === 1 || $code === 'EUR') {
        continue; // Mu3Lab registered the administrator with the household's currency already.
    }
    $current = $db->prepare('SELECT code FROM currencies WHERE id = :id AND user_id = :user');
    $current->bindValue(':id', (int) $user['main_currency'], SQLITE3_INTEGER);
    $current->bindValue(':user', $id, SQLITE3_INTEGER);
    $currentCode = $current->execute()->fetchArray(SQLITE3_ASSOC)['code'] ?? '';
    $count = $db->prepare('SELECT COUNT(*) AS n FROM subscriptions WHERE user_id = :user');
    $count->bindValue(':user', $id, SQLITE3_INTEGER);
    $subscriptions = (int) ($count->execute()->fetchArray(SQLITE3_ASSOC)['n'] ?? 0);
    if ($currentCode !== 'EUR' || $subscriptions > 0) {
        continue;
    }
    $wanted = $db->prepare('SELECT id FROM currencies WHERE code = :code AND user_id = :user');
    $wanted->bindValue(':code', $code, SQLITE3_TEXT);
    $wanted->bindValue(':user', $id, SQLITE3_INTEGER);
    $currency = $wanted->execute()->fetchArray(SQLITE3_ASSOC);
    if (!$currency) {
        continue; // Not one of the currencies Wallos gives every account.
    }
    $update = $db->prepare('UPDATE user SET main_currency = :currency WHERE id = :user');
    $update->bindValue(':currency', (int) $currency['id'], SQLITE3_INTEGER);
    $update->bindValue(':user', $id, SQLITE3_INTEGER);
    $update->execute();
    $changed++;
}
echo "MU3LAB_OUTPUT MU3LAB_WALLOS_CURRENCY_SEEN=$seen\n";
echo "MU3LAB_WALLOS_CURRENCY_OK changed=$changed\n";
