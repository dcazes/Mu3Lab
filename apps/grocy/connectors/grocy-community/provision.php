<?php
// Prints the dedicated "Mu3Lab MCP" API key for Grocy's one prepared administrator.
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

$permission = $db->permission_hierarchy()->where('name', 'ADMIN')->fetch();
$ids = [];
foreach ($db->user_permissions()->where('permission_id', $permission->id)->fetchAll() as $row) {
    if ($db->users()->where('id', $row->user_id)->fetch() !== null) {
        $ids[$row->user_id] = true;
    }
}
if (count($ids) !== 1) {
    throw new RuntimeException('expected exactly one administrator');
}
$ownerId = array_key_first($ids);
$owner = $db->users()->where('id', $ownerId)->fetch();
if ($owner === null || ($owner->username === 'admin' && password_verify('admin', $owner->password))) {
    throw new RuntimeException('expected exactly one prepared administrator');
}
define('GROCY_USER_ID', $ownerId);
// Reuse the dedicated key if the runtime credential file needs recovery.
$key = $db->api_keys()->where('user_id', $ownerId)->where('description', 'Mu3Lab MCP')
    ->where('key_type', ApiKeyService::API_KEY_TYPE_DEFAULT)
    ->where('expires > ?', date('Y-m-d H:i:s'))->fetch();
$token = $key === null ? $keys->CreateApiKey(ApiKeyService::API_KEY_TYPE_DEFAULT, 'Mu3Lab MCP') : $key->api_key;
echo 'MU3LAB_OUTPUT api_key=' . $token . "\n";
