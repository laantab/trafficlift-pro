<?php
/**
 * Plugin Name: TrafficLift Website Report
 * Description: Shares recent daily Koko Analytics totals with TrafficLift.
 * Version: 1.0.0
 */
defined('ABSPATH') || exit;
add_action('rest_api_init', function () {
    register_rest_route('trafficlift/v1', '/totals', [
        'methods' => 'GET',
        'permission_callback' => function ($request) {
            return hash_equals('f4b11d7073b4270927748c952c7d7134666ae89d6d700efe00b7dcdab1a961c4', hash('sha256', (string) $request->get_header('X-TrafficLift-Key')));
        },
        'callback' => function () {
            if (!defined('KOKO_ANALYTICS_VERSION')) {
                return new WP_Error('inactive', 'Koko Analytics unavailable.', ['status' => 503]);
            }
            global $wpdb;
            $today = current_datetime();
            $start = $today->modify('-6 days')->format('Y-m-d');
            $rows = $wpdb->get_results($wpdb->prepare(
                "SELECT date, visitors, pageviews FROM {$wpdb->prefix}koko_analytics_site_stats WHERE date BETWEEN %s AND %s ORDER BY date",
                $start, $today->format('Y-m-d')
            ), ARRAY_A);
            if ($wpdb->last_error) {
                return new WP_Error('unavailable', 'Totals unavailable.', ['status' => 503]);
            }
            $response = rest_ensure_response(['days' => $rows]);
            $response->header('Cache-Control', 'private, no-store');
            return $response;
        }
    ]);
});
