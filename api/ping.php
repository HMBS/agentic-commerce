<?php
/**
 * GET /api/ping.php — proves PHP is executing on this host and the catalog is reachable.
 * If this returns HTML instead of JSON, the plan has no PHP and the API must live elsewhere.
 */

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: no-store');

$catalogPath = __DIR__ . '/../products.json';
$catalog = is_readable($catalogPath)
    ? json_decode((string) file_get_contents($catalogPath), true)
    : null;

http_response_code($catalog ? 200 : 500);
echo json_encode([
    'ok' => (bool) $catalog,
    'php' => PHP_VERSION,
    'catalog_readable' => is_readable($catalogPath),
    'products' => is_array($catalog) ? count($catalog['products'] ?? []) : 0,
    'checkout' => 'POST api/checkout.php',
    'note' => 'Diagnostic endpoint for the static deployment. Delete it once checkout works.',
], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
