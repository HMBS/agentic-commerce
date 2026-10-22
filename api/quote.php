<?php
/**
 * GET /api/quote.php — the 402 quote for hosts that can't run server.py.
 * Mirrors server.py's /api/agent/quote: x402 v2 PaymentRequired body plus the
 * base64 PAYMENT-REQUIRED header. Wire shape only; no facilitator, no settlement.
 */

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, PAYMENT-SIGNATURE, UCP-Agent');
header('Access-Control-Expose-Headers: PAYMENT-REQUIRED, PAYMENT-RESPONSE');
header('Cache-Control: no-store');

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') {
    http_response_code(204);
    exit;
}

$host = $_SERVER['HTTP_HOST'] ?? 'store.leobo.me';
$scheme = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https' : 'http';
$base = $scheme . '://' . $host;

if (!empty($_SERVER['HTTP_PAYMENT_SIGNATURE'])) {
    http_response_code(501);
    echo json_encode(['error' => [
        'code' => 'settlement_not_implemented',
        'message' => 'This build publishes the 402 wire shape only. No facilitator is contacted and nothing is settled.',
    ]], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    exit;
}

$doc = [
    'x402Version' => 2,
    'error' => 'PAYMENT-SIGNATURE header is required',
    'resource' => [
        'url' => $base . '/api/quote.php',
        'description' => 'Checkout priority slot for one basket (test resource)',
        'mimeType' => 'application/json',
        'serviceName' => 'Fitzwilliam Provisions',
        'tags' => ['checkout', 'grocery', 'test'],
    ],
    'accepts' => [[
        'scheme' => 'exact',
        'network' => 'eip155:8453',
        'amount' => '1000000',
        'asset' => '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
        'payTo' => 'merchant',
        'maxTimeoutSeconds' => 60,
        'extra' => [
            'assetTransferMethod' => 'eip3009',
            'paymentFlow' => 'authorization',
            'name' => 'USDC',
            'version' => '2',
            'settlement' => 'not_implemented_in_this_build',
        ],
    ]],
];

$json = json_encode($doc, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
header('PAYMENT-REQUIRED: ' . base64_encode($json));
http_response_code(402);
echo $json;
