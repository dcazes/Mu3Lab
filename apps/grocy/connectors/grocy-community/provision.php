<?php
// Prints the dedicated "Mu3Lab MCP" API key for the owner's Grocy administrator account.
// Uses Grocy's own token generator; never raw SQLite SQL.
use Grocy\Services\ApiKeyService;
use Grocy\Services\DatabaseMigrationService;
use Grocy\Services\DatabaseService;

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
$keys = ApiKeyService::GetInstance();

// Household members also become Grocy administrators, so the owner is found by
// the username Mu3Lab installed Grocy for, not by being the only administrator.
$username = $argv[1] ?? '';
if ($username === '') {
    throw new RuntimeException('the owner username is missing');
}
$owner = $db->users()->where('username', $username)->fetch();
if ($owner === null) {
    throw new RuntimeException('the owner has no Grocy account yet');
}
if ($owner->username === 'admin' && password_verify('admin', $owner->password)) {
    throw new RuntimeException('the owner still uses the default admin password');
}
$permission = $db->permission_hierarchy()->where('name', 'ADMIN')->fetch();
if ($db->user_permissions()->where('user_id', $owner->id)->where('permission_id', $permission->id)->fetch() === null) {
    throw new RuntimeException('the owner is not a Grocy administrator');
}
$ownerId = $owner->id;
define('GROCY_USER_ID', $ownerId);
// Reuse the dedicated key if the runtime credential file needs recovery.
$key = $db->api_keys()->where('user_id', $ownerId)->where('description', 'Mu3Lab MCP')
    ->where('key_type', ApiKeyService::API_KEY_TYPE_DEFAULT)
    ->where('expires > ?', date('Y-m-d H:i:s'))->fetch();
$token = $key === null ? $keys->CreateApiKey(ApiKeyService::API_KEY_TYPE_DEFAULT, 'Mu3Lab MCP') : $key->api_key;
echo 'MU3LAB_OUTPUT api_key=' . $token . "\n";
