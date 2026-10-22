<?php
/**
 * POST /api/checkout — PHP twin of server.py's price_basket().
 *
 * Why this exists: the storefront is hosted on shared hosting (Hostinger/hcdn),
 * which serves static files only and cannot run server.py. This endpoint keeps the
 * age gate server-side instead of letting it collapse into a browser-only check.
 *
 * Response codes mirror the Python server exactly:
 *   200 priced session · 400 malformed body · 405 wrong method
 *   422 empty_basket / unknown_item / invalid_quantity / age_verification_required
 */

declare(strict_types=1);

const VAT_RATE = ['pantry' => 0.0, 'wine' => 0.23, 'kitchen' => 0.23];

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, PAYMENT-SIGNATURE, UCP-Agent');
header('Access-Control-Expose-Headers: PAYMENT-REQUIRED, PAYMENT-RESPONSE');
header('Cache-Control: no-store');

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') {
    http_response_code(204);
    exit;
}
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => ['code' => 'method_not_allowed', 'message' => 'Use POST']], JSON_PRETTY_PRINT);
    exit;
}

function out(int $status, array $payload): void
{
    http_response_code($status);
    echo json_encode(
        $payload,
        JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE
    );
    exit;
}

function vat_portion(int $gross, float $rate): int
{
    return $rate === 0.0 ? 0 : (int) round($gross * $rate / (1 + $rate));
}

$catalogPath = __DIR__ . '/../products.json';
if (!is_readable($catalogPath)) {
    out(500, ['error' => ['code' => 'catalog_missing',
        'message' => 'products.json is not readable next to api/']]);
}

$catalog = json_decode((string) file_get_contents($catalogPath), true);
if (!is_array($catalog) || !isset($catalog['products'])) {
    out(500, ['error' => ['code' => 'catalog_invalid', 'message' => 'products.json did not parse']]);
}

/* index products by id */
$products = [];
foreach ($catalog['products'] as $p) {
    $products[$p['id']] = $p;
}
$merchant = $catalog['merchant'];

$raw = file_get_contents('php://input') ?: '';
if (strlen($raw) > 64000) {
    out(400, ['error' => ['code' => 'invalid_json', 'message' => 'body too large']]);
}
$body = json_decode($raw, true);
if (!is_array($body) || array_is_list($body)) {
    out(400, ['error' => ['code' => 'invalid_json',
        'message' => 'body must be a JSON object']]);
}

$items = $body['items'] ?? null;
if (!is_array($items) || count($items) === 0 || !array_is_list($items)) {
    out(400, ['error' => ['code' => 'empty_basket',
        'message' => 'items must be a non-empty array']]);
}

$lines = [];
$subtotal = 0;
$taxTotal = 0;
$blocked = [];

foreach ($items as $item) {
    $id = is_array($item) ? ($item['id'] ?? null) : null;
    $qty = is_array($item) ? ($item['quantity'] ?? 1) : null;

    if (!is_string($id) || !isset($products[$id])) {
        out(422, ['error' => ['code' => 'unknown_item',
            'message' => 'No product with id ' . json_encode($id) . ' on this shelf']]);
    }
    if (!is_int($qty) || $qty < 1 || $qty > 99) {
        out(422, ['error' => ['code' => 'invalid_quantity',
            'message' => "quantity for $id must be an integer 1-99"]]);
    }

    $p = $products[$id];
    $rate = VAT_RATE[$p['category']] ?? 0.23;
    $gross = $p['price'] * $qty;
    $subtotal += $gross;
    $taxTotal += vat_portion($gross, $rate);

    $lines[] = [
        'id' => $p['id'],
        'sku' => $p['sku'],
        'name' => $p['name'],
        'quantity' => $qty,
        'unit_price' => $p['price'],
        'line_total' => $gross,
        'vat_rate' => $rate,
        'age_restriction' => $p['restriction'],
    ];
    if (!empty($p['restriction'])) {
        $blocked[] = ['id' => $p['id'], 'sku' => $p['sku'], 'restriction' => $p['restriction']];
    }
}

$ageVerified = ($body['age_verified'] ?? false) === true;

if (count($blocked) > 0 && !$ageVerified) {
    out(422, ['error' => [
        'code' => 'age_verification_required',
        'message' => 'Basket contains age-restricted lines. Assert age_verified=true after an age check, or remove them.',
        'retryable' => true,
        'recovery' => 'verify_age',
        'details' => ['blocked_items' => $blocked, 'policy' => $merchant['age_policy']],
    ]]);
}

out(200, ['checkout_session' => [
    'id' => 'ck_' . bin2hex(random_bytes(6)),
    'created_at' => gmdate('Y-m-d\TH:i:sP'),
    'status' => 'ready_for_payment',
    'currency' => $merchant['currency'],
    'merchant' => ['name' => $merchant['name'], 'legal_name' => $merchant['legal_name']],
    'lines' => $lines,
    'subtotal' => $subtotal,
    'tax_total' => $taxTotal,
    'total' => $subtotal,
    'price_note' => 'All amounts in EUR minor units. Shelf prices include VAT.',
    'age_assertion' => [
        'verified' => $ageVerified,
        'method' => $ageVerified ? 'self_declared' : 'none',
        'restricted_lines' => count($blocked),
        'note' => 'Test build: the assertion comes from the client. A production shop must use an age-verification provider and keep an audit trail.',
    ],
    'payment' => ['handlers' => ['card', 'x402'], 'x402_quote' => null],
    'agent' => $body['agent'] ?? null,
    'disclaimer' => 'Test shop. No payment is taken, no order is placed.',
]]);
