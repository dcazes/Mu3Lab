<?php
// Make Grocy's shipped admin/admin account the owner's Authentik account (owner JSON in argv[1]).
// Uses Grocy's own migrations and user services; never raw SQLite SQL.
use Grocy\Services\ApiKeyService;
use Grocy\Services\DatabaseMigrationService;
use Grocy\Services\DatabaseService;
use Grocy\Services\UsersService;

if (PHP_SAPI !== 'cli') {
    exit(1);
}
set_exception_handler(function (Throwable $error) {
    echo 'MU3LAB_ERROR ' . str_replace(["\r", "\n"], ' ', $error->getMessage()) . "\n";
    exit(1);
});
define('GROCY_DATAPATH', '/config/data');
define('GROCY_IS_EMBEDDED_INSTALL', false);
require '/app/www/packages/autoload.php';
require GROCY_DATAPATH . '/config.php';
require '/app/www/config-dist.php';
DatabaseMigrationService::GetInstance()->MigrateDatabase();
$db = DatabaseService::GetInstance()->GetDbConnection();
$users = UsersService::GetInstance();
$keys = ApiKeyService::GetInstance();

$identity = json_decode($argv[1] ?? '', true, 512, JSON_THROW_ON_ERROR);
$username = trim($identity['username'] ?? '');
if ($username === '' || preg_match('/[\x00-\x1f\x7f]/', $username)) {
    throw new RuntimeException('A verified Authentik username is required');
}
$owner = $db->users()->where('username', $username)->fetch();
$seed = $db->users()->where('id', 1)->where('username', 'admin')->fetch();
$shippedDefault = $seed !== null && password_verify('admin', $seed->password);
// A customized admin is existing household data, not a shipped default.
if ($shippedDefault) {
    // No credential inherited from the shipped administrator should survive.
    foreach ($db->api_keys()->where('user_id', $seed->id)->fetchAll() as $key) {
        $keys->RemoveApiKey($key->api_key);
    }
    if ($owner === null || $owner->id == $seed->id) {
        $users->EditUser($seed->id, $username, '', '', bin2hex(random_bytes(32)));
        $owner = $db->users()->where('username', $username)->fetch();
    } else {
        $users->DeleteUser($seed->id);
    }
}
if ($owner === null) {
    $users->CreateUser($username, '', '', bin2hex(random_bytes(32)));
}
echo "MU3LAB_GROCY_OWNER_OK\n";
